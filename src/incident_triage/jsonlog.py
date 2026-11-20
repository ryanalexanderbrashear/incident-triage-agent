"""JSON-lines logging to stderr, shared by the server and the agent.

Never stdout: under stdio it is the MCP protocol channel, and the agent CLI prints its answer
there. Each line is one JSON object with `ts`, `level`, `event` and the event's fields.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

ContextFields = Callable[[], Mapping[str, Any]]


class StderrHandler(logging.Handler):
    """Writes to whatever sys.stderr is at emit time, so test capture and redirection work."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            sys.stderr.write(self.format(record) + "\n")
            sys.stderr.flush()
        except Exception:
            self.handleError(record)


class JsonFormatter(logging.Formatter):
    def __init__(self, context: ContextFields | None = None) -> None:
        super().__init__()
        self._context = context

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        if self._context is not None:
            payload.update(self._context())
        fields: Mapping[str, Any] = getattr(record, "fields", {})
        payload.update(fields)
        return json.dumps(payload, default=str)


def json_logger(name: str, context: ContextFields | None = None) -> logging.Logger:
    """A logger that writes JSON lines to stderr and never propagates to the root logger."""
    logger = logging.getLogger(name)
    if not any(isinstance(h, StderrHandler) for h in logger.handlers):
        handler = StderrHandler()
        handler.setFormatter(JsonFormatter(context))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False  # the MCP SDK configures the root logger; keep our lines ours
    return logger
