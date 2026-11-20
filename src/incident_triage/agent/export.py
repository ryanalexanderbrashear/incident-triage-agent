"""Export one Langfuse trace as JSON fit for this public repository (docs/traces/).

The export is clean by construction, not by scanning afterwards:
- SDK and OpenTelemetry plumbing is dropped: metadata under `scope.` (which carries the Langfuse
  SDK's public key) and `resourceAttributes.` (process identifiers), and the account's `projectId`;
- everything left is passed through the same `redact` mask the traces use;
- if anything key-shaped survives, the export is refused rather than written.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from incident_triage.agent.tracing import contains_secret, redact

# Every field group the Observations API v2 offers. Input and output arrive as raw strings
# (the endpoint rejects parseIoAsJson), so they are exported as the strings Langfuse holds.
FIELDS = "core,basic,time,io,metadata,model,usage,metrics,trace_context"
SOURCE = "langfuse observations API v2 (observations.get_many)"
_DROPPED_FIELDS = frozenset({"projectId"})
_DROPPED_METADATA_PREFIXES = ("scope.", "resourceAttributes.")


class ExportError(RuntimeError):
    pass


class ObservationsAPI(Protocol):
    """The slice of `Langfuse().api.observations` the export uses; injected for keyless tests."""

    get_many: Callable[..., Any]  # the SDK's method has typed parameters; any callable fits


def _as_dict(observation: Any) -> dict[str, Any]:
    if isinstance(observation, Mapping):
        return dict(observation)  # pyright: ignore[reportUnknownArgumentType]
    as_dict: dict[str, Any] = observation.dict()
    return as_dict


def _clean(observation: dict[str, Any]) -> dict[str, Any]:
    cleaned = {k: v for k, v in observation.items() if k not in _DROPPED_FIELDS}
    metadata = cleaned.get("metadata")
    if isinstance(metadata, Mapping):
        entries: Mapping[str, Any] = metadata  # pyright: ignore[reportUnknownVariableType]
        cleaned["metadata"] = {
            k: v for k, v in entries.items() if not k.startswith(_DROPPED_METADATA_PREFIXES)
        }
    return cleaned


def fetch_observations(api: ObservationsAPI, trace_id: str) -> list[dict[str, Any]]:
    """Every observation of the trace, following the API's cursor."""
    observations: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        page = api.get_many(
            trace_id=trace_id, limit=100, fields=FIELDS, expand_metadata="true", cursor=cursor
        )
        observations.extend(_as_dict(o) for o in page.data)
        cursor = getattr(getattr(page, "meta", None), "cursor", None)
        if not cursor:
            return observations


def build_export(
    trace_id: str, observations: list[dict[str, Any]], *, exported_at: datetime | None = None
) -> dict[str, Any]:
    """The export document. Raises ExportError if the trace is empty or a secret survives."""
    if not observations:
        raise ExportError(
            f"no observations found for trace {trace_id} (wrong ID, or not ingested yet)"
        )
    cleaned = sorted((_clean(o) for o in observations), key=lambda o: str(o.get("startTime")))
    document: dict[str, Any] = {
        "trace_id": trace_id,
        "exported_at": (exported_at or datetime.now(UTC)).isoformat(timespec="seconds"),
        "source": SOURCE,
        "observation_count": len(cleaned),
        "observations": redact(data=json.loads(json.dumps(cleaned, default=str))),
    }
    if contains_secret(json.dumps(document)):
        raise ExportError("a key-shaped string survived redaction; refusing to write the export")
    return document


def export_trace(api: ObservationsAPI, trace_id: str) -> str:
    """Fetch, clean, redact and check one trace; return it as stable, indented JSON."""
    document = build_export(trace_id, fetch_observations(api, trace_id))
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
