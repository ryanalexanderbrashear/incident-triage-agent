"""`search_logs` as a pure function over the fixture store, independent of MCP."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from incident_triage.fixtures import FixtureStore, LogLevel, LogRecord

MAX_LIMIT = 50
DEFAULT_LIMIT = 20
MAX_QUERY_LENGTH = 200

_LEVEL_RANK = {level: rank for rank, level in enumerate(LogLevel)}


class UnknownIncidentError(LookupError):
    def __init__(self, incident_id: str, known: list[str]):
        self.incident_id = incident_id
        self.known = known
        super().__init__(f"unknown incident_id {incident_id}; known incidents: {', '.join(known)}")


class SearchLogsResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    incident_id: str
    total_matches: int
    returned: int
    offset: int
    next_offset: int | None
    records: list[LogRecord]


def search_logs(
    store: FixtureStore,
    *,
    incident_id: str,
    query: str = "",
    level: LogLevel | None = None,
    service: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> SearchLogsResult:
    """Return one page of an incident's log records, in timestamp order.

    `query` is whitespace-separated terms; a record matches when every term appears,
    case-insensitively, in its message, service, level, or an attribute key or value.
    `level` is a minimum: WARN also returns ERROR and FATAL. Arguments are assumed valid
    (the MCP layer enforces the bounds); an unknown incident raises UnknownIncidentError.
    """
    if incident_id not in store.incidents:
        raise UnknownIncidentError(incident_id, sorted(store.incidents))

    terms = [term.casefold() for term in query.split()]
    min_rank = _LEVEL_RANK[level] if level is not None else 0
    matches = [
        record
        for record in store.logs_for(incident_id)
        if _LEVEL_RANK[record.level] >= min_rank
        and (service is None or record.service == service)
        and all(term in _haystack(record) for term in terms)
    ]

    page = matches[offset : offset + limit]
    end = offset + len(page)
    return SearchLogsResult(
        incident_id=incident_id,
        total_matches=len(matches),
        returned=len(page),
        offset=offset,
        next_offset=end if end < len(matches) else None,
        records=page,
    )


def _haystack(record: LogRecord) -> str:
    parts = [record.message, record.service, record.level.value]
    for key, value in record.attributes.items():
        parts.append(key)
        parts.append(str(value))
    return "\n".join(parts).casefold()
