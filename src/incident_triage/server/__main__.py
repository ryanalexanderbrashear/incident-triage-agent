"""`incident-mcp` entry point. Serves over stdio (default) or Streamable HTTP.

Never print here: under stdio, stdout is the MCP protocol channel. All output goes to stderr.
"""

from __future__ import annotations

import argparse
import logging
import sys

from incident_triage import __version__
from incident_triage.fixtures import FixtureError, load_fixtures
from incident_triage.server.app import build_server
from incident_triage.server.observability import log_event


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="incident-mcp", description=__doc__)
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP only (default: %(default)s)")
    parser.add_argument("--port", type=int, default=8000, help="HTTP only (default: %(default)s)")
    args = parser.parse_args(argv)

    try:
        store = load_fixtures()
    except FixtureError as exc:
        log_event("fixtures_invalid", level=logging.ERROR, error=str(exc))
        return 2

    server = build_server(store)
    http = (
        {"host": args.host, "port": args.port, "path": "/mcp"} if args.transport == "http" else None
    )
    log_event(
        "server_started",
        transport=args.transport,
        version=__version__,
        fixtures=store.counts(),
        http=http,
    )

    if args.transport == "http":
        server.run("streamable-http", host=args.host, port=args.port)
    else:
        server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
