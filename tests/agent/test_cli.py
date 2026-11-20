"""The `triage` command: exit codes, stdout, and never leaking credentials."""

from __future__ import annotations

import json

import pytest
from scripted import ScriptedMessages, answer_text, message, tool_use

from incident_triage.agent import cli
from incident_triage.server import build_server

SECRET = "sk-ant-test-not-a-real-key-0123456789"


def test_no_credentials_exits_2_with_instructions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in cli.CREDENTIAL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    assert cli.main(["run", "INC-0001"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ANTHROPIC_API_KEY" in captured.err and "--env-file .env" in captured.err


@pytest.mark.parametrize("bad", ["inc-0001", "INC-1", "TKT-0001"])
def test_malformed_incident_id_exits_2(bad: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["run", bad], messages_api=ScriptedMessages()) == 2
    assert "not an incident ID" in capsys.readouterr().err


def test_unknown_incident_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        cli.main(["run", "INC-0099"], messages_api=ScriptedMessages(), server=build_server()) == 2
    )
    captured = capsys.readouterr()
    assert captured.out == "" and "unknown incident_id INC-0099" in captured.err


def test_a_run_prints_its_record_as_json_and_exits_0(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    script = ScriptedMessages(
        message([answer_text("expired_credential", "Cert expired.", ["LOG-0002-0010"])], "end_turn")
    )
    assert cli.main(["run", "INC-0002"], messages_api=script, server=build_server()) == 0
    captured = capsys.readouterr()
    record = json.loads(captured.out)
    assert record["outcome"] == "answered"
    assert record["answer"]["root_cause_category"] == "expired_credential"
    assert SECRET not in captured.out and SECRET not in captured.err


def test_a_run_without_an_answer_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    script = ScriptedMessages(message([], "refusal"))
    assert cli.main(["run", "INC-0002"], messages_api=script, server=build_server()) == 1
    assert json.loads(capsys.readouterr().out)["outcome"] == "refused"


def test_the_default_path_spawns_incident_mcp_over_stdio(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Only the model is scripted: the server is the real one, spawned as a subprocess."""
    script = ScriptedMessages(
        message([tool_use("tu_1", "get_ticket", ticket_id="TKT-0002")], "tool_use"),
        message([answer_text("expired_credential", "Cert expired.", ["TKT-0002"])], "end_turn"),
    )
    assert cli.main(["run", "INC-0002"], messages_api=script) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["tool_calls"][0] | {"duration_ms": 0} == {
        "turn": 1,
        "name": "get_ticket",
        "arguments": {"ticket_id": "TKT-0002"},
        "outcome": "ok",
        "duration_ms": 0,
    }
    result = script.requests[1]["messages"][-1]["content"][0]
    assert '"title":"Merchant payouts not arriving"' in result["content"]


def test_a_traced_run_prints_its_trace_id_and_exports_before_exiting(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import uuid

    from langfuse import Langfuse
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    client = Langfuse(
        public_key=f"pk-lf-test-{uuid.uuid4().hex}",
        secret_key="sk-lf-test-not-a-real-key",
        base_url="http://127.0.0.1:9",
        span_exporter=exporter,
        tracer_provider=TracerProvider(),
    )
    script = ScriptedMessages(
        message([answer_text("expired_credential", "Cert expired.", ["TKT-0002"])], "end_turn")
    )
    code = cli.main(
        ["run", "INC-0002"],
        messages_api=script,
        server=build_server(),
        langfuse_factory=lambda: client,
    )
    assert code == 0
    captured = capsys.readouterr()
    trace_id = json.loads(captured.out)["trace_id"]
    assert trace_id and len(trace_id) == 32
    # Exported by the CLI itself before main() returned: this test never flushes.
    exported: set[str] = set()
    for span in exporter.get_finished_spans():
        assert span.context is not None
        exported.add(format(span.context.trace_id, "032x"))
    assert exported == {trace_id}
    events = [json.loads(line) for line in captured.err.splitlines() if line.startswith("{")]
    assert any(e.get("event") == "trace" and e.get("trace_id") == trace_id for e in events)


def test_without_langfuse_keys_the_run_is_untraced_and_says_so(
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = ScriptedMessages(
        message([answer_text("expired_credential", "Cert expired.", ["TKT-0002"])], "end_turn")
    )
    assert cli.main(["run", "INC-0002"], messages_api=script, server=build_server()) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["trace_id"] is None
    assert '"event": "tracing_disabled"' in captured.err
