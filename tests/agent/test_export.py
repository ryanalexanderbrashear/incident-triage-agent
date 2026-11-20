"""Trace export: clean by construction, and refused rather than written if a key survives."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from incident_triage.agent import cli
from incident_triage.agent import export as export_module
from incident_triage.agent.export import ExportError, build_export, export_trace
from incident_triage.agent.tracing import REDACTED

TRACE_ID = "e0d00d01efebd5211f43021d703a2921"
# Deliberately fake, key-shaped values: the export must strip or redact them.
FAKE_PUBLIC = "pk-lf-00000000-0000-0000-0000-000000000000"  # gitleaks:allow
FAKE_ANTHROPIC = "sk-ant-api03-FAKEFAKEFAKEFAKEFAKE"  # gitleaks:allow


def _observation(name: str, start: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": f"obs-{name}",
        "traceId": TRACE_ID,
        "projectId": "account-identifying-project-id",
        "name": name,
        "startTime": start,
        "type": "SPAN",
        "metadata": {
            "case_id": "INC-0001",
            "scope.attributes.public_key": FAKE_PUBLIC,
            "scope.name": "langfuse-sdk",
            "resourceAttributes.service.instance.id": "process-id",
        },
        **extra,
    }


@dataclass
class _Page:
    data: list[dict[str, Any]]
    meta: Any = None


@dataclass
class FakeObservations:
    pages: list[list[dict[str, Any]]]
    calls: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    def get_many(self, **kwargs: Any) -> _Page:
        self.calls.append(kwargs)
        index = int(kwargs["cursor"] or 0)
        nxt = str(index + 1) if index + 1 < len(self.pages) else None
        return _Page(self.pages[index], meta=type("Meta", (), {"cursor": nxt})())


def _document(api: FakeObservations) -> dict[str, Any]:
    return json.loads(export_trace(api, TRACE_ID))


def test_sdk_metadata_and_project_id_are_stripped_and_run_metadata_kept() -> None:
    doc = _document(FakeObservations([[_observation("root", "2026-11-20T00:00:00Z")]]))
    [obs] = doc["observations"]
    assert "projectId" not in obs
    assert obs["metadata"] == {"case_id": "INC-0001"}
    assert FAKE_PUBLIC not in json.dumps(doc)


def test_key_shaped_content_is_redacted() -> None:
    leaky = _observation("turn 1", "2026-11-20T00:00:01Z", output=f"token={FAKE_ANTHROPIC}")
    doc = _document(FakeObservations([[leaky]]))
    assert doc["observations"][0]["output"] == f"token={REDACTED}"


def test_observations_are_sorted_by_start_time_and_counted() -> None:
    api = FakeObservations(
        [[_observation("b", "2026-11-20T00:00:02Z"), _observation("a", "2026-11-20T00:00:01Z")]]
    )
    doc = _document(api)
    assert [o["name"] for o in doc["observations"]] == ["a", "b"]
    assert doc["observation_count"] == 2 and doc["trace_id"] == TRACE_ID


def test_every_page_is_fetched_with_every_field_group() -> None:
    api = FakeObservations([[_observation("a", "1")], [_observation("b", "2")]])
    doc = _document(api)
    assert doc["observation_count"] == 2
    assert all(call["fields"] == export_module.FIELDS for call in api.calls)
    assert "parse_io_as_json" not in api.calls[0], "the v2 endpoint rejects parseIoAsJson"


def test_an_empty_trace_is_an_error() -> None:
    with pytest.raises(ExportError, match="no observations"):
        build_export(TRACE_ID, [])


def test_it_refuses_to_write_if_a_key_survives_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export_module, "redact", lambda *, data, **_: data)  # redaction broken
    leaky = _observation("turn 1", "1", output=f"token={FAKE_ANTHROPIC}")
    with pytest.raises(ExportError, match="refusing to write"):
        build_export(TRACE_ID, [leaky], exported_at=datetime(2026, 11, 20, tzinfo=UTC))


def test_cli_export_writes_the_file(tmp_path: Any, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "trace.json"
    api = FakeObservations([[_observation("root", "1")]])
    assert cli.main(["trace", "export", TRACE_ID, "--out", str(out)], observations_api=api) == 0
    assert json.loads(out.read_text())["trace_id"] == TRACE_ID
    assert capsys.readouterr().out == ""


def test_cli_export_without_keys_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["trace", "export", TRACE_ID]) == 2
    assert "LANGFUSE_PUBLIC_KEY" in capsys.readouterr().err


def test_cli_export_rejects_a_malformed_trace_id(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["trace", "export", "not-a-trace"]) == 2
    assert "not a trace ID" in capsys.readouterr().err


def test_cli_export_failure_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["trace", "export", TRACE_ID], observations_api=FakeObservations([[]])) == 1
    assert "no observations" in capsys.readouterr().err
