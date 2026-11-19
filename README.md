# Incident Triage Agent

A small Python project that shows an MCP server, a golden-dataset eval harness and Langfuse tracing working together. An agent triages synthetic incident alerts by calling a purpose-built, read-only MCP server (`incident-mcp`), and every run is scored and traced.

**Status:** repository tooling and CI are in place (issue #1); the MCP server, agent and evals are not built yet. See [PRODUCT.md](PRODUCT.md) for what this is and [the project plan](docs/projects/incident-triage-agent.md) for how it will be built. Progress is tracked in the GitHub issues and milestones.

This README is replaced with real usage, architecture and results as the milestones land. It will only state numbers that a run produced.

## Development

Requires Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync                      # create .venv and install the locked dev tools
uv run ruff check            # lint
uv run ruff format --check   # formatting
uv run pyright               # type check
uv run pytest                # tests
```

CI (`.github/workflows/ci.yml`) runs each of these as its own job on every pull request and push to `main`, plus a `gitleaks` scan of the full git history. None of it needs an API key. Secrets go in `.env`, which is git-ignored.
