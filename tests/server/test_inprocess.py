"""The issue's Done-when: an in-process Client calls search_logs, including a no-match case."""

from __future__ import annotations

from typing import Any

import pytest
from mcp import Client

from incident_triage.server import build_server


def _text(result: Any) -> str:
    return " ".join(getattr(block, "text", "") for block in result.content)


@pytest.mark.asyncio
async def test_lists_three_read_only_tools_with_output_schemas() -> None:
    async with Client(build_server()) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(tools) == {"search_logs", "get_ticket", "get_runbook"}
    for tool in tools.values():
        assert tool.annotations is not None, tool.name
        assert tool.annotations.read_only_hint is True, tool.name
        assert tool.output_schema is not None, tool.name
    tool = tools["search_logs"]
    assert {"incident_id", "query", "level", "service", "limit", "offset"} <= set(
        tool.input_schema["properties"]
    )


@pytest.mark.asyncio
async def test_hit_returns_records_with_their_ids() -> None:
    async with Client(build_server()) as client:
        result = await client.call_tool(
            "search_logs", {"incident_id": "INC-0002", "query": "certificate expired"}
        )
    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["total_matches"] > 0
    ids = [record["id"] for record in result.structured_content["records"]]
    assert ids and all(i.startswith("LOG-0002-") for i in ids)


@pytest.mark.asyncio
async def test_no_match_is_not_an_error() -> None:
    async with Client(build_server()) as client:
        result = await client.call_tool(
            "search_logs", {"incident_id": "INC-0001", "query": "kubernetes"}
        )
    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["total_matches"] == 0
    assert result.structured_content["records"] == []


@pytest.mark.asyncio
async def test_unknown_incident_is_an_explicit_tool_error() -> None:
    async with Client(build_server()) as client:
        result = await client.call_tool("search_logs", {"incident_id": "INC-0099"})
    assert result.is_error is True
    assert "unknown incident_id INC-0099" in _text(result)
    assert "INC-0001" in _text(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"incident_id": "INC-0001", "limit": 51},
        {"incident_id": "INC-0001", "limit": 0},
        {"incident_id": "INC-0001", "offset": -1},
        {"incident_id": "incident one"},
        {"incident_id": "INC-0001", "query": "x" * 201},
        {"incident_id": "INC-0001", "level": "WARNING"},
        {},
    ],
)
async def test_invalid_arguments_are_rejected(arguments: dict[str, Any]) -> None:
    async with Client(build_server()) as client:
        result = await client.call_tool("search_logs", arguments)
    assert result.is_error is True
