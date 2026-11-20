"""Convert between MCP and the Anthropic Messages API.

The MCP SDK returns a structured tool result twice: as structured content and as JSON text.
The model is given the structured copy only, as compact JSON: the text copy would roughly double
the tokens for no information (#10 measured 9,731 vs 13,103 characters for one seed page).
"""

from __future__ import annotations

import json
from typing import Any

from anthropic.types.beta import BetaToolParam, BetaToolResultBlockParam
from mcp.types import CallToolResult, Tool


def to_anthropic_tool(tool: Tool) -> BetaToolParam:
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.input_schema,
    }


def _text(result: CallToolResult) -> str:
    return "\n".join(getattr(block, "text", "") for block in result.content).strip()


def to_tool_result(tool_use_id: str, result: CallToolResult) -> BetaToolResultBlockParam:
    if result.is_error:
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": _text(result) or "the tool reported an error with no message",
            "is_error": True,
        }
    structured: dict[str, Any] | None = result.structured_content
    content = (
        json.dumps(structured, separators=(",", ":"), ensure_ascii=False)
        if structured is not None
        else _text(result)
    )
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}


def error_result(tool_use_id: str, message: str) -> BetaToolResultBlockParam:
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": message, "is_error": True}
