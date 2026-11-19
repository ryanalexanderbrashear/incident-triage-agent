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
MAX_RUNBOOK_ID_CHARS = 40
RunbookId = Annotated[
    str, StringConstraints(pattern=RUNBOOK_ID_PATTERN, max_length=MAX_RUNBOOK_ID_CHARS)
]

# Size limits. No fixture string or collection is unbounded, and the limits are set so that the
# largest possible single-item response (a ticket, a runbook, an overview, one log record) fits
# the server's response budget; tests/server/test_protocol.py builds that worst case from these
# constants and measures it. An oversized fixture fails at load time, naming its file and line,
# rather than being truncated at serve time (which could silently cut an evidence ID). To raise
# one, raise it here and let the worst-case test say whether the budget still holds.
MAX_SHORT_TEXT_CHARS = 200  # titles, alert summary and metric, reporter, comment author
MAX_SERVICE_NAME_CHARS = 64
MAX_LOG_MESSAGE_CHARS = 1_000
MAX_LOG_ATTRIBUTES = 10
MAX_ATTRIBUTE_KEY_CHARS = 40
MAX_ATTRIBUTE_VALUE_CHARS = 200
MAX_LOGS_PER_INCIDENT = 1_000
MAX_SERVICES_PER_INCIDENT = 20  # distinct services across an incident's logs
MAX_RUNBOOKS_PER_INCIDENT = 10
MAX_TICKET_BODY_CHARS = 3_000
MAX_TICKET_COMMENTS = 8
MAX_COMMENT_CHARS = 1_000
MAX_RUNBOOK_SYMPTOMS = 10
MAX_RUNBOOK_STEPS = 20
MAX_RUNBOOK_ITEM_CHARS = 300


def _bounded(max_length: int) -> StringConstraints:
    return StringConstraints(min_length=1, max_length=max_length, strip_whitespace=True)


NonEmptyStr = Annotated[str, _bounded(MAX_SHORT_TEXT_CHARS)]
LogMessage = Annotated[str, _bounded(MAX_LOG_MESSAGE_CHARS)]
TicketBody = Annotated[str, _bounded(MAX_TICKET_BODY_CHARS)]
CommentBody = Annotated[str, _bounded(MAX_COMMENT_CHARS)]
RunbookItem = Annotated[str, _bounded(MAX_RUNBOOK_ITEM_CHARS)]
ServiceName = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9-]*$", max_length=MAX_SERVICE_NAME_CHARS)
]
AttributeKey = Annotated[str, _bounded(MAX_ATTRIBUTE_KEY_CHARS)]
AttributeText = Annotated[str, StringConstraints(max_length=MAX_ATTRIBUTE_VALUE_CHARS)]


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware UTC (end it with Z or +00:00)")
    return value


UtcDatetime = Annotated[datetime, AfterValidator(_require_utc)]
AttributeValue = AttributeText | int | float | bool


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
    runbook_ids: tuple[RunbookId, ...] = Field(default=(), max_length=MAX_RUNBOOKS_PER_INCIDENT)


class LogRecord(_Fixture):
    id: LogId
    incident_id: IncidentId
    timestamp: UtcDatetime
    service: ServiceName
    level: LogLevel
    message: LogMessage
    attributes: dict[AttributeKey, AttributeValue] = Field(
        default_factory=dict[AttributeKey, AttributeValue], max_length=MAX_LOG_ATTRIBUTES
    )


class TicketComment(_Fixture):
    author: NonEmptyStr  # a role ("on-call SRE"), never a person's name
    created_at: UtcDatetime
    body: CommentBody


class Ticket(_Fixture):
    id: TicketId
    incident_id: IncidentId
    title: NonEmptyStr
    status: TicketStatus
    created_at: UtcDatetime
    reporter: NonEmptyStr  # a role, never a person's name
    body: TicketBody
    comments: tuple[TicketComment, ...] = Field(default=(), max_length=MAX_TICKET_COMMENTS)


class Runbook(_Fixture):
    id: RunbookId
    title: NonEmptyStr
    service: ServiceName
    symptoms: tuple[RunbookItem, ...] = Field(min_length=1, max_length=MAX_RUNBOOK_SYMPTOMS)
    steps: tuple[RunbookItem, ...] = Field(min_length=1, max_length=MAX_RUNBOOK_STEPS)
