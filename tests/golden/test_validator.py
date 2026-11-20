"""The golden-case validator: one test per rule, each built from a valid case with one change."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from incident_triage.fixtures import FixtureStore, load_fixtures
from incident_triage.golden import LabelStatus, load_golden
from incident_triage.golden.__main__ import main as golden_main

REPO = Path(__file__).resolve().parents[2]
SEED = REPO / "golden" / "cases.jsonl"


@pytest.fixture(scope="module")
def store() -> FixtureStore:
    return load_fixtures()


def _case(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "id": "GC-0101",
        "incident_id": "INC-0001",
        "difficulty": "straightforward",
        "root_cause_category": "config_or_deploy_change",
        "evidence_ids": ["LOG-0001-0006", "TKT-0001"],
        "supporting_ids": ["LOG-0001-0014"],
        "injection_ids": [],
        "rationale": "The concurrency change exhausted the pool.",
        "label_status": "draft",
        "verified_on": None,
    }
    return base | overrides


def _validate(tmp_path: Path, store: FixtureStore, *cases: dict[str, Any] | str) -> list[str]:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        "".join((c if isinstance(c, str) else json.dumps(c)) + "\n" for c in cases),
        encoding="utf-8",
    )
    return [str(p) for p in load_golden(path, store).problems]


def test_a_valid_case_has_no_problems(tmp_path: Path, store: FixtureStore) -> None:
    assert _validate(tmp_path, store, _case()) == []


def test_the_committed_seed_cases_are_valid(store: FixtureStore) -> None:
    golden = load_golden(SEED, store)
    assert golden.problems == ()
    assert sorted(golden.cases) == ["GC-0001", "GC-0002"]


def test_seed_cases_are_drafts_until_the_human_verifies_them(store: FixtureStore) -> None:
    """Only the human sets `verified`. This test is updated when they do, never by a draft."""
    golden = load_golden(SEED, store)
    for case in golden.cases.values():
        if case.label_status is LabelStatus.VERIFIED:
            assert case.verified_on is not None
    assert golden.summary()["cases"] == 2


def _one(problems: list[str], fragment: str) -> None:
    assert len(problems) >= 1, "expected a problem"
    assert any(fragment in p for p in problems), problems


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"root_cause_category": None}, "root_cause_category"),
        ({"root_cause_category": "dns_outage"}, "root_cause_category"),
        ({"rationale": ""}, "rationale"),
        ({"rationale": "x" * 2001}, "rationale"),
        ({"difficulty": "medium"}, "difficulty"),
        ({"id": "CASE-1"}, "id"),
        ({"schema_version": 2}, "schema_version"),
        ({"confidence": 0.9}, "confidence: Extra inputs are not permitted"),
        ({"evidence_ids": []}, "evidence_ids is required unless the category is cannot_determine"),
        (
            {"root_cause_category": "cannot_determine"},
            "a cannot_determine case has no evidence_ids",
        ),
        ({"supporting_ids": ["LOG-0001-0006"]}, "in both evidence_ids and supporting_ids"),
        ({"injection_ids": ["LOG-0001-0006"]}, "an injected record cannot prove the cause"),
        ({"evidence_ids": ["LOG-0001-0006", "LOG-0001-0006"]}, "evidence_ids has duplicates"),
        ({"label_status": "verified"}, "a verified case needs verified_on"),
        ({"verified_on": "2026-11-20"}, "a draft case has no verified_on"),
    ],
    ids=lambda v: str(v)[:40],
)
def test_each_model_rule(
    tmp_path: Path, store: FixtureStore, change: dict[str, Any], fragment: str
) -> None:
    case = _case(**change)
    if change.get("root_cause_category") is None and "root_cause_category" in change:
        del case["root_cause_category"]  # a missing label, not a null one
    _one(_validate(tmp_path, store, case), fragment)


@pytest.mark.parametrize(
    ("field", "record_id", "fragment"),
    [
        ("evidence_ids", "LOG-0001-9999", "does not exist in the fixtures"),
        ("evidence_ids", "RB-DB-POOL", "is a runbook, not a log record or ticket"),
        ("evidence_ids", "INC-0001", "is a incident, not a log record or ticket"),
        ("evidence_ids", "LOG-0002-0010", "belongs to another incident, not INC-0001"),
        ("evidence_ids", "TKT-0002", "belongs to another incident, not INC-0001"),
        ("supporting_ids", "LOG-0002-0010", "belongs to another incident"),
        ("injection_ids", "LOG-0001-9999", "does not exist in the fixtures"),
    ],
)
def test_labelled_ids_must_exist_be_evidence_and_belong_to_the_incident(
    tmp_path: Path, store: FixtureStore, field: str, record_id: str, fragment: str
) -> None:
    case = _case()
    case[field] = [*case[field], record_id] if field == "evidence_ids" else [record_id]
    _one(_validate(tmp_path, store, case), f"{field}: {record_id} {fragment}")


def test_unknown_incident(tmp_path: Path, store: FixtureStore) -> None:
    case = _case(incident_id="INC-0099", evidence_ids=["LOG-0099-0001"], supporting_ids=[])
    _one(_validate(tmp_path, store, case), "incident_id INC-0099 does not exist")


def test_duplicate_case_id(tmp_path: Path, store: FixtureStore) -> None:
    second = _case(incident_id="INC-0002", evidence_ids=["LOG-0002-0010"], supporting_ids=[])
    _one(_validate(tmp_path, store, _case(), second), "duplicate case ID, first on line 1")


def test_one_case_per_incident(tmp_path: Path, store: FixtureStore) -> None:
    _one(
        _validate(tmp_path, store, _case(), _case(id="GC-0102")),
        "INC-0001 already has a case (GC-0101)",
    )


def test_invalid_json_names_its_line(tmp_path: Path, store: FixtureStore) -> None:
    problems = _validate(tmp_path, store, _case(), '{"id": "GC-0102",')
    assert any(
        p.endswith(":2: -: invalid JSON: Expecting property name enclosed in double quotes")
        or ":2: -: invalid JSON" in p
        for p in problems
    ), problems


def test_every_problem_is_reported_not_just_the_first(tmp_path: Path, store: FixtureStore) -> None:
    bad_a = _case(id="GC-0101", rationale="")
    bad_b = _case(
        id="GC-0102", incident_id="INC-0002", evidence_ids=["LOG-0001-0006"], supporting_ids=[]
    )
    problems = _validate(tmp_path, store, bad_a, bad_b)
    assert any("GC-0101" in p and "rationale" in p for p in problems)
    assert any("GC-0102" in p and "belongs to another incident" in p for p in problems)


def test_a_missing_file_is_a_problem(tmp_path: Path, store: FixtureStore) -> None:
    assert [str(p) for p in load_golden(tmp_path / "nope.jsonl", store).problems] == [
        f"{tmp_path / 'nope.jsonl'}: -: missing"
    ]


def test_cli_exits_0_on_the_seed_and_1_with_every_problem_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert golden_main(["validate", "--golden", str(SEED)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["cases"] == 2 and summary["problems"] == 0
    broken = tmp_path / "cases.jsonl"
    broken.write_text(json.dumps(_case(rationale="")) + "\n", encoding="utf-8")
    assert golden_main(["validate", "--golden", str(broken)]) == 1
    assert "GC-0101: rationale" in capsys.readouterr().err


def test_labels_are_not_in_the_served_fixture_data() -> None:
    data = REPO / "src" / "incident_triage" / "fixtures" / "data"
    assert data.is_dir()
    assert not list(data.rglob("cases.jsonl"))
    assert not (REPO / "golden").is_relative_to(data)
