"""get_ticket, get_runbook and the incident:// resource through an in-process Client."""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError

from incident_triage.server import build_server

ANSWER_KEYS = {"root_cause", "category", "label", "evidence", "evidence_ids", "answer"}


def _text(result: Any) -> str:
    return " ".join(getattr(block, "text", "") for block in result.content)


@pytest.mark.asyncio
async def test_get_ticket_hit() -> None:
    async with Client(build_server()) as client:
        result = await client.call_tool("get_ticket", {"ticket_id": "TKT-0001"})
    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["id"] == "TKT-0001"
    assert result.structured_content["incident_id"] == "INC-0001"
    assert result.structured_content["comments"], "TKT-0001 has a comment"


@pytest.mark.asyncio
async def test_get_runbook_hit() -> None:
    async with Client(build_server()) as client:
        result = await client.call_tool("get_runbook", {"runbook_id": "RB-TLS-CERT"})
    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["id"] == "RB-TLS-CERT"
    assert result.structured_content["steps"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments", "expected"),
    [
        ("get_ticket", {"ticket_id": "TKT-0099"}, "unknown ticket_id TKT-0099"),
        ("get_runbook", {"runbook_id": "RB-NOPE"}, "unknown runbook_id RB-NOPE"),
    ],
)
async def test_unknown_id_is_an_explicit_error_pointing_to_the_resource(
    tool: str, arguments: dict[str, Any], expected: str
) -> None:
    async with Client(build_server()) as client:
        result = await client.call_tool(tool, arguments)
    assert result.is_error is True
    assert expected in _text(result)
    assert "incident://" in _text(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("get_ticket", {"ticket_id": "ticket 1"}),  # malformed
        ("get_ticket", {"ticket_id": "RB-DB-POOL"}),  # a runbook ID
        ("get_ticket", {}),
        ("get_runbook", {"runbook_id": "rb-db-pool"}),  # malformed (lower case)
        ("get_runbook", {"runbook_id": "TKT-0001"}),  # a ticket ID
        ("get_runbook", {}),
    ],
)
async def test_malformed_or_wrong_kind_ids_are_rejected(
    tool: str, arguments: dict[str, Any]
) -> None:
    async with Client(build_server()) as client:
        result = await client.call_tool(tool, arguments)
    assert result.is_error is True


@pytest.mark.asyncio
async def test_incident_resources_are_listed_and_templated() -> None:
    async with Client(build_server()) as client:
        resources = (await client.list_resources()).resources
        templates = (await client.list_resource_templates()).resource_templates
    assert sorted(str(r.uri) for r in resources) == ["incident://INC-0001", "incident://INC-0002"]
    assert all(r.mime_type == "application/json" for r in resources)
    assert [t.uri_template for t in templates] == ["incident://{incident_id}"]


@pytest.mark.asyncio
async def test_reading_an_incident_returns_its_overview_without_answers() -> None:
    async with Client(build_server()) as client:
        result = await client.read_resource("incident://INC-0001")
    [content] = result.contents
    assert content.mime_type == "application/json"
    overview = json.loads(getattr(content, "text", ""))
    assert overview["id"] == "INC-0001"
    assert overview["ticket_ids"] == ["TKT-0001"]
    assert overview["runbook_ids"] == ["RB-DB-POOL"]
    assert overview["log_count"] > 0


def _keys(node: object) -> set[str]:
    if isinstance(node, dict):
        mapping: dict[str, object] = node  # pyright: ignore[reportUnknownVariableType]
        return set(mapping) | {k for value in mapping.values() for k in _keys(value)}
    if isinstance(node, list):
        items: list[object] = node  # pyright: ignore[reportUnknownVariableType]
        return {k for item in items for k in _keys(item)}
    return set()


@pytest.mark.asyncio
@pytest.mark.parametrize("incident_id", ["INC-0001", "INC-0002"])
async def test_no_overview_carries_an_answer_key(incident_id: str) -> None:
    """The overview is served to the agent, so it must never carry a label."""
    async with Client(build_server()) as client:
        result = await client.read_resource(f"incident://{incident_id}")
    keys = _keys(json.loads(getattr(result.contents[0], "text", "")))
    assert len(keys) > 10, "walked too little: the check would pass vacuously"
    assert not keys & ANSWER_KEYS


@pytest.mark.asyncio
async def test_reading_an_unknown_incident_is_an_error_naming_it() -> None:
    async with Client(build_server()) as client:
        with pytest.raises(MCPError, match="unknown incident_id INC-0099") as raised:
            await client.read_resource("incident://INC-0099")
    assert raised.value.code == -32602
