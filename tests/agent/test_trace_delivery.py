"""#47: a run never claims a trace that was not delivered, and an incomplete config says so.

A local stub HTTP server stands in for Langfuse's ingestion endpoint, so the real OTLP exporter
does the sending. The stub answering 200 is the control: it proves the path works, so the 401
test cannot pass because nothing was sent.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from scripted import ScriptedMessages, answer_text, message

from incident_triage.agent import cli, tracing
from incident_triage.agent.tracing import (
    langfuse_client,
    langfuse_config_problem,
    make_langfuse,
    uses_deprecated_host,
)
from incident_triage.server import build_server


class _Stub:
    def __init__(self, status: int) -> None:
        self.status = status
        self.paths: list[str] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                stub.paths.append(self.path)
                self.send_response(stub.status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = do_POST

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()


@pytest.fixture
def stub_factory() -> Iterator[Any]:
    stubs: list[_Stub] = []

    def make(status: int) -> _Stub:
        stubs.append(_Stub(status))
        return stubs[-1]

    yield make
    for stub in stubs:
        stub.close()


def _run_cli(
    stub: _Stub, capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict[str, Any], list[dict[str, Any]]]:
    client = langfuse_client(
        public_key=f"pk-lf-test-{uuid.uuid4().hex}",
        secret_key="sk-lf-test-not-a-real-key",
        base_url=stub.url,
        tracer_provider=TracerProvider(),  # the real OTLP exporter, no span_exporter override
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
    captured = capsys.readouterr()
    record = json.loads(captured.out)
    events = [json.loads(line) for line in captured.err.splitlines() if line.startswith("{")]
    return code, record, events


def _events(events: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [e for e in events if e.get("event") == name]


def test_control_a_delivered_trace_keeps_its_id(
    stub_factory: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    stub = stub_factory(200)
    code, record, events = _run_cli(stub, capsys)
    assert code == 0
    assert any(path.endswith("/api/public/otel/v1/traces") for path in stub.paths), stub.paths
    assert record["trace_id"] and record["trace_error"] is None
    assert _events(events, "trace") and not _events(events, "trace_export_failed")


def test_a_rejected_trace_is_not_claimed(
    stub_factory: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    stub = stub_factory(401)
    code, record, events = _run_cli(stub, capsys)
    assert any(path.endswith("/api/public/otel/v1/traces") for path in stub.paths), stub.paths
    assert code == 0, "tracing never decides a run's outcome"
    assert record["outcome"] == "answered"
    assert record["trace_id"] is None
    assert "401" in record["trace_error"]
    [failed] = _events(events, "trace_export_failed")
    assert failed["level"] == "WARNING" and "401" in failed["error"]
    assert not _events(events, "trace"), "no trace line (or link) for an undelivered trace"


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_BASE_URL",
        "LANGFUSE_HOST",
    ):
        monkeypatch.delenv(name, raising=False)


def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", f"pk-lf-test-{uuid.uuid4().hex}")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test-not-a-real-key")


def test_keys_without_a_url_are_untraced_and_say_why(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _clear(monkeypatch)
    _keys(monkeypatch)
    assert langfuse_config_problem() == "LANGFUSE_BASE_URL not set"
    assert make_langfuse() is None
    script = ScriptedMessages(message([answer_text("expired_credential", "x", [])], "end_turn"))
    assert cli.main(["run", "INC-0002"], messages_api=script, server=build_server()) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["trace_id"] is None
    [disabled] = [
        json.loads(line) for line in captured.err.splitlines() if '"tracing_disabled"' in line
    ]
    assert disabled["reason"] == "LANGFUSE_BASE_URL not set"


def test_missing_keys_are_named(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")
    assert langfuse_config_problem() == "LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY not set"


def test_the_deprecated_host_variable_still_traces(
    stub_factory: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stub = stub_factory(200)
    _clear(monkeypatch)
    _keys(monkeypatch)
    monkeypatch.setenv("LANGFUSE_HOST", stub.url)
    # The production path (make_langfuse → langfuse_client → the SDK reading the environment), but
    # with a private TracerProvider: otherwise Langfuse registers the process-wide provider and
    # later tests' spans would try to export to this stub after it closes.
    real_langfuse = tracing.Langfuse
    monkeypatch.setattr(
        tracing, "Langfuse", lambda **kw: real_langfuse(tracer_provider=TracerProvider(), **kw)
    )
    assert langfuse_config_problem() is None and uses_deprecated_host()
    script = ScriptedMessages(message([answer_text("expired_credential", "x", [])], "end_turn"))
    assert cli.main(["run", "INC-0002"], messages_api=script, server=build_server()) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["trace_id"]
    assert '"tracing_note"' in captured.err and "LANGFUSE_HOST is deprecated" in captured.err
    assert any(path.endswith("/api/public/otel/v1/traces") for path in stub.paths)


def test_trace_export_uses_the_same_rule(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _clear(monkeypatch)
    _keys(monkeypatch)
    assert cli.main(["trace", "export", "0" * 32]) == 2
    assert "LANGFUSE_BASE_URL not set" in capsys.readouterr().err
