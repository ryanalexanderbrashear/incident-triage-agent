# Incident Triage Agent

A small Python project that shows an MCP server, a golden-dataset eval harness and Langfuse tracing working together. An agent triages synthetic incident alerts by calling a purpose-built, read-only MCP server (`incident-mcp`), and every run is scored and traced.

**Status:** repository tooling, CI, the incident fixtures and `incident-mcp` v0 with one tool (`search_logs`) are in place (issues #1–#4); the agent and evals are not built yet. See [PRODUCT.md](PRODUCT.md) for what this is and [the project plan](docs/projects/incident-triage-agent.md) for how it will be built. Progress is tracked in the GitHub issues and milestones.

This README is replaced with real usage, architecture and results as the milestones land. It will only state numbers that a run produced.

## Development

Requires Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync                      # create .venv and install the locked dependencies and dev tools
uv run ruff check            # lint
uv run ruff format --check   # formatting
uv run pyright               # type check
uv run pytest                # tests
```

CI (`.github/workflows/ci.yml`) runs each of these as its own job on every pull request and push to `main`, plus a `gitleaks` scan of the full git history. None of it needs an API key. Secrets go in `.env`, which is git-ignored.

Pinned versions and the reasons for them are in [ADR-001](docs/adr/0001-stack.md). On an Intel Mac the lockfile deliberately resolves `cryptography` to an older release; the ADR explains why.

## Fixtures

The data the MCP server will serve lives in `src/incident_triage/fixtures/data/`: one directory per incident (`incident.json`, `logs.jsonl`, `ticket.json`) and shared runbooks. All of it is invented. IDs are type-prefixed and globally unique (`INC-0001`, `LOG-0001-0042`, `TKT-0001`, `RB-DB-POOL`). Fixtures never carry root-cause labels, because the agent can read them; labels belong in the golden set. `load_fixtures()` validates everything and reports problems as `<path>:<line>: <id>: <problem>`.

## Running the server

`incident-mcp` is a read-only MCP server over the fixtures. v0 has one tool, `search_logs`.

```bash
uv run incident-mcp                                   # stdio (what an MCP host launches)
uv run incident-mcp --transport http --port 8000      # Streamable HTTP at http://127.0.0.1:8000/mcp
```

`search_logs` takes an `incident_id` (required), `query` (space-separated terms, all must match), an optional minimum `level` and exact `service`, `limit` (1–50, default 20) and `offset`. It returns records in timestamp order with their `LOG-…` IDs, `total_matches` and `next_offset`. No match is an empty result; an unknown incident is an error that names the known ones.

Logs are JSON lines on **stderr**, never stdout (stdout is the protocol channel under stdio): one `server_started` line with the transport and fixture counts, then one `request` line per request with a fresh `request_id`, its `method`, `tool`, `outcome` (`ok`, `tool_error` or `raised`) and `duration_ms`. Invalid fixtures stop the server with exit code 2 and a `fixtures_invalid` line naming the file and line.
