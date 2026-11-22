"""Langfuse tracing for the agent: one run is one trace (ADR-002, Tracing).

Tracing never decides a run's outcome. Missing keys mean an untraced run; a failed URL lookup is
logged and ignored. Everything exported passes through `redact`, which replaces anything shaped
like an Anthropic or Langfuse key: the data is synthetic by rule, and this is the backstop.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from langfuse import Langfuse

KEY_VARIABLES = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
# The host: LANGFUSE_BASE_URL, or the deprecated LANGFUSE_HOST, which the SDK still honours.
# Without either, the SDK defaults to the EU host, and keys from another region are rejected
# there at export time: the run would be silently untraced (#47).
URL_VARIABLES = ("LANGFUSE_BASE_URL", "LANGFUSE_HOST")
# Ancestor of the OTLP exporters' loggers; failed exports are logged at ERROR below it.
EXPORTER_LOGGER = "opentelemetry.exporter.otlp"
TRACE_NAME = "triage"
REDACTED = "[redacted]"

# Anthropic API keys and admin keys; Langfuse secret and public keys.
_SECRET = re.compile(
    r"\b(?:sk-ant-[A-Za-z0-9_-]{8,}|sk-lf-[A-Za-z0-9-]{8,}|pk-lf-[A-Za-z0-9-]{8,})"
)


def contains_secret(text: str) -> bool:
    """Whether anything shaped like an Anthropic or Langfuse key appears in the text."""
    return _SECRET.search(text) is not None


def redact(*, data: Any, **_: Any) -> Any:
    """Langfuse mask function: redact secret-shaped strings anywhere in the exported data."""
    if isinstance(data, str):
        return _SECRET.sub(REDACTED, data)
    if isinstance(data, Mapping):
        mapping: Mapping[Any, Any] = data  # pyright: ignore[reportUnknownVariableType]
        return {key: redact(data=value) for key, value in mapping.items()}
    if isinstance(data, list | tuple):
        items: list[Any] | tuple[Any, ...] = data  # pyright: ignore[reportUnknownVariableType]
        return [redact(data=item) for item in items]
    return data


def langfuse_client(**options: Any) -> Langfuse:
    """Every Langfuse client this project builds: redaction is always on."""
    return Langfuse(mask=redact, **options)


def langfuse_config_problem() -> str | None:
    """Why tracing can't be configured from the environment, or None if it can."""
    missing = [name for name in KEY_VARIABLES if not os.environ.get(name)]
    if not any(os.environ.get(name) for name in URL_VARIABLES):
        missing.append("LANGFUSE_BASE_URL")
    return f"{' and '.join(missing)} not set" if missing else None


def uses_deprecated_host() -> bool:
    return not os.environ.get("LANGFUSE_BASE_URL") and bool(os.environ.get("LANGFUSE_HOST"))


def make_langfuse() -> Langfuse | None:
    """The real client from the environment, or None (an untraced run) when it's incomplete."""
    if langfuse_config_problem() is not None:
        return None
    return langfuse_client()


class ExportFailureWatcher(logging.Handler):
    """Collects span-export failures while active.

    The OTLP exporter reports a rejected or undeliverable batch only by logging at ERROR (for
    example "Failed to export span batch code: 401, reason: Unauthorized"). Langfuse.flush()
    exports synchronously, so wrapping the flush in this context manager is enough to know
    whether the trace was actually delivered.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.errors: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.errors.append(record.getMessage())

    def __enter__(self) -> ExportFailureWatcher:
        logging.getLogger(EXPORTER_LOGGER).addHandler(self)
        return self

    def __exit__(self, *_: object) -> None:
        logging.getLogger(EXPORTER_LOGGER).removeHandler(self)


def git_sha(cwd: str | None = None) -> str:
    """HEAD's SHA, with `-dirty` if the working tree has uncommitted changes; else "unknown"."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return f"{sha}-dirty" if status else sha


def trace_attributes(
    *,
    incident_id: str,
    model: str,
    effort: str,
    git: str,
    tool_budget: int,
    turn_limit: int,
    case_id: str | None = None,
) -> dict[str, Any]:
    """Keyword arguments for `langfuse.propagate_attributes`: stamped on every span in the trace.

    `incident:` names the fixture incident; `case:` is added only for a golden case (GC-…).
    """
    tags = [f"incident:{incident_id}"]
    if case_id is not None:
        tags.append(f"case:{case_id}")
    tags += [f"model:{model}", f"effort:{effort}", f"git:{git[:12]}"]
    metadata: dict[str, str] = {"incident_id": incident_id}
    if case_id is not None:
        metadata["case_id"] = case_id
    return {
        "trace_name": TRACE_NAME,
        "tags": tags,
        "metadata": metadata
        | {
            "model": model,
            "effort": effort,
            "git_sha": git,
            "tool_budget": str(tool_budget),
            "turn_limit": str(turn_limit),
        },
    }


def trace_url(
    client: Langfuse,
    trace_id: str,
    *,
    timeout_s: float = 5.0,
    log: Callable[..., None] | None = None,
) -> str | None:
    """Best-effort link to the trace.

    The lookup is a blocking API call that can raise; it must never fail a run.
    """
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(client.get_trace_url, trace_id=trace_id).result(timeout=timeout_s)
    except (FutureTimeout, Exception) as exc:
        if log is not None:
            log("trace_url_unavailable", level=logging.WARNING, error=type(exc).__name__)
        return None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
