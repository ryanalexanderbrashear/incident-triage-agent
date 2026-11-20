"""`triage` command line. Prints one JSON run record to stdout; logs JSON lines to stderr.

Exit codes: 0 answered; 1 the run ended without a valid answer; 2 usage or configuration error.
Never prints the API key or any other environment value.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
from collections.abc import Callable
from typing import Any, cast, get_args

from anthropic import AsyncAnthropic
from anthropic.types.beta import BetaMessage
from langfuse import Langfuse
from mcp import Client, StdioServerParameters
from mcp.shared.exceptions import MCPError

from incident_triage.agent.export import ExportError, ObservationsAPI, export_trace
from incident_triage.agent.loop import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    DEFAULT_TOOL_BUDGET,
    DEFAULT_TURN_LIMIT,
    Effort,
    MessagesAPI,
    RunRecord,
    log_event,
    run_triage,
)
from incident_triage.agent.tracing import git_sha, make_langfuse, trace_url
from incident_triage.fixtures.models import INCIDENT_ID_PATTERN

CREDENTIAL_VARIABLES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
NO_KEY_MESSAGE = (
    "triage: no Anthropic credentials. Put ANTHROPIC_API_KEY in .env and run "
    "`uv run --env-file .env triage run <incident_id>`."
)


class AnthropicMessages:
    """The real `client.beta.messages.create`, adapted to the loop's MessagesAPI protocol."""

    def __init__(self, client: AsyncAnthropic) -> None:
        self._client = client

    async def create(self, **kwargs: Any) -> BetaMessage:
        # create() is overloaded on `stream`; with **kwargs pyright can't pick an overload.
        response: object = await self._client.beta.messages.create(**kwargs)  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(response, BetaMessage):  # stream=True is never passed
            raise TypeError("expected a BetaMessage; the loop never requests a stream")
        return response


def make_anthropic(**options: Any) -> AsyncAnthropic:
    """The Anthropic client, with the SDK's own OpenTelemetry spans off.

    The loop records each call as a Langfuse generation; the SDK's span would sit under it with
    token counts that include cache reads, duplicating and contradicting it (ADR-002, Tracing).
    """
    return AsyncAnthropic(open_telemetry=False, **options)


def _server() -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=["-m", "incident_triage.server"])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="triage", description="Triage a synthetic incident.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run the agent on one incident.")
    run.add_argument("incident_id", help="e.g. INC-0001")
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--effort", choices=get_args(Effort), default=DEFAULT_EFFORT)
    run.add_argument("--tool-budget", type=int, default=DEFAULT_TOOL_BUDGET)
    run.add_argument("--turn-limit", type=int, default=DEFAULT_TURN_LIMIT)
    trace = commands.add_parser("trace", help="Work with Langfuse traces.")
    trace_commands = trace.add_subparsers(dest="trace_command", required=True)
    export = trace_commands.add_parser(
        "export", help="Export one trace as clean JSON (SDK metadata stripped, keys redacted)."
    )
    export.add_argument("trace_id", help="32 hex characters, as printed by `triage run`")
    export.add_argument("--out", help="File to write (default: stdout)")
    return parser


def _export(args: argparse.Namespace, observations_api: ObservationsAPI | None) -> int:
    if re.fullmatch(r"[0-9a-f]{32}", args.trace_id) is None:
        sys.stderr.write(f"triage: {args.trace_id!r} is not a trace ID (32 hex characters)\n")
        return 2
    client: Langfuse | None = None
    if observations_api is None:
        client = make_langfuse()
        if client is None:
            sys.stderr.write(
                "triage: exporting a trace needs LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and "
                "LANGFUSE_BASE_URL; run with `uv run --env-file .env triage trace export …`\n"
            )
            return 2
        observations_api = client.api.observations
    try:
        text = export_trace(observations_api, args.trace_id)
    except ExportError as exc:
        sys.stderr.write(f"triage: {exc}\n")
        return 1
    finally:
        if client is not None:
            client.shutdown()
    if args.out:
        with open(args.out, "w", encoding="utf-8") as out:
            out.write(text)
        log_event("trace_exported", trace_id=args.trace_id, path=args.out, chars=len(text))
    else:
        sys.stdout.write(text)
    return 0


async def _run(
    args: argparse.Namespace,
    messages_api: MessagesAPI,
    server: object,
    langfuse: Langfuse | None,
) -> int:
    record: RunRecord | None = None
    failure: str | None = None
    if langfuse is None:
        log_event(
            "tracing_disabled",
            level=logging.WARNING,
            reason="LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are not both set",
        )
    # The MCP client runs in a task group, so an error raised inside it can arrive wrapped in an
    # ExceptionGroup; except* matches it either way (and cannot itself contain a return).
    try:
        async with Client(server) as mcp:  # pyright: ignore[reportArgumentType]
            record = await run_triage(
                args.incident_id,
                mcp=mcp,
                messages_api=messages_api,
                model=args.model,
                effort=args.effort,
                tool_budget=args.tool_budget,
                turn_limit=args.turn_limit,
                langfuse=langfuse,
                git_sha=git_sha(),
            )
    except* MCPError as group:
        failure = _first_mcp_error(group).message
    finally:
        # A short-lived process: export the trace before exiting, whatever happened above.
        if langfuse is not None:
            langfuse.flush()
    if record is None:
        sys.stderr.write(f"triage: {failure}\n")
        return 2
    sys.stdout.write(record.model_dump_json(indent=2) + "\n")
    if langfuse is not None and record.trace_id is not None:
        url = trace_url(langfuse, record.trace_id, log=log_event)
        log_event("trace", trace_id=record.trace_id, url=url)
    log_event("run_finished", incident_id=args.incident_id, outcome=record.outcome)
    return 0 if record.outcome == "answered" else 1


def _first_mcp_error(error: BaseException) -> MCPError:
    """The innermost MCPError: task groups can nest the original several groups deep."""
    if isinstance(error, MCPError):
        return error
    if isinstance(error, BaseExceptionGroup):
        inners = cast(BaseExceptionGroup[BaseException], error).exceptions
        for inner in inners:
            try:
                return _first_mcp_error(inner)
            except LookupError:
                continue
    raise LookupError("no MCPError in the group")


def main(
    argv: list[str] | None = None,
    *,
    messages_api: MessagesAPI | None = None,
    server: object | None = None,
    langfuse_factory: Callable[[], Langfuse | None] = make_langfuse,
    observations_api: ObservationsAPI | None = None,
) -> int:
    """`messages_api`, `server`, `langfuse_factory` and `observations_api` are injectable for tests.

    By default: the real Messages API, incident-mcp spawned over stdio, and Langfuse from the
    environment (an untraced run if its keys are missing).
    """
    args = _parser().parse_args(argv)
    if args.command == "trace":
        return _export(args, observations_api)
    if re.fullmatch(INCIDENT_ID_PATTERN, args.incident_id) is None:
        sys.stderr.write(f"triage: {args.incident_id!r} is not an incident ID like INC-0001\n")
        return 2
    if args.tool_budget < 1 or args.turn_limit < 1:
        sys.stderr.write("triage: --tool-budget and --turn-limit must be at least 1\n")
        return 2
    if messages_api is None:
        if not any(os.environ.get(name) for name in CREDENTIAL_VARIABLES):
            sys.stderr.write(NO_KEY_MESSAGE + "\n")
            return 2
        messages_api = AnthropicMessages(make_anthropic())
    langfuse = langfuse_factory()
    try:
        return asyncio.run(
            _run(args, messages_api, server if server is not None else _server(), langfuse)
        )
    finally:
        if langfuse is not None:
            langfuse.shutdown()


if __name__ == "__main__":
    sys.exit(main())
