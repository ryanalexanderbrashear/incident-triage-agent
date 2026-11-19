"""The server logs one JSON line per request to stderr, each with its own request ID."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from mcp import Client

from incident_triage.server import build_server

REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")


def _lines(stderr: str) -> list[dict[str, Any]]:
    """Our JSON lines only; the SDK's own stderr output is plain text and is skipped."""
    lines: list[dict[str, Any]] = []
    for raw in stderr.splitlines():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "event" in parsed:
            lines.append(parsed)  # pyright: ignore[reportUnknownArgumentType]
    return lines


async def _calls(capsys: pytest.CaptureFixture[str]) -> tuple[list[dict[str, Any]], str]:
    capsys.readouterr()  # discard anything earlier
    async with Client(build_server()) as client:
        await client.call_tool("search_logs", {"incident_id": "INC-0001", "query": "pool"})
        await client.call_tool("search_logs", {"incident_id": "INC-0099"})
        await client.call_tool("search_logs", {"incident_id": "INC-0001", "limit": 999})
    captured = capsys.readouterr()
    return _lines(captured.err), captured.out


@pytest.mark.asyncio
async def test_one_line_per_request_each_with_a_fresh_request_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    lines, _ = await _calls(capsys)
    requests = [line for line in lines if line["event"] == "request"]
    calls = [line for line in requests if line["method"] == "tools/call"]
    assert len(calls) == 3
    ids = [line["request_id"] for line in requests]
    assert all(REQUEST_ID.match(i) for i in ids)
    assert len(set(ids)) == len(ids), "request IDs must not repeat"
    for line in calls:
        assert line["tool"] == "search_logs"
        assert isinstance(line["duration_ms"], float | int)


@pytest.mark.asyncio
async def test_outcomes_distinguish_success_tool_error_and_rejected_arguments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    lines, _ = await _calls(capsys)
    calls = [
        line for line in lines if line["event"] == "request" and line["method"] == "tools/call"
    ]
    assert [line["outcome"] for line in calls] == ["ok", "tool_error", "tool_error"]


@pytest.mark.asyncio
async def test_tool_result_line_shares_its_request_id(capsys: pytest.CaptureFixture[str]) -> None:
    lines, _ = await _calls(capsys)
    results = [line for line in lines if line["event"] == "tool_result"]
    assert len(results) == 2  # the rejected-argument call never reaches the tool
    hit, unknown = results
    assert hit["result_count"] > 0 and hit["total_matches"] >= hit["result_count"]
    assert unknown["error"] == "unknown_incident"
    request_ids = {line["request_id"] for line in lines if line["event"] == "request"}
    assert {hit["request_id"], unknown["request_id"]} <= request_ids


@pytest.mark.asyncio
async def test_nothing_is_written_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    _, stdout = await _calls(capsys)
    assert stdout == ""
