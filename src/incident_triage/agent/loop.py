"""The hand-written tool-use loop (ADR-001, ADR-002).

History is append-only: each response's content is appended unchanged, thinking blocks
included, and nothing earlier is ever edited. Claude Opus 5.5's thinking blocks are bound to the
conversation that produced them, so an edited history would silently lose them.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from typing import Any, Literal, Protocol

from anthropic.types.beta import BetaMessage, BetaMessageParam, BetaToolResultBlockParam
from mcp import Client
from mcp.shared.exceptions import MCPError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from incident_triage.agent.answer import ANSWER_SCHEMA, TriageAnswer
from incident_triage.agent.bridge import error_result, to_anthropic_tool, to_tool_result
from incident_triage.agent.prompts import SYSTEM_PROMPT, first_user_message
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
) -> RunRecord:
    """Triage one incident. Raises MCPError only if the incident's overview can't be read."""
    record = RunRecord(incident_id=incident_id, outcome="turn_limit", model=model, effort=effort)
    tools = [to_anthropic_tool(tool) for tool in (await mcp.list_tools()).tools]
    overview = await mcp.read_resource(f"incident://{incident_id}")
    overview_text = "".join(getattr(content, "text", "") for content in overview.contents)
    messages: list[BetaMessageParam] = [
        {"role": "user", "content": first_user_message(overview_text)}
    ]
    budget_notice_sent = False

    for turn in range(1, turn_limit + 1):
        started = time.perf_counter()
        response = await messages_api.create(
            **request_params(model=model, effort=effort, tools=tools, messages=messages)
        )
        record.turns = turn
        record.served_by.append(response.model)
        record.usage.add(response)
        record.stop_reason = response.stop_reason
        request_id = getattr(response, "_request_id", None)
        if isinstance(request_id, str):
            record.request_ids.append(request_id)
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
                        results.append(
                            error_result(
                                block.id,
                                f"tool budget of {tool_budget} calls exhausted; "
                                "answer now with the evidence you have",
                            )
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
                        await _call_tool(record, mcp, turn, block.id, block.name, arguments)
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
) -> BetaToolResultBlockParam:
    started = time.perf_counter()
    try:
        result = await mcp.call_tool(name, arguments)
        block = to_tool_result(tool_use_id, result)
        is_error = bool(result.is_error)
    except MCPError as exc:
        block = error_result(tool_use_id, exc.message)
        is_error = True
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
