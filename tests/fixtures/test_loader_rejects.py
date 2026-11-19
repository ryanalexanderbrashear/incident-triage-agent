"""Each loader check, exercised on a minimal fixture tree written to tmp_path.

Every test builds the same valid tree, so `test_minimal_tree_is_valid` proves that a rejection
below comes from the one mutation the test makes, not from a broken baseline.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from incident_triage.fixtures import FixtureError, load_fixtures
from incident_triage.fixtures.models import (
    MAX_ATTRIBUTE_KEY_CHARS,
    MAX_ATTRIBUTE_VALUE_CHARS,
    MAX_COMMENT_CHARS,
    MAX_LOG_ATTRIBUTES,
    MAX_LOG_MESSAGE_CHARS,
    MAX_LOGS_PER_INCIDENT,
    MAX_RUNBOOK_ID_CHARS,
    MAX_RUNBOOK_ITEM_CHARS,
    MAX_RUNBOOK_STEPS,
    MAX_RUNBOOK_SYMPTOMS,
    MAX_RUNBOOKS_PER_INCIDENT,
    MAX_SERVICE_NAME_CHARS,
    MAX_SERVICES_PER_INCIDENT,
    MAX_SHORT_TEXT_CHARS,
    MAX_TICKET_BODY_CHARS,
    MAX_TICKET_COMMENTS,
)

Tree = dict[str, Any]


def _log(n: int, ts: str, **overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": f"LOG-0001-{n:04d}",
        "incident_id": "INC-0001",
        "timestamp": ts,
        "service": "checkout-api",
        "level": "INFO",
        "message": f"line {n}",
        "attributes": {"n": n},
    }
    record.update(overrides)
    return record


def _valid_tree() -> Tree:
    return {
        "runbooks/RB-TEST.json": {
            "id": "RB-TEST",
            "title": "Test runbook",
            "service": "checkout-api",
            "symptoms": ["something is slow"],
            "steps": ["look at it"],
        },
        "incidents/INC-0001/incident.json": {
            "id": "INC-0001",
            "title": "Test incident",
            "service": "checkout-api",
            "severity": "SEV3",
            "alert": {"fired_at": "2026-01-01T00:05:00Z", "summary": "slow", "metric": "p99_ms"},
            "runbook_ids": ["RB-TEST"],
        },
        "incidents/INC-0001/logs.jsonl": [
            _log(1, "2026-01-01T00:00:00Z"),
            _log(2, "2026-01-01T00:01:00Z"),
        ],
        "incidents/INC-0001/ticket.json": {
            "id": "TKT-0001",
            "incident_id": "INC-0001",
            "title": "Test ticket",
            "status": "open",
            "created_at": "2026-01-01T00:06:00Z",
            "reporter": "support lead",
            "body": "It is slow.",
        },
    }


def _write(tree: Tree, root: Path) -> Path:
    for rel, content in tree.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        elif rel.endswith(".jsonl"):
            path.write_text("".join(json.dumps(r) + "\n" for r in content), encoding="utf-8")
        else:
            path.write_text(json.dumps(content), encoding="utf-8")
    return root


def _load(tmp_path: Path, mutate: Callable[[Tree], None]) -> None:
    tree = _valid_tree()
    mutate(tree)
    load_fixtures(_write(tree, tmp_path))


def test_minimal_tree_is_valid(tmp_path: Path) -> None:
    store = load_fixtures(_write(_valid_tree(), tmp_path))
    assert store.counts() == {"incidents": 1, "logs": 2, "tickets": 1, "runbooks": 1}


def test_duplicate_id_across_files(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        second = _valid_tree()["incidents/INC-0001/ticket.json"] | {
            "id": "TKT-0001",
            "incident_id": "INC-0002",
        }
        tree["incidents/INC-0002/incident.json"] = tree["incidents/INC-0001/incident.json"] | {
            "id": "INC-0002"
        }
        tree["incidents/INC-0002/logs.jsonl"] = [
            _log(1, "2026-01-01T00:00:00Z", id="LOG-0002-0001", incident_id="INC-0002")
        ]
        tree["incidents/INC-0002/ticket.json"] = second

    with pytest.raises(
        FixtureError,
        match=(
            r"incidents/INC-0002/ticket\.json: TKT-0001: "
            r"duplicate ID, first defined in incidents/INC-0001/ticket\.json"
        ),
    ):
        _load(tmp_path, mutate)


def test_malformed_id(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["incidents/INC-0001/ticket.json"]["id"] = "TICKET-1"

    with pytest.raises(
        FixtureError, match=r"ticket\.json: TICKET-1: id: String should match pattern"
    ):
        _load(tmp_path, mutate)


def test_naive_timestamp(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["incidents/INC-0001/logs.jsonl"][1]["timestamp"] = "2026-01-01T00:01:00"

    with pytest.raises(
        FixtureError, match=r"logs\.jsonl:2: LOG-0001-0002: timestamp: .*timezone-aware UTC"
    ):
        _load(tmp_path, mutate)


def test_non_utc_offset(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["incidents/INC-0001/logs.jsonl"][1]["timestamp"] = "2026-01-01T01:01:00+01:00"

    with pytest.raises(
        FixtureError, match=r"logs\.jsonl:2: LOG-0001-0002: timestamp: .*timezone-aware UTC"
    ):
        _load(tmp_path, mutate)


def test_unknown_level(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["incidents/INC-0001/logs.jsonl"][0]["level"] = "WARNING"

    with pytest.raises(FixtureError, match=r"logs\.jsonl:1: LOG-0001-0001: level: "):
        _load(tmp_path, mutate)


def test_extra_field(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["incidents/INC-0001/incident.json"]["root_cause"] = "db pool"

    with pytest.raises(
        FixtureError, match=r"incident\.json: INC-0001: root_cause: Extra inputs are not permitted"
    ):
        _load(tmp_path, mutate)


def test_log_in_wrong_incident_file(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["incidents/INC-0001/logs.jsonl"][1]["incident_id"] = "INC-0002"

    with pytest.raises(
        FixtureError, match=r"logs\.jsonl:2: LOG-0001-0002: incident_id must be INC-0001"
    ):
        _load(tmp_path, mutate)


def test_log_id_prefix_must_match_incident(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["incidents/INC-0001/logs.jsonl"][1]["id"] = "LOG-0002-0002"

    with pytest.raises(
        FixtureError,
        match=r"logs\.jsonl:2: LOG-0002-0002: log IDs in this file must start LOG-0001-",
    ):
        _load(tmp_path, mutate)


def test_out_of_order_timestamps(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["incidents/INC-0001/logs.jsonl"][1]["timestamp"] = "2025-12-31T23:59:00Z"

    with pytest.raises(
        FixtureError, match=r"logs\.jsonl:2: LOG-0001-0002: timestamp earlier than previous line"
    ):
        _load(tmp_path, mutate)


def test_no_log_before_alert(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["incidents/INC-0001/incident.json"]["alert"]["fired_at"] = "2025-12-31T00:00:00Z"

    with pytest.raises(
        FixtureError, match=r"logs\.jsonl: INC-0001: no log at or before the alert's fired_at"
    ):
        _load(tmp_path, mutate)


def test_dangling_runbook_reference(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["incidents/INC-0001/incident.json"]["runbook_ids"] = ["RB-MISSING"]

    with pytest.raises(FixtureError, match=r"incident\.json: INC-0001: unknown runbook RB-MISSING"):
        _load(tmp_path, mutate)


def test_ticket_for_another_incident(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["incidents/INC-0001/ticket.json"]["incident_id"] = "INC-0009"

    with pytest.raises(FixtureError, match=r"ticket\.json: TKT-0001: incident_id must be INC-0001"):
        _load(tmp_path, mutate)


def test_directory_name_must_match_incident_id(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["incidents/INC-0001/incident.json"]["id"] = "INC-0003"

    with pytest.raises(
        FixtureError, match=r"incident\.json: INC-0003: directory must be named INC-0003"
    ):
        _load(tmp_path, mutate)


def test_runbook_file_name_must_match_id(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["runbooks/RB-TEST.json"]["id"] = "RB-OTHER"

    with pytest.raises(
        FixtureError, match=r"runbooks/RB-TEST\.json: RB-OTHER: file name must be RB-OTHER\.json"
    ):
        _load(tmp_path, mutate)


def test_malformed_json_names_the_line(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        lines = "".join(json.dumps(r) + "\n" for r in tree["incidents/INC-0001/logs.jsonl"])
        tree["incidents/INC-0001/logs.jsonl"] = lines + "\n" + '{"id": "LOG-0001-0003",\n'

    with pytest.raises(FixtureError, match=r"logs\.jsonl:4: -: invalid JSON"):
        _load(tmp_path, mutate)


def test_missing_logs_file(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        del tree["incidents/INC-0001/logs.jsonl"]

    with pytest.raises(FixtureError, match=r"logs\.jsonl: INC-0001: missing"):
        _load(tmp_path, mutate)


def test_empty_runbook_steps(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["runbooks/RB-TEST.json"]["steps"] = []

    with pytest.raises(FixtureError, match=r"RB-TEST\.json: RB-TEST: steps: "):
        _load(tmp_path, mutate)


# Size limits: every server response is bounded because the fixtures are.


def test_log_message_over_limit(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["incidents/INC-0001/logs.jsonl"][0]["message"] = "x" * (MAX_LOG_MESSAGE_CHARS + 1)

    with pytest.raises(
        FixtureError,
        match=rf"logs\.jsonl:1: LOG-0001-0001: message: .*at most {MAX_LOG_MESSAGE_CHARS}",
    ):
        _load(tmp_path, mutate)


def test_too_many_log_attributes(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        attrs = {f"k{i}": i for i in range(MAX_LOG_ATTRIBUTES + 1)}
        tree["incidents/INC-0001/logs.jsonl"][0]["attributes"] = attrs

    with pytest.raises(FixtureError, match=r"logs\.jsonl:1: LOG-0001-0001: attributes: "):
        _load(tmp_path, mutate)


def test_ticket_body_over_limit(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["incidents/INC-0001/ticket.json"]["body"] = "x" * (MAX_TICKET_BODY_CHARS + 1)

    with pytest.raises(
        FixtureError, match=rf"ticket\.json: TKT-0001: body: .*at most {MAX_TICKET_BODY_CHARS}"
    ):
        _load(tmp_path, mutate)


def _comment(body: str = "noted") -> dict[str, Any]:
    return {"author": "on-call SRE", "created_at": "2026-01-01T00:07:00Z", "body": body}


def test_too_many_ticket_comments(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["incidents/INC-0001/ticket.json"]["comments"] = [
            _comment() for _ in range(MAX_TICKET_COMMENTS + 1)
        ]

    with pytest.raises(FixtureError, match=r"ticket\.json: TKT-0001: comments: "):
        _load(tmp_path, mutate)


def test_ticket_comment_over_limit(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["incidents/INC-0001/ticket.json"]["comments"] = [
            _comment("x" * (MAX_COMMENT_CHARS + 1))
        ]

    with pytest.raises(FixtureError, match=r"ticket\.json: TKT-0001: comments\.0\.body: "):
        _load(tmp_path, mutate)


def test_too_many_runbook_steps(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["runbooks/RB-TEST.json"]["steps"] = [f"step {i}" for i in range(MAX_RUNBOOK_STEPS + 1)]

    with pytest.raises(FixtureError, match=r"RB-TEST\.json: RB-TEST: steps: "):
        _load(tmp_path, mutate)


def test_too_many_runbook_symptoms(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["runbooks/RB-TEST.json"]["symptoms"] = [
            f"symptom {i}" for i in range(MAX_RUNBOOK_SYMPTOMS + 1)
        ]

    with pytest.raises(FixtureError, match=r"RB-TEST\.json: RB-TEST: symptoms: "):
        _load(tmp_path, mutate)


def test_runbook_item_over_limit(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["runbooks/RB-TEST.json"]["steps"] = ["x" * (MAX_RUNBOOK_ITEM_CHARS + 1)]

    with pytest.raises(FixtureError, match=r"RB-TEST\.json: RB-TEST: steps\.0: "):
        _load(tmp_path, mutate)


def test_limits_are_inclusive(tmp_path: Path) -> None:
    """Exactly at each limit loads: the tests above fail on the limit, not below it."""

    def mutate(tree: Tree) -> None:
        log = tree["incidents/INC-0001/logs.jsonl"][0]
        log["message"] = "x" * MAX_LOG_MESSAGE_CHARS
        log["attributes"] = {f"k{i}": i for i in range(MAX_LOG_ATTRIBUTES)}
        ticket = tree["incidents/INC-0001/ticket.json"]
        ticket["body"] = "x" * MAX_TICKET_BODY_CHARS
        ticket["comments"] = [_comment("x" * MAX_COMMENT_CHARS) for _ in range(MAX_TICKET_COMMENTS)]
        runbook = tree["runbooks/RB-TEST.json"]
        runbook["steps"] = ["x" * MAX_RUNBOOK_ITEM_CHARS for _ in range(MAX_RUNBOOK_STEPS)]
        runbook["symptoms"] = ["x" * MAX_RUNBOOK_ITEM_CHARS for _ in range(MAX_RUNBOOK_SYMPTOMS)]

    _load(tmp_path, mutate)


@pytest.mark.parametrize(
    ("rel", "path", "value", "fragment"),
    [
        ("incidents/INC-0001/incident.json", ("title",), "x" * (MAX_SHORT_TEXT_CHARS + 1), "title"),
        (
            "incidents/INC-0001/incident.json",
            ("alert", "summary"),
            "x" * (MAX_SHORT_TEXT_CHARS + 1),
            "alert.summary",
        ),
        (
            "incidents/INC-0001/incident.json",
            ("alert", "metric"),
            "x" * (MAX_SHORT_TEXT_CHARS + 1),
            "alert.metric",
        ),
        (
            "incidents/INC-0001/incident.json",
            ("service",),
            "s" * (MAX_SERVICE_NAME_CHARS + 1),
            "service",
        ),
        ("incidents/INC-0001/ticket.json", ("title",), "x" * (MAX_SHORT_TEXT_CHARS + 1), "title"),
        (
            "incidents/INC-0001/ticket.json",
            ("reporter",),
            "x" * (MAX_SHORT_TEXT_CHARS + 1),
            "reporter",
        ),
        ("runbooks/RB-TEST.json", ("title",), "x" * (MAX_SHORT_TEXT_CHARS + 1), "title"),
    ],
    ids=lambda v: v if isinstance(v, str) and len(v) < 40 else "",
)
def test_short_text_fields_are_bounded(
    tmp_path: Path, rel: str, path: tuple[str, ...], value: str, fragment: str
) -> None:
    def mutate(tree: Tree) -> None:
        node = tree[rel]
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    with pytest.raises(FixtureError, match=rf": {re.escape(fragment)}: "):
        _load(tmp_path, mutate)


def test_comment_author_is_bounded(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        comment = _comment() | {"author": "x" * (MAX_SHORT_TEXT_CHARS + 1)}
        tree["incidents/INC-0001/ticket.json"]["comments"] = [comment]

    with pytest.raises(FixtureError, match=r"comments\.0\.author: "):
        _load(tmp_path, mutate)


def test_attribute_key_is_bounded(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["incidents/INC-0001/logs.jsonl"][0]["attributes"] = {
            "k" * (MAX_ATTRIBUTE_KEY_CHARS + 1): 1
        }

    with pytest.raises(FixtureError, match=r"logs\.jsonl:1: LOG-0001-0001: attributes\."):
        _load(tmp_path, mutate)


def test_attribute_string_value_is_bounded(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["incidents/INC-0001/logs.jsonl"][0]["attributes"] = {
            "k": "v" * (MAX_ATTRIBUTE_VALUE_CHARS + 1)
        }

    with pytest.raises(FixtureError, match=r"logs\.jsonl:1: LOG-0001-0001: attributes\.k"):
        _load(tmp_path, mutate)


def test_too_many_runbooks_per_incident(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        ids = [f"RB-T{i}" for i in range(MAX_RUNBOOKS_PER_INCIDENT + 1)]
        tree["incidents/INC-0001/incident.json"]["runbook_ids"] = ids

    with pytest.raises(FixtureError, match=r"incident\.json: INC-0001: runbook_ids: "):
        _load(tmp_path, mutate)


def test_runbook_id_is_bounded(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["runbooks/RB-TEST.json"]["id"] = "RB-" + "X" * MAX_RUNBOOK_ID_CHARS

    with pytest.raises(FixtureError, match=r"RB-TEST\.json: RB-X+: id: "):
        _load(tmp_path, mutate)


def test_too_many_logs_per_incident(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["incidents/INC-0001/logs.jsonl"] = [
            _log(n, "2026-01-01T00:00:00Z") for n in range(1, MAX_LOGS_PER_INCIDENT + 2)
        ]

    with pytest.raises(
        FixtureError,
        match=rf"logs\.jsonl:{MAX_LOGS_PER_INCIDENT + 1}: .*more than {MAX_LOGS_PER_INCIDENT}",
    ):
        _load(tmp_path, mutate)


def test_too_many_services_per_incident(tmp_path: Path) -> None:
    def mutate(tree: Tree) -> None:
        tree["incidents/INC-0001/logs.jsonl"] = [
            _log(n, "2026-01-01T00:00:00Z", service=f"svc-{n}")
            for n in range(1, MAX_SERVICES_PER_INCIDENT + 2)
        ]

    with pytest.raises(
        FixtureError, match=rf"INC-0001: {MAX_SERVICES_PER_INCIDENT + 1} distinct services"
    ):
        _load(tmp_path, mutate)
