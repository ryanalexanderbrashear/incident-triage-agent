"""Data models for the synthetic incident fixtures the MCP server serves.

Everything here is served to the agent, so nothing here may carry an answer: root-cause
categories and evidence labels belong in the golden set, never in a fixture.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

# Type-prefixed and globally unique, so an ID's kind is visible from the ID alone.
INCIDENT_ID_PATTERN = r"^INC-\d{4}$"
LOG_ID_PATTERN = r"^LOG-\d{4}-\d{4}$"
TICKET_ID_PATTERN = r"^TKT-\d{4}$"
RUNBOOK_ID_PATTERN = r"^RB-[A-Z0-9]+(?:-[A-Z0-9]+)*$"

IncidentId = Annotated[str, StringConstraints(pattern=INCIDENT_ID_PATTERN)]
LogId = Annotated[str, StringConstraints(pattern=LOG_ID_PATTERN)]
TicketId = Annotated[str, StringConstraints(pattern=TICKET_ID_PATTERN)]
RunbookId = Annotated[str, StringConstraints(pattern=RUNBOOK_ID_PATTERN)]

NonEmptyStr = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
ServiceName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]*$")]


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware UTC (end it with Z or +00:00)")
    return value


UtcDatetime = Annotated[datetime, AfterValidator(_require_utc)]
AttributeValue = str | int | float | bool


class _Fixture(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Severity(StrEnum):
    SEV1 = "SEV1"
    SEV2 = "SEV2"
    SEV3 = "SEV3"
    SEV4 = "SEV4"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    FATAL = "FATAL"


class TicketStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"


class Alert(_Fixture):
    fired_at: UtcDatetime
    summary: NonEmptyStr
    metric: NonEmptyStr
    threshold: float | None = None
    observed: float | None = None


class Incident(_Fixture):
    id: IncidentId
    title: NonEmptyStr
    service: ServiceName
    severity: Severity
    alert: Alert
    runbook_ids: tuple[RunbookId, ...] = ()


class LogRecord(_Fixture):
    id: LogId
    incident_id: IncidentId
    timestamp: UtcDatetime
    service: ServiceName
    level: LogLevel
    message: NonEmptyStr
    attributes: dict[str, AttributeValue] = Field(default_factory=dict[str, AttributeValue])


class TicketComment(_Fixture):
    author: NonEmptyStr  # a role ("on-call SRE"), never a person's name
    created_at: UtcDatetime
    body: NonEmptyStr


class Ticket(_Fixture):
    id: TicketId
    incident_id: IncidentId
    title: NonEmptyStr
    status: TicketStatus
    created_at: UtcDatetime
    reporter: NonEmptyStr  # a role, never a person's name
    body: NonEmptyStr
    comments: tuple[TicketComment, ...] = ()


class Runbook(_Fixture):
    id: RunbookId
    title: NonEmptyStr
    service: ServiceName
    symptoms: tuple[NonEmptyStr, ...] = Field(min_length=1)
    steps: tuple[NonEmptyStr, ...] = Field(min_length=1)
