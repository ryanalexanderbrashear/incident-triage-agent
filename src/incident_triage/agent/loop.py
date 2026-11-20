"""The hand-written tool-use loop (ADR-001, ADR-002).

History is append-only: each response's content is appended unchanged, thinking blocks
included, and nothing earlier is ever edited. Claude Opus 5.5's thinking blocks are bound to the
conversation that produced them, so an edited history would silently lose them.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, Literal, Protocol

from anthropic.types.beta import BetaMessage, BetaMessageParam, BetaToolResultBlockParam
from langfuse import Langfuse, propagate_attributes
from mcp import Client
from mcp.shared.exceptions import MCPError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from incident_triage.agent.answer import ANSWER_SCHEMA, TriageAnswer
from incident_triage.agent.bridge import error_result, to_anthropic_tool, to_tool_result
from incident_triage.agent.pricing import VERIFIED as PRICES_VERIFIED
from incident_triage.agent.pricing import turn_cost
from incident_triage.agent.prompts import SYSTEM_PROMPT, first_user_message
from incident_triage.agent.tracing import trace_attributes
from incident_triage.jsonlog import json_logger

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT: Effort = "medium"  # Claude Opus 5.5's default; set explicitly (ADR-002)
DEFAULT_TOOL_BUDGET = 12
DEFAULT_TURN_LIMIT = 20
MAX_TOKENS = 16_000
FALLBACK_BETA = "server-side-fallback-2026-07-01"

Effort = Literal["low", "medium", "high", "xhigh", "max"]
Outcome = Literal[
    "answered",
    "invalid_answer",
    "refused",
    "truncated",
    "budget_exhausted",
    "turn_limit",
    "unexpected_stop",
]

_log = json_logger("incident_triage.agent")


def log_event(event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    _log.log(level, event, extra={"fields": {"component": "agent", **fields}})


class MessagesAPI(Protocol):
    """The one call the loop makes: `client.beta.messages.create`. Injected so tests need no key."""

    async def create(self, **kwargs: Any) -> BetaMessage: ...


class ToolCallRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    turn: int
    name: str
    arguments: dict[str, Any]
    outcome: Literal["ok", "tool_error", "over_budget"]
    duration_ms: float


class TurnRecord(BaseModel):
    """One Messages API call, in the API's own usage semantics. Priced at its serving model."""

    model_config = ConfigDict(frozen=True)

    turn: int
    served_by: str
    stop_reason: str | None
    request_id: str | None
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    cost_usd: float | None  # None when served_by is not in the price table


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def add(self, message: BetaMessage) -> None:
        usage = message.usage
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cache_creation_input_tokens += usage.cache_creation_input_tokens or 0
        self.cache_read_input_tokens += usage.cache_read_input_tokens or 0


class RunRecord(BaseModel):
    """Everything a run did. #6 turns it into a trace; #7 prices it; graders read `answer`."""

    incident_id: str
    outcome: Outcome
    answer: TriageAnswer | None = None
    model: str
    effort: Effort
    served_by: list[str] = Field(default_factory=list[str])  # response.model, per turn
    turns: int = 0
    tool_calls: list[ToolCallRecord] = Field(default_factory=list[ToolCallRecord])
    stop_reason: str | None = None
    refusal_category: str | None = None
    detail: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    request_ids: list[str] = Field(default_factory=list[str])
    git_sha: str = "unknown"
    trace_id: str | None = None  # null when the run was not traced, or its export failed
    trace_error: str | None = None  # why the trace was not delivered, when export failed
    turn_records: list[TurnRecord] = Field(default_factory=list[TurnRecord])
    cost_usd: float | None = None  # sum of turn costs; None if any turn could not be priced
    prices_verified: str = PRICES_VERIFIED


def request_params(
    *,
    model: str,
    effort: Effort,
    tools: Sequence[Any],
    messages: Sequence[BetaMessageParam],
) -> dict[str, Any]:
    """Every request's parameters. Only `messages` varies between turns, so the prefix caches."""
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "tools": list(tools),
        "messages": list(messages),
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {
            "effort": effort,
            "format": {"type": "json_schema", "schema": ANSWER_SCHEMA},
        },
        "cache_control": {"type": "ephemeral"},
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
    }


async def run_triage(
    incident_id: str,
    *,
    mcp: Client,
    messages_api: MessagesAPI,
    model: str = DEFAULT_MODEL,
    effort: Effort = DEFAULT_EFFORT,
    tool_budget: int = DEFAULT_TOOL_BUDGET,
    turn_limit: int = DEFAULT_TURN_LIMIT,
    langfuse: Langfuse | None = None,
    git_sha: str = "unknown",
) -> RunRecord:
    """Triage one incident. Raises MCPError only if the incident's overview can't be read.

    With a Langfuse client, the run is one trace: an `agent` observation holding one
    `generation` per Messages API call and one `tool` observation per tool call. Without one,
    the run is untraced and otherwise identical.
    """
    record = RunRecord(
        incident_id=incident_id, outcome="turn_limit", model=model, effort=effort, git_sha=git_sha
    )
    if langfuse is None:
        return _priced(await _run(record, mcp, messages_api, tool_budget, turn_limit, None))
    attributes = trace_attributes(
        incident_id=incident_id,
        model=model,
        effort=effort,
        git=git_sha,
        tool_budget=tool_budget,
        turn_limit=turn_limit,
    )
    with (
        propagate_attributes(**attributes),
        langfuse.start_as_current_observation(
            name=f"triage {incident_id}", as_type="agent"
        ) as root,
    ):
        record.trace_id = langfuse.get_current_trace_id()
        _priced(await _run(record, mcp, messages_api, tool_budget, turn_limit, langfuse))
        answered = record.outcome == "answered"
        root.update(
            output=record.answer.model_dump(mode="json")
            if record.answer is not None
            else {"outcome": record.outcome, "detail": record.detail},
            level="DEFAULT" if answered else "ERROR",
            status_message=None
            if answered
            else f"{record.outcome}: {record.detail or ''}".strip(": "),
            metadata={
                "outcome": record.outcome,
                "turns": record.turns,
                "tool_calls": len(record.tool_calls),
                "served_by": sorted(set(record.served_by)),
                "usage": record.usage.model_dump(),
                "cost_usd": record.cost_usd,
            },
        )
    return record


def _turn_record(turn: int, response: BetaMessage, request_id: object) -> TurnRecord:
    usage = response.usage
    counts = {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_creation_input_tokens": usage.cache_creation_input_tokens or 0,
        "cache_read_input_tokens": usage.cache_read_input_tokens or 0,
    }
    cost = turn_cost(model=response.model, **counts)
    return TurnRecord(
        turn=turn,
        served_by=response.model,
        stop_reason=response.stop_reason,
        request_id=request_id if isinstance(request_id, str) else None,
        cost_usd=None if cost is None else float(round(cost, 6)),
        **counts,
    )


def _priced(record: RunRecord) -> RunRecord:
    """Sum the turns' costs. One unpriced turn makes the run's cost unknown, never a guess."""
    unpriced = sorted({t.served_by for t in record.turn_records if t.cost_usd is None})
    if unpriced:
        record.cost_usd = None
        note = f"cost unknown: no price for {', '.join(unpriced)}"
        record.detail = f"{record.detail}; {note}" if record.detail else note
    else:
        exact = sum(
            (
                turn_cost(
                    model=t.served_by,
                    input_tokens=t.input_tokens,
                    output_tokens=t.output_tokens,
                    cache_creation_input_tokens=t.cache_creation_input_tokens,
                    cache_read_input_tokens=t.cache_read_input_tokens,
                )
                or Decimal(0)
                for t in record.turn_records
            ),
            Decimal(0),
        )
        record.cost_usd = float(round(exact, 6))
    return record


@contextmanager
def _observe(langfuse: Langfuse | None, **kwargs: Any) -> Generator[Any]:
    """An observation when tracing, nothing otherwise. Yields the observation or None."""
    if langfuse is None:
        yield None
        return
    # start_as_current_observation is overloaded on as_type; with **kwargs no overload is chosen.
    observation: Any
    with langfuse.start_as_current_observation(**kwargs) as observation:  # pyright: ignore[reportUnknownVariableType]
        yield observation


def _plain(value: Any) -> Any:
    """JSON-ready copy of SDK objects (content blocks are Pydantic models) for trace payloads."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        mapping: dict[Any, Any] = value  # pyright: ignore[reportUnknownVariableType]
        return {str(k): _plain(v) for k, v in mapping.items()}
    if isinstance(value, list | tuple):
        items: list[Any] | tuple[Any, ...] = value  # pyright: ignore[reportUnknownVariableType]
        return [_plain(v) for v in items]
    return value


async def _run(
    record: RunRecord,
    mcp: Client,
    messages_api: MessagesAPI,
    tool_budget: int,
    turn_limit: int,
    langfuse: Langfuse | None,
) -> RunRecord:
    incident_id, model, effort = record.incident_id, record.model, record.effort
    tools = [to_anthropic_tool(tool) for tool in (await mcp.list_tools()).tools]
    overview = await mcp.read_resource(f"incident://{incident_id}")
    overview_text = "".join(getattr(content, "text", "") for content in overview.contents)
    if langfuse is not None:
        langfuse.update_current_span(input=overview_text)
    messages: list[BetaMessageParam] = [
        {"role": "user", "content": first_user_message(overview_text)}
    ]
    budget_notice_sent = False

    for turn in range(1, turn_limit + 1):
        started = time.perf_counter()
        with _observe(
            langfuse,
            name=f"turn {turn}",
            as_type="generation",
            model=model,
            model_parameters={"effort": effort, "max_tokens": MAX_TOKENS},
            input=_plain(messages),
        ) as generation:
            response = await messages_api.create(
                **request_params(model=model, effort=effort, tools=tools, messages=messages)
            )
            if generation is not None:
                fallback = response.model != model
                generation.update(
                    output=_plain(response.content),
                    usage_details={
                        "input": response.usage.input_tokens,
                        "output": response.usage.output_tokens,
                        "cache_read_input_tokens": response.usage.cache_read_input_tokens or 0,
                        "cache_creation_input_tokens": response.usage.cache_creation_input_tokens
                        or 0,
                    },
                    metadata={
                        "served_by": response.model,
                        "stop_reason": response.stop_reason,
                        "request_id": getattr(response, "_request_id", None),
                    },
                    level="WARNING" if fallback else "DEFAULT",
                    status_message=f"served by fallback model {response.model}"
                    if fallback
                    else None,
                )
        record.turns = turn
        record.served_by.append(response.model)
        record.usage.add(response)
        record.stop_reason = response.stop_reason
        request_id = getattr(response, "_request_id", None)
        if isinstance(request_id, str):
            record.request_ids.append(request_id)
        record.turn_records.append(_turn_record(turn, response, request_id))
        log_event(
            "turn",
            incident_id=incident_id,
            turn=turn,
            served_by=response.model,
            stop_reason=response.stop_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read_input_tokens=response.usage.cache_read_input_tokens,
            request_id=request_id,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        messages.append({"role": "assistant", "content": response.content})

        match response.stop_reason:
            case "end_turn":
                return _finish_with_answer(record, response)
            case "refusal":
                record.outcome = "refused"
                details = response.stop_details
                record.refusal_category = getattr(details, "category", None)
                return record
            case "max_tokens":
                record.outcome = "truncated"
                return record
            case "pause_turn":
                continue
            case "tool_use":
                uses = [block for block in response.content if block.type == "tool_use"]
                calls_made = sum(1 for call in record.tool_calls if call.outcome != "over_budget")
                if calls_made >= tool_budget and budget_notice_sent:
                    record.outcome = "budget_exhausted"
                    record.detail = f"kept calling tools after the {tool_budget}-call budget"
                    return record
                results: list[BetaToolResultBlockParam] = []
                for block in uses:
                    arguments: dict[str, Any] = dict(block.input)
                    if calls_made >= tool_budget:
                        budget_notice_sent = True
                        warning = error_result(
                            block.id,
                            f"tool budget of {tool_budget} calls exhausted; "
                            "answer now with the evidence you have",
                        )
                        results.append(warning)
                        with _observe(
                            langfuse, name=block.name, as_type="tool", input=arguments
                        ) as observation:
                            if observation is not None:
                                observation.update(
                                    output=warning.get("content"),
                                    level="WARNING",
                                    status_message="over budget: not run",
                                )
                        record.tool_calls.append(
                            ToolCallRecord(
                                turn=turn,
                                name=block.name,
                                arguments=arguments,
                                outcome="over_budget",
                                duration_ms=0.0,
                            )
                        )
                        continue
                    calls_made += 1
                    results.append(
                        await _call_tool(
                            record, mcp, turn, block.id, block.name, arguments, langfuse
                        )
                    )
                # All results in one user message: splitting them discourages parallel calls.
                messages.append({"role": "user", "content": results})
            case _:
                record.outcome = "unexpected_stop"
                record.detail = f"stop_reason {response.stop_reason!r}"
                return record

    record.outcome = "turn_limit"
    record.detail = f"no answer within {turn_limit} turns"
    return record


async def _call_tool(
    record: RunRecord,
    mcp: Client,
    turn: int,
    tool_use_id: str,
    name: str,
    arguments: dict[str, Any],
    langfuse: Langfuse | None,
) -> BetaToolResultBlockParam:
    started = time.perf_counter()
    with _observe(langfuse, name=name, as_type="tool", input=arguments) as observation:
        try:
            result = await mcp.call_tool(name, arguments)
            block = to_tool_result(tool_use_id, result)
            is_error = bool(result.is_error)
        except MCPError as exc:
            block = error_result(tool_use_id, exc.message)
            is_error = True
        if observation is not None:
            # Exactly what the model was given: the compact structured JSON, or the error text.
            observation.update(
                output=block.get("content"),
                level="ERROR" if is_error else "DEFAULT",
                status_message="tool error" if is_error else None,
            )
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    outcome: Literal["ok", "tool_error"] = "tool_error" if is_error else "ok"
    record.tool_calls.append(
        ToolCallRecord(
            turn=turn, name=name, arguments=arguments, outcome=outcome, duration_ms=duration_ms
        )
    )
    log_event("tool_call", turn=turn, tool=name, outcome=outcome, duration_ms=duration_ms)
    return block


def _finish_with_answer(record: RunRecord, response: BetaMessage) -> RunRecord:
    text = "".join(block.text for block in response.content if block.type == "text")
    try:
        record.answer = TriageAnswer.model_validate(json.loads(text))
        record.outcome = "answered"
    except (json.JSONDecodeError, ValidationError) as exc:
        record.outcome = "invalid_answer"
        record.detail = f"{type(exc).__name__}: {str(exc)[:500]}"
    return record
