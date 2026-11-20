"""Cost per call and per run, checked against figures Langfuse computed independently."""

from __future__ import annotations

from decimal import Decimal

import pytest
from mcp import Client
from scripted import ScriptedMessages, answer_text, message, tool_use

from incident_triage.agent.loop import run_triage
from incident_triage.agent.pricing import PRICES, VERIFIED, turn_cost
from incident_triage.server import build_server

# Trace e0d00d01efebd5211f43021d703a2921 (#6's live run, 2026-11-20): Langfuse's own cost per
# generation, from its own model prices, alongside the usage it recorded.
LANGFUSE_TRACE = [
    (
        {
            "input_tokens": 4,
            "output_tokens": 230,
            "cache_creation_input_tokens": 2398,
            "cache_read_input_tokens": 0,
        },
        "0.016606",
    ),
    (
        {
            "input_tokens": 2,
            "output_tokens": 171,
            "cache_creation_input_tokens": 2998,
            "cache_read_input_tokens": 2398,
        },
        "0.0188976",
    ),
    (
        {
            "input_tokens": 2,
            "output_tokens": 521,
            "cache_creation_input_tokens": 2300,
            "cache_read_input_tokens": 5396,
        },
        "0.0230072",
    ),
]


@pytest.mark.parametrize(("usage", "langfuse_cost"), LANGFUSE_TRACE)
def test_opus_5_5_matches_langfuses_own_pricing(usage: dict[str, int], langfuse_cost: str) -> None:
    assert turn_cost(model="claude-opus-5-5", **usage) == Decimal(langfuse_cost)


def test_each_token_kind_has_its_own_rate() -> None:
    def one(kind: str) -> Decimal | None:
        counts = dict.fromkeys(
            (
                "input_tokens",
                "output_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            ),
            0,
        )
        return turn_cost(model="claude-opus-5-5", **(counts | {kind: 1_000_000}))

    assert (one("input_tokens"), one("output_tokens")) == (Decimal(4), Decimal(20))
    assert (one("cache_creation_input_tokens"), one("cache_read_input_tokens")) == (
        Decimal(5),
        Decimal("0.20"),
    )


def test_unknown_model_is_unpriced_not_guessed() -> None:
    assert (
        turn_cost(
            model="claude-unknown",
            input_tokens=1,
            output_tokens=1,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        is None
    )


def test_haiku_over_100k_tier_is_unpriced() -> None:
    assert (
        turn_cost(
            model="claude-haiku-5-5",
            input_tokens=100_001,
            output_tokens=0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        is None
    )


def test_table_is_dated() -> None:
    assert VERIFIED == "2026-11-20"
    assert "claude-opus-5-5" in PRICES


ANSWER = answer_text("expired_credential", "Cert expired.", ["TKT-0002"])


@pytest.mark.asyncio
async def test_a_run_records_each_turn_and_its_total_cost() -> None:
    script = ScriptedMessages(
        message(
            [tool_use("tu_1", "get_ticket", ticket_id="TKT-0002")],
            "tool_use",
            input_tokens=4,
            output_tokens=230,
        ),
        message([ANSWER], "end_turn", input_tokens=2, output_tokens=171, cache_read=2398),
    )
    async with Client(build_server()) as mcp:
        record = await run_triage("INC-0002", mcp=mcp, messages_api=script)
    assert [t.turn for t in record.turn_records] == [1, 2]
    assert record.turn_records[1].cache_read_input_tokens == 2398
    first = Decimal(4 * 4 + 230 * 20) / 1_000_000
    second = Decimal(2 * 4 + 171 * 20) / 1_000_000 + Decimal(2398) * Decimal("0.20") / 1_000_000
    assert record.cost_usd == float(round(first + second, 6))
    assert record.prices_verified == VERIFIED


@pytest.mark.asyncio
async def test_a_fallback_turn_is_priced_at_the_model_that_served_it() -> None:
    script = ScriptedMessages(
        message(
            [tool_use("tu_1", "get_ticket", ticket_id="TKT-0002")],
            "tool_use",
            input_tokens=0,
            output_tokens=0,
        ),
        message(
            [ANSWER], "end_turn", model="claude-opus-4-8", input_tokens=1_000_000, output_tokens=0
        ),
    )
    async with Client(build_server()) as mcp:
        record = await run_triage("INC-0002", mcp=mcp, messages_api=script)
    assert record.turn_records[1].cost_usd == 5.0  # Opus 4.8's $5 input, not Opus 5.5's $4
    assert record.cost_usd == 5.0


@pytest.mark.asyncio
async def test_an_unpriced_turn_makes_the_run_cost_unknown_and_says_why() -> None:
    script = ScriptedMessages(message([ANSWER], "end_turn", model="claude-mystery-1"))
    async with Client(build_server()) as mcp:
        record = await run_triage("INC-0002", mcp=mcp, messages_api=script)
    assert record.outcome == "answered"
    assert record.cost_usd is None
    assert record.detail is not None and "no price for claude-mystery-1" in record.detail
