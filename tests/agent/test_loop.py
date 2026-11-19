"""The tool-use loop, with a scripted model against the real in-process server."""

from __future__ import annotations

import itertools
from typing import Any

import pytest
from mcp import Client
from scripted import (
    ScriptedMessages,
    answer_text,
    message,
    text,
    thinking,
    tool_use,
)

from incident_triage.agent.answer import ANSWER_SCHEMA
from incident_triage.agent.loop import FALLBACK_BETA, RunRecord, run_triage
from incident_triage.agent.prompts import SYSTEM_PROMPT
from incident_triage.server import build_server

ANSWER = answer_text("expired_credential", "The upstream certificate expired.", ["LOG-0002-0010"])


async def _run(script: ScriptedMessages, incident_id: str = "INC-0002", **kwargs: Any) -> RunRecord:
    async with Client(build_server()) as mcp:
        return await run_triage(incident_id, mcp=mcp, messages_api=script, **kwargs)


@pytest.mark.asyncio
async def test_one_tool_round_then_an_answer() -> None:
    script = ScriptedMessages(
        message(
            [
                thinking(),
                tool_use("tu_1", "search_logs", incident_id="INC-0002", query="certificate"),
            ],
            "tool_use",
        ),
        message([ANSWER], "end_turn"),
    )
    record = await _run(script)
    assert record.outcome == "answered"
    assert record.answer is not None and record.answer.evidence_ids == ["LOG-0002-0010"]
    assert (record.turns, len(record.tool_calls)) == (2, 1)
    assert record.tool_calls[0].outcome == "ok"
    assert record.served_by == ["claude-opus-5-5", "claude-opus-5-5"]
    assert record.usage.input_tokens == 200 and record.usage.output_tokens == 40


@pytest.mark.asyncio
async def test_parallel_calls_are_all_answered_in_one_user_message() -> None:
    script = ScriptedMessages(
        message(
            [
                tool_use("tu_1", "search_logs", incident_id="INC-0002", level="ERROR"),
                tool_use("tu_2", "get_ticket", ticket_id="TKT-0002"),
                tool_use("tu_3", "get_runbook", runbook_id="RB-TLS-CERT"),
            ],
            "tool_use",
        ),
        message([ANSWER], "end_turn"),
    )
    await _run(script)
    last = script.requests[1]["messages"][-1]
    assert last["role"] == "user"
    assert [block["tool_use_id"] for block in last["content"]] == ["tu_1", "tu_2", "tu_3"]
    assert len(script.requests[1]["messages"]) == 3  # overview, assistant, all three results


@pytest.mark.asyncio
async def test_history_is_append_only_with_thinking_blocks_unchanged() -> None:
    script = ScriptedMessages(
        message(
            [thinking("first"), tool_use("tu_1", "get_ticket", ticket_id="TKT-0002")], "tool_use"
        ),
        message(
            [thinking("second"), tool_use("tu_2", "get_runbook", runbook_id="RB-TLS-CERT")],
            "tool_use",
        ),
        message([thinking("third"), ANSWER], "end_turn"),
    )
    await _run(script)
    histories = [request["messages"] for request in script.requests]
    for earlier, later in itertools.pairwise(histories):
        assert later[: len(earlier)] == earlier, "an earlier turn was edited"
    replayed = histories[2][1]["content"][0]
    assert replayed == {"type": "thinking", "thinking": "first", "signature": "sig-first"}


@pytest.mark.asyncio
async def test_every_request_has_the_fixed_shape() -> None:
    script = ScriptedMessages(
        message([tool_use("tu_1", "get_ticket", ticket_id="TKT-0002")], "tool_use"),
        message([ANSWER], "end_turn"),
    )
    await _run(script, effort="high", model="claude-opus-5-5")
    first, second = script.requests
    for request in (first, second):
        assert request["model"] == "claude-opus-5-5"
        assert request["system"] == SYSTEM_PROMPT
        assert request["output_config"] == {
            "effort": "high",
            "format": {"type": "json_schema", "schema": ANSWER_SCHEMA},
        }
        assert request["thinking"] == {"type": "adaptive", "display": "summarized"}
        assert request["cache_control"] == {"type": "ephemeral"}
        assert request["fallbacks"] == "default" and request["betas"] == [FALLBACK_BETA]
        assert "tool_choice" not in request, "forced tool choice is a 400 on Claude Opus 5.5"
        assert sorted(t["name"] for t in request["tools"]) == [
            "get_runbook",
            "get_ticket",
            "search_logs",
        ]
    # Only the messages change between turns, so the cached prefix stays valid.
    assert {k: v for k, v in first.items() if k != "messages"} == {
        k: v for k, v in second.items() if k != "messages"
    }
    opening = first["messages"][0]["content"]
    assert '"id":"INC-0002"' in opening and '"ticket_ids":["TKT-0002"]' in opening


@pytest.mark.asyncio
async def test_a_tool_error_is_returned_to_the_model_and_recorded() -> None:
    script = ScriptedMessages(
        message([tool_use("tu_1", "get_ticket", ticket_id="TKT-0099")], "tool_use"),
        message([ANSWER], "end_turn"),
    )
    record = await _run(script)
    result = script.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "unknown ticket_id TKT-0099" in result["content"]
    assert record.tool_calls[0].outcome == "tool_error"
    assert record.outcome == "answered"


@pytest.mark.asyncio
async def test_budget_exhaustion_warns_once_then_stops() -> None:
    call = lambda n: tool_use(f"tu_{n}", "get_ticket", ticket_id="TKT-0002")  # noqa: E731
    script = ScriptedMessages(
        message([call(1), call(2)], "tool_use"),
        message([call(3)], "tool_use"),  # over budget: gets the warning
        message([call(4)], "tool_use"),  # ignores it: run stops
    )
    record = await _run(script, tool_budget=2)
    warning = script.requests[2]["messages"][-1]["content"][0]
    assert warning["is_error"] is True and "tool budget of 2 calls exhausted" in warning["content"]
    assert [c.outcome for c in record.tool_calls] == ["ok", "ok", "over_budget"]
    assert record.outcome == "budget_exhausted" and record.answer is None


@pytest.mark.asyncio
async def test_an_answer_after_the_budget_warning_still_counts() -> None:
    call = lambda n: tool_use(f"tu_{n}", "get_ticket", ticket_id="TKT-0002")  # noqa: E731
    script = ScriptedMessages(
        message([call(1)], "tool_use"),
        message([call(2)], "tool_use"),
        message([ANSWER], "end_turn"),
    )
    record = await _run(script, tool_budget=1)
    assert record.outcome == "answered"


@pytest.mark.asyncio
async def test_refusal_records_its_category() -> None:
    script = ScriptedMessages(
        message(
            [],
            "refusal",
            stop_details={"type": "refusal", "category": "cyber", "explanation": None},
        )
    )
    record = await _run(script)
    assert (record.outcome, record.refusal_category, record.answer) == ("refused", "cyber", None)


@pytest.mark.asyncio
async def test_max_tokens_is_truncated() -> None:
    record = await _run(ScriptedMessages(message([text('{"root_cause')], "max_tokens")))
    assert record.outcome == "truncated" and record.answer is None


@pytest.mark.asyncio
async def test_turn_limit() -> None:
    call = lambda n: tool_use(f"tu_{n}", "get_ticket", ticket_id="TKT-0002")  # noqa: E731
    record = await _run(
        ScriptedMessages(message([call(1)], "tool_use"), message([call(2)], "tool_use")),
        turn_limit=2,
    )
    assert (record.outcome, record.turns) == ("turn_limit", 2)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "final",
    [
        text("the certificate expired"),
        answer_text("dns_outage", "x", []),
        answer_text("expired_credential", "x", ["see the logs"]),
    ],
    ids=["not json", "unknown category", "prose evidence id"],
)
async def test_an_answer_that_fails_validation_is_invalid(final: dict[str, Any]) -> None:
    record = await _run(ScriptedMessages(message([final], "end_turn")))
    assert record.outcome == "invalid_answer" and record.answer is None
    assert record.detail


@pytest.mark.asyncio
async def test_a_fallback_turn_is_visible_in_served_by() -> None:
    script = ScriptedMessages(
        message([tool_use("tu_1", "get_ticket", ticket_id="TKT-0002")], "tool_use"),
        message([ANSWER], "end_turn", model="claude-opus-4-8"),
    )
    record = await _run(script)
    assert record.served_by == ["claude-opus-5-5", "claude-opus-4-8"]
