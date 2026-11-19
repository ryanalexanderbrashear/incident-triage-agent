"""`triage` command line. Prints one JSON run record to stdout; logs JSON lines to stderr.

Exit codes: 0 answered; 1 the run ended without a valid answer; 2 usage or configuration error.
Never prints the API key or any other environment value.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from typing import Any, cast, get_args

from anthropic import AsyncAnthropic
from anthropic.types.beta import BetaMessage
from mcp import Client, StdioServerParameters
from mcp.shared.exceptions import MCPError

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
    return parser


async def _run(args: argparse.Namespace, messages_api: MessagesAPI, server: object) -> int:
    record: RunRecord | None = None
    failure: str | None = None
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
            )
    except* MCPError as group:
        failure = _first_mcp_error(group).message
    if record is None:
        sys.stderr.write(f"triage: {failure}\n")
        return 2
    sys.stdout.write(record.model_dump_json(indent=2) + "\n")
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
) -> int:
    """`messages_api` and `server` are injectable for tests.

    By default: the real Messages API and incident-mcp spawned over stdio.
    """
    args = _parser().parse_args(argv)
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
        messages_api = AnthropicMessages(AsyncAnthropic())
    return asyncio.run(_run(args, messages_api, server if server is not None else _server()))


if __name__ == "__main__":
    sys.exit(main())
