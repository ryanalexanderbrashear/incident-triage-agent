"""What a Messages API call costs, from a pinned and dated price table (ADR-002, Cost).

Prices change. When they do, update PRICES, VERIFIED and docs/cost.md together. A model missing
from the table prices as None, never as a guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing.md"
VERIFIED = "2026-11-20"
_PER_TOKEN = Decimal(1_000_000)


@dataclass(frozen=True)
class Price:
    """USD per million tokens. The loop uses the default 5-minute cache TTL."""

    input: Decimal
    output: Decimal
    cache_write_5m: Decimal
    cache_read: Decimal


PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(Decimal("4"), Decimal("20"), Decimal("5"), Decimal("0.20")),
    # Possible refusal-fallback models: a turn is priced at the model that served it.
    "claude-opus-5": Price(Decimal("5"), Decimal("25"), Decimal("6.25"), Decimal("0.50")),
    "claude-opus-4-8": Price(Decimal("5"), Decimal("25"), Decimal("6.25"), Decimal("0.50")),
    "claude-sonnet-5-5": Price(Decimal("2"), Decimal("10"), Decimal("2.50"), Decimal("0.10")),
    # Prompts up to 100,000 tokens; longer Haiku 5.5 prompts cost more and are not priced here.
    "claude-haiku-5-5": Price(Decimal("0.10"), Decimal("0.50"), Decimal("0.125"), Decimal("0.01")),
}


def turn_cost(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int,
    cache_read_input_tokens: int,
) -> Decimal | None:
    """One call's cost in USD, or None for a model not in the table.

    Token counts are in the Messages API's own semantics: `input_tokens` excludes cache reads and
    writes, which are counted separately.
    """
    price = PRICES.get(model)
    if price is None:
        return None
    if model == "claude-haiku-5-5" and (
        input_tokens + cache_creation_input_tokens + cache_read_input_tokens > 100_000
    ):
        return None  # the over-100K tier is not in the table
    return (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_creation_input_tokens * price.cache_write_5m
        + cache_read_input_tokens * price.cache_read
    ) / _PER_TOKEN
