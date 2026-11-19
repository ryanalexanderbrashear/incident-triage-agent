"""The real transports: a stdio subprocess and a Streamable HTTP server on a free port."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator

import pytest
from mcp import Client, StdioServerParameters

SERVER_ARGS = ["-m", "incident_triage.server"]
CALL = {"incident_id": "INC-0001", "query": "pool saturated"}


@pytest.mark.asyncio
async def test_stdio_subprocess_serves_search_logs() -> None:
    params = StdioServerParameters(command=sys.executable, args=SERVER_ARGS)
    async with Client(params) as client:
        result = await client.call_tool("search_logs", CALL)
    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["total_matches"] > 0


def test_stdio_stdout_carries_only_json_rpc() -> None:
    """Under stdio, stdout is the protocol channel: every line must be a JSON-RPC message.

    Runs the server unbuffered. With Python's default block buffering on a pipe, a stray
    print() before the transport starts is silently discarded when the SDK takes over fd 1,
    so this test would pass over the very bug it guards against. Unbuffered (as many MCP host
    configs run Python) the stray line reaches the client and corrupts the stream.
    """
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "stdout-guard", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "search_logs", "arguments": CALL},
        },
    ]
    proc = subprocess.Popen(
        [sys.executable, *SERVER_ARGS],
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdin is not None and proc.stdout is not None
    lines: list[str] = []
    try:
        for message in messages:
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()
        # Read until the tools/call response (id 2) arrives, keeping every line seen.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            lines.append(line)
            parsed = json.loads(line) if line.strip().startswith("{") else None
            if isinstance(parsed, dict) and parsed.get("id") == 2:
                break
    finally:
        proc.terminate()
        _, stderr = proc.communicate(timeout=10)  # also closes stdin

    assert lines, f"no stdout from server; stderr:\n{stderr}"
    for line in lines:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            pytest.fail(f"non-JSON-RPC output on stdout (protocol channel): {line!r}")
        assert message.get("jsonrpc") == "2.0", line
    assert any(json.loads(line).get("id") == 2 for line in lines), (
        f"no tools/call response; stderr:\n{stderr}"
    )
    assert '"event": "server_started"' in stderr


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def http_server() -> Iterator[str]:
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, *SERVER_ARGS, "--transport", "http", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                break
            except OSError:
                if proc.poll() is not None or time.monotonic() > deadline:
                    _, stderr = proc.communicate(timeout=5)
                    pytest.fail(f"HTTP server did not start (exit {proc.returncode}):\n{stderr}")
                time.sleep(0.1)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        proc.communicate(timeout=10)


@pytest.mark.asyncio
async def test_streamable_http_serves_search_logs(http_server: str) -> None:
    async with Client(http_server) as client:
        result = await asyncio.wait_for(client.call_tool("search_logs", CALL), timeout=30)
    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["total_matches"] > 0
