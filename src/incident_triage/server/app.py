"""Build the incident-mcp server: read-only tools over the synthetic incident fixtures."""

from __future__ import annotations

from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from incident_triage import __version__
from incident_triage.fixtures import FixtureStore, LogLevel, load_fixtures
from incident_triage.fixtures.models import INCIDENT_ID_PATTERN
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

INSTRUCTIONS = (
    "Read-only access to synthetic incident data: logs, tickets and runbooks. "
    "Every record has a stable ID (INC-…, LOG-…, TKT-…, RB-…); cite those IDs as evidence."
)


def build_server(store: FixtureStore | None = None) -> MCPServer:
    """Create the server. Loads the packaged fixtures unless a store is given."""
    fixtures = store if store is not None else load_fixtures()
    server = MCPServer(
        SERVER_NAME,
        version=__version__,
        instructions=INSTRUCTIONS,
        log_level="WARNING",  # the SDK's own stderr lines; ours are JSON via observability
        middleware=[request_logging],
    )

    @server.tool(
        name="search_logs",
        annotations=ToolAnnotations(
            read_only_hint=True, idempotent_hint=True, open_world_hint=False
        ),
    )
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

    return server
