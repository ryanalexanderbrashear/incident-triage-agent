"""A scripted stand-in for `client.beta.messages`, so agent tests need no API key.

Responses are real SDK `BetaMessage` objects, and every request is recorded as a JSON
snapshot taken at call time, so a later mutation of the loop's history can't hide in a test.
"""

from __future__ import annotations

import json
from typing import Any

from anthropic.types.beta import BetaMessage
from pydantic import BaseModel

MODEL = "claude-opus-5-5"


def message(
    content: list[dict[str, Any]],
    stop_reason: str,
    *,
    model: str = MODEL,
    input_tokens: int = 100,
    output_tokens: int = 20,
    cache_read: int = 0,
    stop_details: dict[str, Any] | None = None,
) -> BetaMessage:
    return BetaMessage.model_validate(
        {
            "id": "msg_scripted",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "stop_details": stop_details,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": cache_read,
            },
        }
    )


def tool_use(tool_use_id: str, name: str, **arguments: Any) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_use_id, "name": name, "input": arguments}


def thinking(text: str = "considering the evidence") -> dict[str, Any]:
    return {"type": "thinking", "thinking": text, "signature": "sig-" + text[:8]}


def text(value: str) -> dict[str, Any]:
    return {"type": "text", "text": value}


def answer_text(category: str, explanation: str, evidence_ids: list[str]) -> dict[str, Any]:
    payload = {
        "root_cause_category": category,
        "explanation": explanation,
        "evidence_ids": evidence_ids,
    }
    return text(json.dumps(payload))


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    return value


class ScriptedMessages:
    def __init__(self, *responses: BetaMessage) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> BetaMessage:
        self.requests.append(json.loads(json.dumps(_plain(kwargs))))
        if not self._responses:
            raise AssertionError("the loop made more requests than the script has responses")
        return self._responses.pop(0)
