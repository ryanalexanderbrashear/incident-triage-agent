# Incident Triage Agent

A small Python project that shows an MCP server, a golden-dataset eval harness and Langfuse tracing working together. An agent triages synthetic incident alerts by calling a purpose-built, read-only MCP server (`incident-mcp`), and every run is scored and traced.

**Status:** repository tooling, CI, the incident fixtures and the `incident-mcp` server (three tools and the `incident://` resource) are in place (issues #1–#4 and #9); the agent and evals are not built yet. See [PRODUCT.md](PRODUCT.md) for what this is and [the project plan](docs/projects/incident-triage-agent.md) for how it will be built. Progress is tracked in the GitHub issues and milestones.

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

`incident-mcp` is a read-only MCP server over the fixtures, with three tools and one resource.

```bash
uv run incident-mcp                                   # stdio (what an MCP host launches)
uv run incident-mcp --transport http --port 8000      # Streamable HTTP at http://127.0.0.1:8000/mcp
```

| Name | Kind | What it returns |
|---|---|---|
| `incident://{incident_id}` | resource (JSON) | Where an investigation starts: the alert, severity, `ticket_ids`, `runbook_ids`, `log_count`, first and last log time, and the services in the logs. Listed per incident and as a template. |
| `search_logs` | tool | One page of an incident's logs. Takes an `incident_id` (required), `query` (space-separated terms, all must match), an optional minimum `level` and exact `service`, `limit` (1–50, default 20) and `offset`. Returns records in timestamp order with their `LOG-…` IDs, `total_matches` and `next_offset`. |
| `get_ticket` | tool | One ticket (`TKT-…`), with its body and comments. |
| `get_runbook` | tool | One runbook (`RB-…`): the symptoms it covers and its steps. |

A model using the tools can't read resources itself; the host or agent code reads `incident://` and gives it the overview. No match from `search_logs` is an empty result. An unknown but well-formed ID is an explicit error: an unknown incident names the known ones, and an unknown ticket or runbook points to the incident's `incident://` overview. A malformed or wrong-kind ID (`get_ticket("RB-DB-POOL")`) is rejected by the tool's schema.

Every response is bounded because the fixtures are. A log message is at most 1,000 characters with 20 attributes; a ticket body at most 4,000 characters, with 20 comments of up to 2,000; a runbook at most 10 symptoms and 20 steps, each up to 500. An oversized fixture fails at load time. The limits are named constants in `src/incident_triage/fixtures/models.py`.

Logs are JSON lines on **stderr**, never stdout (stdout is the protocol channel under stdio): one `server_started` line with the transport and fixture counts, then one `request` line per request with a fresh `request_id`, its `method`, `tool` or `uri`, `outcome` and `duration_ms`. The outcome is `ok`; `tool_error` (a tool reported an anticipated error to the model); `rejected` (the request was wrong, for example an unknown resource, logged at INFO with its `error_code`); or `raised` (a server fault, logged at ERROR). So `"outcome": "raised"` always means a bug. Invalid fixtures stop the server with exit code 2 and a `fixtures_invalid` line naming the file and line.
