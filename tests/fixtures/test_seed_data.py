"""The packaged seed fixtures load, are self-consistent, and carry no answers."""

from __future__ import annotations

import json
from importlib.resources import files

import pytest

from incident_triage.fixtures import FixtureStore, load_fixtures


@pytest.fixture(scope="module")
def store() -> FixtureStore:
    return load_fixtures()


def test_two_seed_incidents_each_with_logs_ticket_and_runbook(store: FixtureStore) -> None:
    assert sorted(store.incidents) == ["INC-0001", "INC-0002"]
    for incident_id, incident in store.incidents.items():
        assert store.logs_for(incident_id), incident_id
        assert [t for t in store.tickets.values() if t.incident_id == incident_id], incident_id
        assert incident.runbook_ids, incident_id
        for runbook_id in incident.runbook_ids:
            assert store.runbook(runbook_id) is not None


def test_every_id_resolves_to_its_kind(store: FixtureStore) -> None:
    for incident_id in store.incidents:
        assert store.kind_of(incident_id) == "incident"
        for record in store.logs_for(incident_id):
            assert store.kind_of(record.id) == "log"
    for ticket_id in store.tickets:
        assert store.kind_of(ticket_id) == "ticket"
    for runbook_id in store.runbooks:
        assert store.kind_of(runbook_id) == "runbook"
    assert store.kind_of("LOG-0009-0001") is None
    assert store.kind_of("") is None


def test_counts_match_the_store(store: FixtureStore) -> None:
    assert store.counts() == {
        "incidents": 2,
        "logs": sum(len(store.logs_for(i)) for i in store.incidents),
        "tickets": 2,
        "runbooks": 2,
    }


FORBIDDEN_KEYS = {"root_cause", "category", "label", "evidence", "evidence_ids", "answer"}


def test_no_fixture_file_carries_an_answer_key() -> None:
    """Fixtures are served to the agent; labels live only in the golden set."""
    data = files("incident_triage.fixtures") / "data"
    seen: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():  # pyright: ignore[reportUnknownVariableType]
                seen.add(str(key))  # pyright: ignore[reportUnknownArgumentType]
                walk(value)
        elif isinstance(node, list):
            for item in node:  # pyright: ignore[reportUnknownVariableType]
                walk(item)

    for directory in (data / "incidents", data / "runbooks"):
        for entry in directory.iterdir():
            targets = list(entry.iterdir()) if entry.is_dir() else [entry]
            for target in targets:
                text = target.read_text(encoding="utf-8")
                docs = (
                    [json.loads(line) for line in text.splitlines() if line.strip()]
                    if target.name.endswith(".jsonl")
                    else [json.loads(text)]
                )
                for doc in docs:
                    walk(doc)
    assert seen, "walked nothing: the check would pass vacuously"
    assert not (seen & FORBIDDEN_KEYS)
