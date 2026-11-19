"""The entry point refuses to serve invalid fixtures, and says why on stderr."""

from __future__ import annotations

import json

import pytest

from incident_triage.fixtures import FixtureError
from incident_triage.server import __main__ as entry


def test_invalid_fixtures_exit_2_with_the_location(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken() -> None:
        raise FixtureError("incidents/INC-0001/logs.jsonl", "invalid JSON", line=7)

    monkeypatch.setattr(entry, "load_fixtures", broken)
    assert entry.main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    event = json.loads(captured.err.strip().splitlines()[-1])
    assert event["event"] == "fixtures_invalid"
    assert event["level"] == "ERROR"
    assert event["error"] == "incidents/INC-0001/logs.jsonl:7: -: invalid JSON"
