# Incident Triage Agent

A small Python project that shows an MCP server, a golden-dataset eval harness and Langfuse tracing working together. An agent triages synthetic incident alerts by calling a purpose-built, read-only MCP server (`incident-mcp`), and every run is scored and traced.

**Status:** repository tooling, CI and the incident fixtures are in place (issues #1–#3); the MCP server, agent and evals are not built yet. See [PRODUCT.md](PRODUCT.md) for what this is and [the project plan](docs/projects/incident-triage-agent.md) for how it will be built. Progress is tracked in the GitHub issues and milestones.

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
