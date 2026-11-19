# Handoff: building the Incident Triage Agent

For a Claude Code instance (or a person) picking up development. Read this, then [PRODUCT.md](../PRODUCT.md) and [the project plan](projects/incident-triage-agent.md). The plan holds the reasoning. The work itself is in the GitHub issues.

**State as of 2026-11-19:** planning is finished and no code exists yet. The repository, 3 milestones, 27 issues and 55 dependency links are in place at <https://github.com/ryanalexanderbrashear/incident-triage-agent>. The only unblocked issue is #1 (repo tooling and CI), so start there. Issues 2, 3 and 4 follow, and none of them needs an API key.

## What you are building

A small Python project that shows three things working together: a **new MCP server** (`incident-mcp`), a **golden-dataset eval harness**, and **Langfuse traces**. An agent answers synthetic incident alerts by calling the server's read-only tools, and every run is graded and traced. The full success criteria are in the plan. They are the definition of done, so check against them rather than against this page.

## How to work

1. **Take the lowest-numbered open issue in the earliest unfinished milestone that has no open `blocked by`.** Check with `gh issue view <n> --json blockedBy`.
2. **Write a plan on the issue before any code** (the `plan-work` skill, if available). Post it as an issue comment and wait for the human to approve it.
3. **One pull request per issue**, branched from `main`. Return to `main` and `git pull` before cutting each branch, and never base a PR on another branch.
4. **Run the gates one at a time and report each status:** `ruff check`, `ruff format --check`, the type checker, and `pytest`. Never read a gate's result through a pipe. Where you write a new test or checker, watch it fail against deliberately broken input before trusting it.
5. **Check the docs.** `README.md` and the plan must stay true after every change. The PR description says which documents were checked, even when none changed.
6. **Audit milestones by using the thing.** The three audit issues (one per milestone) require running the project from a fresh clone, not re-reading the issue list. File what you find as new issues.
7. Update the plan's **Status** and the issue state as work lands. When reality diverges from the plan, change the plan.

## Decisions already made

Do not reopen these without a reason, and record any change as an ADR.

- **Python 3.11+, managed with `uv`.** The lockfile is committed.
- **Hand-written agent loop** on the Anthropic SDK, with the MCP `Client` supplying the tools. No LangChain, LangGraph or vector search.
- **The MCP server is new code**, with three read-only tools (`search_logs`, `get_ticket`, `get_runbook`) and one resource. It runs over stdio and Streamable HTTP.
- **Grading is layered.** Deterministic graders decide pass or fail and gate CI. A model-graded judge scores explanation quality only, and never gates.
- **The golden set is JSONL in the repo**, the source of truth, and is synced to a Langfuse dataset. Every label is hand-verified by the human. LLM help is fine for drafting log text, but not for deciding labels.
- **All data is synthetic**, and the repository is public.
- **Own thin eval harness by default.** A one-hour spike early in M2 may overturn this (open question 5 in the plan).

## SDK facts, checked 2026-11-19 on PyPI

These are newer major versions than most tutorials and training data cover. Verify against the official docs before writing code from memory.

| Package | Version | Note |
|---|---|---|
| `mcp` | 2.3.0 | Server class is `mcp.server.MCPServer` (renamed from `FastMCP`). The client is `mcp.Client`, and a URL argument means Streamable HTTP. Docs: <https://py.sdk.modelcontextprotocol.io/>, including the v2 migration guide. |
| `langfuse` | 5.2.0 | OpenTelemetry-based; needs Python 3.11 or newer. Use `get_client()` with `start_as_current_observation` or `@observe`. The old `trace()`, `span()` and `generation()` client API is deprecated. Query traces through the Observations API v2. Docs: <https://langfuse.com/docs/observability/sdk/overview>. |
| `anthropic` | 2.11.0 | Messages API with tool use. |
| `pytest` | 9.2.0 | With `pytest-asyncio` 1.5.0 for async tests. |

Pin whatever you adopt in ADR-001, with the date you checked.

## Gotchas

- **Under stdio, stdout is the MCP protocol channel.** Never `print()` in the server. Log to **stderr**, as structured JSON with a request ID per tool call.
- **Never commit or log secrets.** Keys live in `.env` (git-ignored) and in CI secrets. Do not paste them into chat, issues, traces or fixtures.
- **Never put real data in the repo.** Logs, tickets, runbooks and names are all invented.
- **CI must not need an API key** for the gate. It runs server tests and the deterministic graders on recorded agent outputs. Live evals are a manual workflow.
- **Follow the `blocked by` links, not the numbering.** Issues #1 to #27 happen to match the original draft's item numbers, but only because the repository was new. New issues will not line up with anything, so never infer a dependency from a number.
- **Cost.** Measure tokens and cost per case from the first trace (item 7) and cap eval concurrency. Spend is limited on the key, so stop and tell the human if a run hits the limit.
- **Honest claims.** The README and docs claim only what a run produced. No number appears that a run did not generate. State limits plainly: synthetic data, a small hand-labelled calibration sample, one model family.

## What only the human can do

Ask for these rather than working around them.

- **Anthropic API key.** A dedicated key, with a monthly spend limit set in the Anthropic console. It goes in `.env` as `ANTHROPIC_API_KEY`.
- **Langfuse Cloud project.** A project and keys, in `.env` as `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_BASE_URL`. Confirm the free tier is enough.
- **GitHub Actions secrets** for the manual live-eval workflow, if used.
- **Labelling.** The golden-set labels (M2) and the 10 or more hand labels for judge calibration (M3) are made by the human. Draft cases, then ask for verification.

Until the keys exist, M1's issues 1 to 4 (repo tooling, ADR, fixtures, server v0) can proceed without them. The agent loop and tracing issues cannot be finished without keys, so ask for them at that point.

## Local setup

`uv` is installed on the author's machine as a user-level Python package, and the binary is at `~/Library/Python/3.11/bin/uv`, which is not on `PATH`. Either add that directory to `PATH` or run `python3 -m uv`. Elsewhere, install `uv` the usual way.

## Open questions still pending (from the plan)

5. Own harness or `pydantic-evals` / Langfuse's experiment runner: decide after the M2 spike.
6. Judge model: a different Claude tier from the agent's.
7. Trace propagation across the MCP boundary: the M3 stretch.
8. Type checker: recommended pyright, strict on the server and graders; decide in the ADR.
