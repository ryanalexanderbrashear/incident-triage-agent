"""Synthetic incident fixtures: the data the incident-mcp server serves. All of it is invented."""

from incident_triage.fixtures.loader import FixtureError, FixtureKind, FixtureStore, load_fixtures
from incident_triage.fixtures.models import (
    Alert,
    Incident,
    LogLevel,
    LogRecord,
    Runbook,
    Severity,
    Ticket,
    TicketComment,
    TicketStatus,
)

__all__ = [
    "Alert",
    "FixtureError",
    "FixtureKind",
    "FixtureStore",
    "Incident",
    "LogLevel",
    "LogRecord",
    "Runbook",
    "Severity",
    "Ticket",
    "TicketComment",
    "TicketStatus",
    "load_fixtures",
]
