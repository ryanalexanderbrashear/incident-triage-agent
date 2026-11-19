"""Request guards the SDK does not provide.

The SDK builds tool arguments without `extra="forbid"`, so an unknown argument is silently
dropped. For a misspelt optional filter (`"levl"` for `"level"`) that means wrong, unfiltered
results that look right. This middleware answers such a call with a tool error the model can
read, before the tool runs.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, cast

from mcp.server.context import ServerMiddleware
from mcp.types import CallToolResult, TextContent

from incident_triage.server.observability import log_event

AllowedArguments = Mapping[str, frozenset[str]]  # tool name -> its argument names

# The error echoes the caller's argument names back; clip them so the error stays small too.
_MAX_NAME_CHARS = 40
_MAX_NAMED = 10


def _clip(name: str) -> str:
    return name if len(name) <= _MAX_NAME_CHARS else name[:_MAX_NAME_CHARS] + "…"


def argument_names(fn: Callable[..., Any]) -> frozenset[str]:
    return frozenset(inspect.signature(fn).parameters)


def reject_unknown_arguments(allowed: AllowedArguments) -> ServerMiddleware[Any]:
    """Middleware: a tools/call naming an argument its tool does not take gets a tool error.

    `allowed` is read on each call, so it may be filled after the server is constructed.
    Unknown tools and non-object arguments are left to the SDK, which already rejects them.
    """

    async def guard(ctx: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
        if getattr(ctx, "method", None) != "tools/call":
            return await call_next(ctx)
        params: object = getattr(ctx, "params", None)
        if not isinstance(params, Mapping):
            return await call_next(ctx)
        request = cast(Mapping[str, Any], params)
        name: object = request.get("name")
        arguments: object = request.get("arguments")
        if not isinstance(name, str) or name not in allowed or not isinstance(arguments, Mapping):
            return await call_next(ctx)
        expected = allowed[name]
        unknown = sorted(
            _clip(str(key))
            for key in cast(Mapping[object, object], arguments)
            if key not in expected
        )
        if not unknown:
            return await call_next(ctx)
        if len(unknown) > _MAX_NAMED:
            unknown = [*unknown[:_MAX_NAMED], f"... {len(unknown) - _MAX_NAMED} more"]
        log_event("tool_result", tool=name, error="unknown_argument", arguments=unknown)
        message = (
            f"unknown argument{'s' if len(unknown) > 1 else ''} "
            f"{', '.join(repr(u) for u in unknown)} for {name}; "
            f"expected: {', '.join(sorted(expected))}"
        )
        result = CallToolResult(content=[TextContent(type="text", text=message)], is_error=True)
        return result.model_dump(by_alias=True, mode="json", exclude_none=True)

    return guard
