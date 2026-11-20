"""MCP tools and results, converted for the Messages API."""

from __future__ import annotations

import json

import pytest
from mcp import Client

from incident_triage.agent.bridge import to_anthropic_tool, to_tool_result
from incident_triage.server import build_server


@pytest.mark.asyncio
async def test_tool_definitions_carry_the_servers_names_descriptions_and_schemas() -> None:
    async with Client(build_server()) as client:
        tools = (await client.list_tools()).tools
    converted = [to_anthropic_tool(tool) for tool in tools]
    assert [t["name"] for t in converted] == [t.name for t in tools]
    for tool, definition in zip(tools, converted, strict=True):
        assert definition["input_schema"] == tool.input_schema
        assert definition.get("description") == tool.description
        assert set(definition) == {"name", "description", "input_schema"}


@pytest.mark.asyncio
async def test_a_result_is_passed_as_compact_structured_json_only() -> None:
    async with Client(build_server()) as client:
        result = await client.call_tool("get_ticket", {"ticket_id": "TKT-0001"})
    block = to_tool_result("tu_1", result)
    content = block.get("content")
    assert isinstance(content, str)
    assert json.loads(content) == result.structured_content
    assert "\n" not in content and ": " not in content, "compact, not the indented text copy"
    assert "is_error" not in block


@pytest.mark.asyncio
async def test_a_tool_error_becomes_an_error_result_with_the_servers_message() -> None:
    async with Client(build_server()) as client:
        result = await client.call_tool("get_ticket", {"ticket_id": "TKT-0099"})
    block = to_tool_result("tu_1", result)
    assert block.get("is_error") is True
    assert "unknown ticket_id TKT-0099" in str(block.get("content"))
