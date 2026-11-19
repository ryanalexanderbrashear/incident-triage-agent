"""get_ticket, get_runbook and incident_overview as pure functions over the seed fixtures."""

from __future__ import annotations

import pytest

from incident_triage.fixtures import FixtureStore, load_fixtures
from incident_triage.server.lookup import (
    UnknownRunbookError,
    UnknownTicketError,
    get_runbook,
    get_ticket,
    incident_overview,
)
from incident_triage.server.search import UnknownIncidentError


@pytest.fixture(scope="module")
def store() -> FixtureStore:
    return load_fixtures()


def test_get_ticket_returns_the_fixture(store: FixtureStore) -> None:
    assert get_ticket(store, "TKT-0001") == store.tickets["TKT-0001"]


def test_unknown_ticket_points_to_the_incident_resource(store: FixtureStore) -> None:
    with pytest.raises(UnknownTicketError, match=r"unknown ticket_id TKT-0099; .*incident://"):
        get_ticket(store, "TKT-0099")


def test_get_runbook_returns_the_fixture(store: FixtureStore) -> None:
    assert get_runbook(store, "RB-DB-POOL") == store.runbooks["RB-DB-POOL"]


def test_unknown_runbook_points_to_the_incident_resource(store: FixtureStore) -> None:
    with pytest.raises(UnknownRunbookError, match=r"unknown runbook_id RB-NOPE; .*incident://"):
        get_runbook(store, "RB-NOPE")


@pytest.mark.parametrize("incident_id", ["INC-0001", "INC-0002"])
def test_overview_is_computed_from_the_fixtures(store: FixtureStore, incident_id: str) -> None:
    incident = store.incidents[incident_id]
    logs = store.logs_for(incident_id)
    overview = incident_overview(store, incident_id)
    assert overview.id == incident.id
    assert overview.alert == incident.alert
    assert overview.runbook_ids == list(incident.runbook_ids)
    assert overview.ticket_ids == sorted(
        t.id for t in store.tickets.values() if t.incident_id == incident_id
    )
    assert overview.ticket_ids, "each seed incident has a ticket"
    assert overview.log_count == len(logs)
    assert (overview.first_log_at, overview.last_log_at) == (logs[0].timestamp, logs[-1].timestamp)
    assert overview.log_services == sorted({r.service for r in logs})
    assert incident.service in overview.log_services


def test_overview_of_unknown_incident(store: FixtureStore) -> None:
    with pytest.raises(UnknownIncidentError, match="INC-0099"):
        incident_overview(store, "INC-0099")
