"""Build the incident-mcp server: read-only tools over the synthetic incident fixtures."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError
from mcp.server.mcpserver.resources import FunctionResource
from mcp.types import ToolAnnotations
from pydantic import Field

from incident_triage import __version__
from incident_triage.fixtures import FixtureStore, LogLevel, Runbook, Ticket, load_fixtures
from incident_triage.fixtures.models import (
    INCIDENT_ID_PATTERN,
    MAX_RUNBOOK_ID_CHARS,
    RUNBOOK_ID_PATTERN,
    TICKET_ID_PATTERN,
)
from incident_triage.server.guards import argument_names, reject_unknown_arguments
from incident_triage.server.lookup import (
    UnknownRunbookError,
    UnknownTicketError,
    get_runbook,
    get_ticket,
    incident_overview,
)
from incident_triage.server.observability import log_event, request_logging
from incident_triage.server.search import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MAX_QUERY_LENGTH,
    SearchLogsResult,
    UnknownIncidentError,
    search_logs,
)

SERVER_NAME = "incident-mcp"
INCIDENT_URI_TEMPLATE = "incident://{incident_id}"
JSON = "application/json"
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)

INSTRUCTIONS = (
    "Read-only access to synthetic incident data: logs, tickets and runbooks. "
    "Every record has a stable ID (INC-…, LOG-…, TKT-…, RB-…); cite those IDs as evidence."
)


def build_server(store: FixtureStore | None = None) -> MCPServer:
    """Create the server. Loads the packaged fixtures unless a store is given."""
    fixtures = store if store is not None else load_fixtures()
    allowed_arguments: dict[str, frozenset[str]] = {}  # filled as each tool is registered
    server = MCPServer(
        SERVER_NAME,
        version=__version__,
        instructions=INSTRUCTIONS,
        log_level="WARNING",  # the SDK's own stderr lines; ours are JSON via observability
        # Outermost first: logging sees (and logs) what the argument guard rejects.
        middleware=[request_logging, reject_unknown_arguments(allowed_arguments)],
    )

    @server.tool(name="search_logs", annotations=READ_ONLY)
    def search_logs_tool(  # pyright: ignore[reportUnusedFunction]
        incident_id: Annotated[
            str, Field(pattern=INCIDENT_ID_PATTERN, description="e.g. INC-0001")
        ],
        query: Annotated[
            str,
            Field(
                max_length=MAX_QUERY_LENGTH,
                description="Space-separated terms; all must appear (case-insensitive) in a "
                "record's message, service, level or attributes. Empty matches every record.",
            ),
        ] = "",
        level: Annotated[
            LogLevel | None, Field(description="Minimum level: WARN also returns ERROR and FATAL.")
        ] = None,
        service: Annotated[str | None, Field(description="Exact service name.")] = None,
        limit: Annotated[int, Field(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
        offset: Annotated[
            int, Field(ge=0, description="Pass next_offset to get the next page.")
        ] = 0,
    ) -> SearchLogsResult:
        """Search one incident's logs. Read-only and paginated, in timestamp order.

        No match is not an error: it returns total_matches 0. Each record carries its LOG- ID.
        """
        try:
            result = search_logs(
                fixtures,
                incident_id=incident_id,
                query=query,
                level=level,
                service=service,
                limit=limit,
                offset=offset,
            )
        except UnknownIncidentError as exc:
            log_event("tool_result", tool="search_logs", error="unknown_incident")
            raise ToolError(str(exc)) from None
        log_event(
            "tool_result",
            tool="search_logs",
            result_count=result.returned,
            total_matches=result.total_matches,
        )
        return result

    @server.tool(name="get_ticket", annotations=READ_ONLY)
    def get_ticket_tool(  # pyright: ignore[reportUnusedFunction]
        ticket_id: Annotated[str, Field(pattern=TICKET_ID_PATTERN, description="e.g. TKT-0001")],
    ) -> Ticket:
        """Get one ticket, with its body and comments. Read-only.

        An incident's ticket IDs are listed in its incident://<incident_id> resource.
        """
        try:
            ticket = get_ticket(fixtures, ticket_id)
        except UnknownTicketError as exc:
            log_event("tool_result", tool="get_ticket", error="unknown_ticket")
            raise ToolError(str(exc)) from None
        log_event("tool_result", tool="get_ticket", comment_count=len(ticket.comments))
        return ticket

    @server.tool(name="get_runbook", annotations=READ_ONLY)
    def get_runbook_tool(  # pyright: ignore[reportUnusedFunction]
        runbook_id: Annotated[
            str,
            Field(
                pattern=RUNBOOK_ID_PATTERN,
                max_length=MAX_RUNBOOK_ID_CHARS,
                description="e.g. RB-DB-POOL",
            ),
        ],
    ) -> Runbook:
        """Get one runbook: the symptoms it covers and its steps. Read-only.

        An incident's runbook IDs are listed in its incident://<incident_id> resource.
        """
        try:
            runbook = get_runbook(fixtures, runbook_id)
        except UnknownRunbookError as exc:
            log_event("tool_result", tool="get_runbook", error="unknown_runbook")
            raise ToolError(str(exc)) from None
        log_event("tool_result", tool="get_runbook", step_count=len(runbook.steps))
        return runbook

    allowed_arguments.update(
        search_logs=argument_names(search_logs_tool),
        get_ticket=argument_names(get_ticket_tool),
        get_runbook=argument_names(get_runbook_tool),
    )

    def read_overview(incident_id: str) -> str:
        if re.fullmatch(INCIDENT_ID_PATTERN, incident_id) is None:
            raise ResourceNotFoundError(
                f"malformed incident_id {_clip(incident_id)!r}; "
                "expected INC- and four digits, e.g. INC-0001"
            )
        try:
            overview = incident_overview(fixtures, incident_id)
        except UnknownIncidentError as exc:
            raise ResourceNotFoundError(str(exc)) from None
        return overview.model_dump_json()

    server.resource(
        INCIDENT_URI_TEMPLATE,
        name="incident",
        title="Incident overview",
        description="Where an investigation starts: the alert, severity, ticket and runbook "
        "IDs, and the incident's log count, time range and services.",
        mime_type=JSON,
    )(read_overview)

    # Concrete entries as well as the template, so hosts can list the incidents.
    for incident_id in sorted(fixtures.incidents):
        server.add_resource(
            FunctionResource.from_function(
                _bind(read_overview, incident_id),
                uri=INCIDENT_URI_TEMPLATE.format(incident_id=incident_id),
                name=incident_id,
                title=fixtures.incidents[incident_id].title,
                mime_type=JSON,
            )
        )

    return server


def _clip(value: str, limit: int = 40) -> str:
    """Inputs echoed back in errors are clipped, so an error stays within the response budget."""
    return value if len(value) <= limit else value[:limit] + "…"


def _bind(read: Callable[[str], str], incident_id: str) -> Callable[[], str]:
    def read_one() -> str:
        return read(incident_id)

    return read_one
