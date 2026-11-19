"""Structured JSON logging for incident-mcp, to stderr only.

Under stdio, stdout is the MCP protocol channel, so nothing here may ever write to it.
Every inbound request gets a fresh request ID (the JSON-RPC ID restarts per connection, so
it cannot identify a request across sessions). The ID lives in a ContextVar, so anything
logged while the request is handled carries it.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from typing import Any, Protocol, cast

from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, INVALID_REQUEST, METHOD_NOT_FOUND

from incident_triage.jsonlog import json_logger

LOGGER_NAME = "incident_mcp"

# Protocol errors that mean the caller asked wrongly (an unknown resource arrives as
# INVALID_PARAMS). They are logged as "rejected" at INFO; anything else that escapes a handler
# is a server fault, logged as "raised" at ERROR.
_CALLER_ERROR_CODES = frozenset({INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS})

request_id_var: ContextVar[str | None] = ContextVar("incident_mcp_request_id", default=None)


def _request_context() -> dict[str, Any]:
    request_id = request_id_var.get()
    return {} if request_id is None else {"request_id": request_id}


def get_logger() -> logging.Logger:
    return json_logger(LOGGER_NAME, context=_request_context)


def log_event(event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    get_logger().log(level, event, extra={"fields": fields})


class _RequestContext(Protocol):
    @property
    def method(self) -> str: ...
    @property
    def params(self) -> Any: ...
    @property
    def request_id(self) -> Any: ...


async def request_logging(ctx: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
    """MCP server middleware: one JSON line per inbound request, with a fresh request ID.

    Runs before the SDK validates arguments or looks up the tool, so calls it rejects
    (bad arguments, unknown tool) are logged too. Outcomes: "ok"; "tool_error" (the tool
    reported an anticipated error to the model); "rejected" (a protocol error caused by the
    request, such as an unknown resource); "raised" (a server fault, logged at ERROR).
    """
    request: _RequestContext = ctx
    if request.request_id is None:  # a notification, not a request
        return await call_next(ctx)

    token = request_id_var.set(uuid.uuid4().hex)
    started = time.perf_counter()
    fields: dict[str, Any] = {"jsonrpc_id": request.request_id, "method": request.method}
    if request.method == "tools/call":
        fields["tool"] = _param(request.params, "name")
    elif request.method == "resources/read":
        fields["uri"] = _param(request.params, "uri")
    outcome = "raised"
    error_type: str | None = None
    try:
        result: object = await call_next(ctx)
        outcome = "tool_error" if _is_error_result(result) else "ok"
        return result
    except MCPError as exc:
        error_type = type(exc).__name__
        fields["error_code"] = exc.code
        if exc.code in _CALLER_ERROR_CODES:
            outcome = "rejected"
        raise
    except BaseException as exc:
        error_type = type(exc).__name__
        raise
    finally:
        fields["outcome"] = outcome
        if error_type is not None:
            fields["error_type"] = error_type
        fields["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        level = logging.ERROR if outcome == "raised" else logging.INFO
        log_event("request", level=level, **fields)
        request_id_var.reset(token)


def _is_error_result(result: object) -> bool:
    """Middleware sees the serialised result: a tool error is a dict with isError true."""
    if not isinstance(result, Mapping):
        return False
    return bool(cast(Mapping[str, Any], result).get("isError"))


def _param(params: object, key: str) -> str | None:
    """A string field from raw request params, which the SDK has not validated yet."""
    if not isinstance(params, Mapping):
        return None
    value = cast(Mapping[str, Any], params).get(key)
    return value if isinstance(value, str) else None
