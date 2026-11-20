"""`triage run <case-id>` runs a golden case by name, and its labels never reach the model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripted import ScriptedMessages, answer_text, message

from incident_triage.agent import cli
from incident_triage.fixtures import load_fixtures
from incident_triage.golden import load_golden
from incident_triage.server import build_server

SEED = Path(__file__).resolve().parents[2] / "golden" / "cases.jsonl"


def _script() -> ScriptedMessages:
    return ScriptedMessages(message([answer_text("expired_credential", "x", [])], "end_turn"))


def test_a_case_id_runs_its_incident_and_is_recorded(capsys: pytest.CaptureFixture[str]) -> None:
    script = _script()
    code = cli.main(
        ["run", "GC-0002", "--golden", str(SEED)], messages_api=script, server=build_server()
    )
    assert code == 0
    record = json.loads(capsys.readouterr().out)
    assert (record["case_id"], record["incident_id"]) == ("GC-0002", "INC-0002")
    assert '"id":"INC-0002"' in script.requests[0]["messages"][0]["content"]


def test_no_label_content_reaches_the_model(capsys: pytest.CaptureFixture[str]) -> None:
    case = load_golden(SEED, load_fixtures()).case("GC-0002")
    assert case is not None
    script = _script()
    cli.main(["run", "GC-0002", "--golden", str(SEED)], messages_api=script, server=build_server())
    capsys.readouterr()
    sent = json.dumps(script.requests)
    assert case.rationale[:60] not in sent, "the rationale reached the model"
    # Markers that exist only in golden cases. ("evidence_ids" is also a field of the agent's own
    # answer schema, so it legitimately appears in every request.)
    for marker in ("GC-0002", "label_status", "supporting_ids", "injection_ids", "verified_on"):
        assert marker not in sent, f"{marker!r} reached the model"


def test_an_unknown_case_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["run", "GC-0099", "--golden", str(SEED)], messages_api=_script())
    assert code == 2
    assert "unknown golden case GC-0099" in capsys.readouterr().err


def test_an_unknown_case_in_a_broken_file_points_to_the_validator(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "cases.jsonl"
    broken.write_text("{not json\n", encoding="utf-8")
    assert cli.main(["run", "GC-0001", "--golden", str(broken)], messages_api=_script()) == 2
    assert "make validate-golden" in capsys.readouterr().err


def test_an_id_that_is_neither_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["run", "case-1"], messages_api=_script()) == 2
    assert "golden case ID like GC-0001" in capsys.readouterr().err
