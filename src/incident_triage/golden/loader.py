"""Load golden/cases.jsonl and check every case against the fixtures.

Unlike the fixture loader, which stops at the first problem (a server must not start on bad
data), this reports every problem it finds, because its reader is a person fixing a file.
Each problem reads `<path>:<line>: <case id>: <problem>`.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from pydantic import ValidationError

from incident_triage.fixtures import FixtureStore
from incident_triage.golden.models import GoldenCase, LabelStatus

DEFAULT_PATH = Path("golden/cases.jsonl")
EVIDENCE_KINDS = frozenset({"log", "ticket"})


@dataclass(frozen=True)
class Problem:
    path: str
    line: int | None
    case_id: str
    problem: str

    def __str__(self) -> str:
        where = f"{self.path}:{self.line}" if self.line is not None else self.path
        return f"{where}: {self.case_id}: {self.problem}"


@dataclass(frozen=True)
class GoldenSet:
    cases: Mapping[str, GoldenCase]
    problems: tuple[Problem, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return not self.problems

    def case(self, case_id: str) -> GoldenCase | None:
        return self.cases.get(case_id)

    def verified(self) -> list[GoldenCase]:
        return [c for c in self.cases.values() if c.label_status is LabelStatus.VERIFIED]

    def summary(self) -> dict[str, Any]:
        return {
            "cases": len(self.cases),
            "by_status": dict(
                sorted(Counter(c.label_status.value for c in self.cases.values()).items())
            ),
            "by_difficulty": dict(
                sorted(Counter(c.difficulty.value for c in self.cases.values()).items())
            ),
            "by_category": dict(
                sorted(Counter(c.root_cause_category.value for c in self.cases.values()).items())
            ),
        }


def _belongs_to(store: FixtureStore, record_id: str, incident_id: str) -> bool:
    kind = store.kind_of(record_id)
    if kind == "log":
        return record_id.startswith(f"LOG-{incident_id.removeprefix('INC-')}-")
    if kind == "ticket":
        ticket = store.ticket(record_id)
        return ticket is not None and ticket.incident_id == incident_id
    return False


def _check_ids(store: FixtureStore, case: GoldenCase) -> list[str]:
    problems: list[str] = []
    for name, ids in (
        ("evidence_ids", case.evidence_ids),
        ("supporting_ids", case.supporting_ids),
        ("injection_ids", case.injection_ids),
    ):
        for record_id in ids:
            kind = store.kind_of(record_id)
            if kind is None:
                problems.append(f"{name}: {record_id} does not exist in the fixtures")
            elif kind not in EVIDENCE_KINDS:
                problems.append(f"{name}: {record_id} is a {kind}, not a log record or ticket")
            elif not _belongs_to(store, record_id, case.incident_id):
                problems.append(
                    f"{name}: {record_id} belongs to another incident, not {case.incident_id}"
                )
    return problems


def load_golden(path: Path, store: FixtureStore) -> GoldenSet:
    """Every case that parses, plus every problem found.

    Callers decide whether problems are fatal.
    """
    rel = str(path)
    if not path.is_file():
        return GoldenSet(MappingProxyType({}), (Problem(rel, None, "-", "missing"),))
    problems: list[Problem] = []
    cases: dict[str, GoldenCase] = {}
    first_line: dict[str, int] = {}
    by_incident: dict[str, str] = {}
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw: object = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(Problem(rel, line_no, "-", f"invalid JSON: {exc.msg}"))
            continue
        raw_id = cast(Mapping[str, Any], raw).get("id", "-") if isinstance(raw, Mapping) else "-"
        case_id = raw_id if isinstance(raw_id, str) else "-"
        try:
            case = GoldenCase.model_validate(raw)
        except ValidationError as exc:
            for err in exc.errors():
                location = ".".join(str(part) for part in err["loc"])
                message = err["msg"].removeprefix("Value error, ")
                problems.append(
                    Problem(
                        rel, line_no, case_id, f"{location}: {message}" if location else message
                    )
                )
            continue
        if case.id in cases:
            problems.append(
                Problem(
                    rel, line_no, case.id, f"duplicate case ID, first on line {first_line[case.id]}"
                )
            )
            continue
        if case.incident_id not in store.incidents:
            problems.append(
                Problem(
                    rel,
                    line_no,
                    case.id,
                    f"incident_id {case.incident_id} does not exist in the fixtures",
                )
            )
            continue
        if case.incident_id in by_incident:
            problems.append(
                Problem(
                    rel,
                    line_no,
                    case.id,
                    f"{case.incident_id} already has a case ({by_incident[case.incident_id]}); "
                    "one case per incident",
                )
            )
            continue
        id_problems = _check_ids(store, case)
        problems.extend(Problem(rel, line_no, case.id, p) for p in id_problems)
        if id_problems:
            continue
        cases[case.id] = case
        first_line[case.id] = line_no
        by_incident[case.incident_id] = case.id
    if not cases and not problems:
        problems.append(Problem(rel, None, "-", "no cases"))
    return GoldenSet(MappingProxyType(cases), tuple(problems))
