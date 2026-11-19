"""Structured JSON logging for incident-mcp, to stderr only.

Under stdio, stdout is the MCP protocol channel, so nothing here may ever write to it.
Every inbound request gets a fresh request ID (the JSON-RPC ID restarts per connection, so
it cannot identify a request across sessions). The ID lives in a ContextVar, so anything
logged while the request is handled carries it.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, INVALID_REQUEST, METHOD_NOT_FOUND

LOGGER_NAME = "incident_mcp"

# Protocol errors that mean the caller asked wrongly (an unknown resource arrives as
# INVALID_PARAMS). They are logged as "rejected" at INFO; anything else that escapes a handler
# is a server fault, logged as "raised" at ERROR.
_CALLER_ERROR_CODES = frozenset({INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS})

request_id_var: ContextVar[str | None] = ContextVar("incident_mcp_request_id", default=None)


class _StderrHandler(logging.Handler):
    """Writes to whatever sys.stderr is at emit time, so test capture and redirection work."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            sys.stderr.write(self.format(record) + "\n")
            sys.stderr.flush()
        except Exception:
            self.handleError(record)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id is not None:
            payload["request_id"] = request_id
        fields: Mapping[str, Any] = getattr(record, "fields", {})
        payload.update(fields)
        return json.dumps(payload, default=str)


def get_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if not any(isinstance(h, _StderrHandler) for h in logger.handlers):
        handler = _StderrHandler()
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False  # the SDK configures the root logger; keep our lines ours
    return logger


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
