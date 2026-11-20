"""One run is one trace. Spans are captured in memory: no network, no keys.

Each test gets its own Langfuse client (Langfuse caches one per public key), its own
TracerProvider (so nothing depends on the process-global one), and an in-memory exporter.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from langfuse import Langfuse
from langfuse import LangfuseOtelSpanAttributes as A
from mcp import Client
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from scripted import ScriptedMessages, answer_text, message, thinking, tool_use

from incident_triage.agent.loop import MAX_TOKENS, RunRecord, run_triage
from incident_triage.agent.tracing import (
    REDACTED,
    git_sha,
    langfuse_client,
    redact,
    trace_attributes,
)
from incident_triage.server import build_server

ANSWER = answer_text("expired_credential", "The certificate expired.", ["LOG-0002-0010"])
SHA = "0123456789abcdef0123456789abcdef01234567-dirty"


@dataclass
class Traced:
    client: Langfuse
    exporter: InMemorySpanExporter

    def spans(self) -> list[ReadableSpan]:
        self.client.flush()
        return list(self.exporter.get_finished_spans())


@pytest.fixture
def traced() -> Iterator[Traced]:
    exporter = InMemorySpanExporter()
    client = langfuse_client(  # the production constructor, so its mask is what's tested
        public_key=f"pk-lf-test-{uuid.uuid4().hex}",
        secret_key="sk-lf-test-not-a-real-key",
        base_url="http://127.0.0.1:9",  # nothing may be sent; an accidental request fails fast
        span_exporter=exporter,
        tracer_provider=TracerProvider(),
    )
    yield Traced(client, exporter)
    client.shutdown()


async def _run(script: ScriptedMessages, traced: Traced, **kwargs: Any) -> RunRecord:
    async with Client(build_server()) as mcp:
        return await run_triage(
            "INC-0002", mcp=mcp, messages_api=script, langfuse=traced.client, git_sha=SHA, **kwargs
        )


def _ours(spans: list[ReadableSpan]) -> list[ReadableSpan]:
    """Langfuse observations only; the mcp SDK's own `tools/call` spans are left out."""
    return [s for s in spans if s.attributes and A.OBSERVATION_TYPE in s.attributes]


def _ctx(span: ReadableSpan) -> Any:
    assert span.context is not None
    return span.context


def _attr(span: ReadableSpan, name: str) -> Any:
    assert span.attributes is not None
    return span.attributes.get(name)


def _json(span: ReadableSpan, name: str) -> Any:
    value = _attr(span, name)
    return json.loads(value) if isinstance(value, str) else value


def _two_turn_script(*first_turn: dict[str, Any]) -> ScriptedMessages:
    return ScriptedMessages(
        message([thinking("look at TLS errors"), *first_turn], "tool_use", cache_read=40),
        message([ANSWER], "end_turn", input_tokens=7, output_tokens=30, cache_read=900),
    )


@pytest.mark.asyncio
async def test_one_run_is_one_trace_with_an_agent_root(traced: Traced) -> None:
    record = await _run(
        _two_turn_script(tool_use("tu_1", "get_ticket", ticket_id="TKT-0002")), traced
    )
    spans = _ours(traced.spans())
    assert len({_ctx(span).trace_id for span in spans}) == 1
    [root] = [s for s in spans if s.parent is None]
    assert _attr(root, A.OBSERVATION_TYPE) == "agent" and root.name == "triage INC-0002"
    assert record.trace_id == format(_ctx(root).trace_id, "032x")
    ids = {_ctx(s).span_id for s in spans}
    assert all(s.parent is None or s.parent.span_id in ids for s in spans), "a span is orphaned"
    assert _json(root, A.OBSERVATION_OUTPUT)["root_cause_category"] == "expired_credential"
    # A string payload is stored as the raw string: the overview JSON exactly as the model got it.
    assert '"id":"INC-0002"' in str(_attr(root, A.OBSERVATION_INPUT))


@pytest.mark.asyncio
async def test_one_generation_per_turn_with_model_and_api_usage(traced: Traced) -> None:
    await _run(_two_turn_script(tool_use("tu_1", "get_ticket", ticket_id="TKT-0002")), traced)
    generations = sorted(
        (s for s in _ours(traced.spans()) if _attr(s, A.OBSERVATION_TYPE) == "generation"),
        key=lambda s: s.name,
    )
    assert [g.name for g in generations] == ["turn 1", "turn 2"]
    first, second = generations
    assert _attr(first, A.OBSERVATION_MODEL) == "claude-opus-5-5"
    assert _json(first, A.OBSERVATION_MODEL_PARAMETERS) == {
        "effort": "medium",
        "max_tokens": MAX_TOKENS,
    }
    assert _json(second, A.OBSERVATION_USAGE_DETAILS) == {
        "input": 7,
        "output": 30,
        "cache_read_input_tokens": 900,
        "cache_creation_input_tokens": 0,
    }
    # Input is the full history sent; output is the response content, thinking summary included.
    assert len(_json(second, A.OBSERVATION_INPUT)) == 3
    assert _json(first, A.OBSERVATION_OUTPUT)[0] == {
        "type": "thinking",
        "thinking": "look at TLS errors",
        "signature": "sig-look at ",
    }


@pytest.mark.asyncio
async def test_tool_observations_show_exactly_what_the_model_was_given(traced: Traced) -> None:
    script = _two_turn_script(
        tool_use("tu_1", "get_ticket", ticket_id="TKT-0002"),
        tool_use("tu_2", "get_runbook", runbook_id="RB-TLS-CERT"),
    )
    await _run(script, traced)
    tools = {s.name: s for s in _ours(traced.spans()) if _attr(s, A.OBSERVATION_TYPE) == "tool"}
    assert set(tools) == {"get_ticket", "get_runbook"}
    sent = {
        block["tool_use_id"]: block["content"]
        for block in script.requests[1]["messages"][-1]["content"]
    }
    assert _json(tools["get_ticket"], A.OBSERVATION_INPUT) == {"ticket_id": "TKT-0002"}
    assert _attr(tools["get_ticket"], A.OBSERVATION_OUTPUT) == sent["tu_1"]
    assert _attr(tools["get_runbook"], A.OBSERVATION_OUTPUT) == sent["tu_2"]
    assert all(_attr(t, A.OBSERVATION_LEVEL) in (None, "DEFAULT") for t in tools.values())


@pytest.mark.asyncio
async def test_levels_mark_tool_errors_over_budget_calls_and_fallbacks(traced: Traced) -> None:
    script = ScriptedMessages(
        message([tool_use("tu_1", "get_ticket", ticket_id="TKT-0099")], "tool_use"),
        message([tool_use("tu_2", "get_ticket", ticket_id="TKT-0002")], "tool_use"),
        message([ANSWER], "end_turn", model="claude-opus-4-8"),
    )
    await _run(script, traced, tool_budget=1)
    spans = _ours(traced.spans())
    tools = [s for s in spans if _attr(s, A.OBSERVATION_TYPE) == "tool"]
    levels = sorted(
        (_attr(t, A.OBSERVATION_LEVEL), _attr(t, A.OBSERVATION_STATUS_MESSAGE)) for t in tools
    )
    assert levels == [("ERROR", "tool error"), ("WARNING", "over budget: not run")]
    [turn3] = [s for s in spans if s.name == "turn 3"]
    assert _attr(turn3, A.OBSERVATION_LEVEL) == "WARNING"
    assert "claude-opus-4-8" in str(_attr(turn3, A.OBSERVATION_STATUS_MESSAGE))


@pytest.mark.asyncio
async def test_a_run_without_an_answer_marks_the_root_as_an_error(traced: Traced) -> None:
    await _run(ScriptedMessages(message([], "refusal")), traced)
    [root] = [s for s in _ours(traced.spans()) if s.parent is None]
    assert _attr(root, A.OBSERVATION_LEVEL) == "ERROR"
    assert str(_attr(root, A.OBSERVATION_STATUS_MESSAGE)).startswith("refused")


@pytest.mark.asyncio
async def test_case_model_effort_and_git_sha_are_on_every_span(traced: Traced) -> None:
    await _run(
        _two_turn_script(tool_use("tu_1", "get_ticket", ticket_id="TKT-0002")),
        traced,
        effort="high",
    )
    # With a test-owned (non-global) provider only our observations are here; in the CLI, where
    # Langfuse is the global provider, the mcp SDK's tools/call spans carry the same attributes.
    spans = traced.spans()
    assert len(spans) == 4  # root, two generations, one tool
    for span in spans:
        assert _attr(span, A.TRACE_NAME) == "triage", span.name
        tags = list(_attr(span, A.TRACE_TAGS) or [])
        assert tags == [
            "case:INC-0002",
            "model:claude-opus-5-5",
            "effort:high",
            "git:0123456789ab",
        ], span.name
        assert json.loads(str(_attr(span, f"{A.TRACE_METADATA}.git_sha"))) == SHA, span.name


@pytest.mark.asyncio
async def test_a_secret_shaped_string_in_a_tool_result_is_redacted(
    traced: Traced, monkeypatch: pytest.MonkeyPatch
) -> None:
    planted = "sk-ant-api03-PLANTEDTESTVALUE0123456789"
    from incident_triage.agent import loop

    real = loop.to_tool_result

    def leaky(tool_use_id: str, result: Any) -> Any:
        block = real(tool_use_id, result)
        return block | {"content": f"{block.get('content')} token={planted}"}

    monkeypatch.setattr(loop, "to_tool_result", leaky)
    await _run(_two_turn_script(tool_use("tu_1", "get_ticket", ticket_id="TKT-0002")), traced)
    exported = json.dumps([dict(s.attributes or {}) for s in traced.spans()], default=str)
    assert planted not in exported
    assert REDACTED in exported


@pytest.mark.asyncio
async def test_spans_are_exported_when_the_run_fails_midway(traced: Traced) -> None:
    script = ScriptedMessages(
        message([tool_use("tu_1", "get_ticket", ticket_id="TKT-0002")], "tool_use")
    )
    failed = False
    try:
        await _run(script, traced)  # the scripted model runs out on turn 2
    except* AssertionError:  # arrives wrapped in the MCP client's task group
        failed = True
    assert failed
    names = {s.name for s in _ours(traced.spans())}
    assert {"triage INC-0002", "turn 1", "get_ticket"} <= names


def test_redact_handles_nested_data_and_leaves_ordinary_text() -> None:
    # Deliberately fake, key-shaped values: this test checks that they are redacted.
    fake_public = "pk-lf-abcdef0123456789"  # gitleaks:allow
    fake_secret = "sk-lf-0123456789abcdef"  # gitleaks:allow
    data = {"a": ["x", {"key": fake_public}], "b": fake_secret, "c": 3}
    assert redact(data=data) == {"a": ["x", {"key": REDACTED}], "b": REDACTED, "c": 3}
    assert (
        redact(data="LOG-0001-0006 TKT-0001 sk-anthology") == "LOG-0001-0006 TKT-0001 sk-anthology"
    )


def test_trace_attributes_shape() -> None:
    attrs = trace_attributes(
        incident_id="INC-0001",
        model="m",
        effort="low",
        git="abcdef0123456789",
        tool_budget=12,
        turn_limit=20,
    )
    assert attrs["tags"] == ["case:INC-0001", "model:m", "effort:low", "git:abcdef012345"]
    assert attrs["metadata"]["git_sha"] == "abcdef0123456789"


def test_git_sha_outside_a_checkout_is_unknown(tmp_path: Any) -> None:
    assert git_sha(cwd=str(tmp_path)) == "unknown"


CANNED_MESSAGE = {
    "id": "msg_t",
    "type": "message",
    "role": "assistant",
    "model": "claude-opus-5-5",
    "content": [{"type": "text", "text": "{}"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 1, "output_tokens": 1},
}


@pytest.mark.asyncio
async def test_the_cli_client_emits_no_sdk_spans() -> None:
    """Control first: an SDK client with spans on does reach this exporter; the CLI's must not."""
    import httpx2
    from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

    from incident_triage.agent.cli import make_anthropic

    def http() -> Any:
        transport = httpx2.MockTransport(lambda _: httpx2.Response(200, json=CANNED_MESSAGE))
        return DefaultAsyncHttpxClient(transport=transport)

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    provider.add_span_processor(SimpleSpanProcessor(exporter))
    request = {
        "model": "claude-opus-5-5",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "x"}],
    }

    spans_on = AsyncAnthropic(
        api_key="sk-test", http_client=http(), open_telemetry={"tracer_provider": provider}
    )
    await spans_on.beta.messages.create(**request)
    assert [s.name for s in exporter.get_finished_spans()] == ["anthropic.messages.create"]

    exporter.clear()
    cli_client = make_anthropic(api_key="sk-test", http_client=http())
    # The SDK normalises open_telemetry=False to traces off (traceparent propagation stays on).
    options: Any = cli_client.open_telemetry
    assert options["traces"]["enabled"] is False
    await cli_client.beta.messages.create(**request)
    assert exporter.get_finished_spans() == ()
