"""Lookups by ID, and the incident overview, as pure functions over the fixture store."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from incident_triage.fixtures import Alert, FixtureStore, Runbook, Severity, Ticket
from incident_triage.server.search import UnknownIncidentError


class UnknownTicketError(LookupError):
    def __init__(self, ticket_id: str):
        self.ticket_id = ticket_id
        super().__init__(
            f"unknown ticket_id {ticket_id}; "
            "an incident's ticket IDs are listed in its incident://<incident_id> resource"
        )


class UnknownRunbookError(LookupError):
    def __init__(self, runbook_id: str):
        self.runbook_id = runbook_id
        super().__init__(
            f"unknown runbook_id {runbook_id}; "
            "an incident's runbook IDs are listed in its incident://<incident_id> resource"
        )


class IncidentOverview(BaseModel):
    """Where an investigation starts. Built only from fixture fields, which carry no labels."""

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    service: str
    severity: Severity
    alert: Alert
    runbook_ids: list[str]
    ticket_ids: list[str]
    log_count: int
    first_log_at: datetime
    last_log_at: datetime
    log_services: list[str]


def get_ticket(store: FixtureStore, ticket_id: str) -> Ticket:
    ticket = store.ticket(ticket_id)
    if ticket is None:
        raise UnknownTicketError(ticket_id)
    return ticket


def get_runbook(store: FixtureStore, runbook_id: str) -> Runbook:
    runbook = store.runbook(runbook_id)
    if runbook is None:
        raise UnknownRunbookError(runbook_id)
    return runbook


def incident_overview(store: FixtureStore, incident_id: str) -> IncidentOverview:
    incident = store.incidents.get(incident_id)
    if incident is None:
        raise UnknownIncidentError(incident_id, sorted(store.incidents))
    logs = store.logs_for(incident_id)  # the loader guarantees at least one, in time order
    return IncidentOverview(
        id=incident.id,
        title=incident.title,
        service=incident.service,
        severity=incident.severity,
        alert=incident.alert,
        runbook_ids=list(incident.runbook_ids),
        ticket_ids=sorted(t.id for t in store.tickets.values() if t.incident_id == incident_id),
        log_count=len(logs),
        first_log_at=logs[0].timestamp,
        last_log_at=logs[-1].timestamp,
        log_services=sorted({record.service for record in logs}),
    )
