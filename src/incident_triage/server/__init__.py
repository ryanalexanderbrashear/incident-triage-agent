"""incident-mcp: a read-only MCP server over the synthetic incident fixtures."""

from incident_triage.server.app import SERVER_NAME, build_server

__all__ = ["SERVER_NAME", "build_server"]
