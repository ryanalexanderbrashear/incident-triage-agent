# Incident Triage Agent

A small Python project that shows an MCP server, a golden-dataset eval harness and Langfuse tracing working together. An agent triages synthetic incident alerts by calling a purpose-built, read-only MCP server (`incident-mcp`), and every run is scored and traced.

**Status:** repository tooling, CI, the incident fixtures, the `incident-mcp` server (three tools and the `incident://` resource) the agent loop with its `triage` CLI, Langfuse tracing and per-run cost are in place (issues #1–#7, #9, #10); the evals are not built yet. Nothing here yet measures how well the agent triages. See [PRODUCT.md](PRODUCT.md) for what this is and [the project plan](docs/projects/incident-triage-agent.md) for how it will be built. Progress is tracked in the GitHub issues and milestones.

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

CI (`.github/workflows/ci.yml`) runs each of these as its own job on every pull request and push to `main`, plus a `gitleaks` scan of the full git history. None of it needs an API key.

For live runs, copy the template and fill it in:

```bash
cp .env.example .env
```

`.env` is git-ignored. `ANTHROPIC_API_KEY` is required for `triage run`. The three `LANGFUSE_*` variables are optional but all-or-nothing: set all three for tracing, or none. `LANGFUSE_BASE_URL` must be the URL of the region that issued the keys. The template's comments say which.

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

Every response, including every error, fits a budget of 16,000 characters (`src/incident_triage/server/budget.py`). The SDK sends a structured result twice, as structured content and as JSON text, and the budget applies to each form.
- **Single items fit by construction.** Every fixture string and collection has a limit (named constants in `src/incident_triage/fixtures/models.py`), set so the largest possible ticket, runbook, overview or log record is within budget. An oversized fixture fails at load time, naming its file and line.
- **`search_logs` pages are cut between records.** A page stops at `limit`, at the end of the matches, or before the record that would pass the budget (never fewer than one), and `limited_by` says which: `"limit"`, `"end"` or `"size"`.
- **Inputs echoed in errors are bounded too:** ID arguments have a maximum length, and unknown-argument names are clipped.

`tests/server/test_protocol.py` builds the worst case (every field and count at its limit) and checks each response against the budget, in-process and over a real stdio connection.

A misspelt or unknown argument is rejected with an error naming it and the expected ones (`unknown argument 'levl' for search_logs; expected: …`) instead of being silently ignored, which would return unfiltered results.

Logs are JSON lines on **stderr**, never stdout (stdout is the protocol channel under stdio): one `server_started` line with the transport and fixture counts, then one `request` line per request with a fresh `request_id`, its `method`, `tool` or `uri`, `outcome` and `duration_ms`. The outcome is `ok`; `tool_error` (a tool reported an anticipated error to the model); `rejected` (the request was wrong, for example an unknown resource, logged at INFO with its `error_code`); or `raised` (a server fault, logged at ERROR). So `"outcome": "raised"` always means a bug. Invalid fixtures stop the server with exit code 2 and a `fixtures_invalid` line naming the file and line.

## Running the agent

The agent needs an Anthropic API key. Put it in `.env` (git-ignored; see *Development* for `.env.example`) as `ANTHROPIC_API_KEY=…`, then:

```bash
uv run --env-file .env triage run INC-0001
```

`triage run` spawns `incident-mcp` over stdio, reads the incident's `incident://` overview, and runs a tool-use loop on `claude-opus-5-5` (effort `medium`) until the model answers. It prints one JSON run record to stdout:

- **`answer`:** `root_cause_category`, `explanation` and `evidence_ids`, or `null` if there was no valid answer.
- **`outcome`:** `answered`, `invalid_answer`, `refused`, `truncated`, `budget_exhausted`, `turn_limit` or `unexpected_stop`.
- **`served_by`:** the model that served each turn. It differs from `model` if a refusal fell back to another model.
- **The run's history:** every tool call and its outcome, summed token usage and the API request IDs.

Logs are JSON lines on stderr, apart from notices the Anthropic and Langfuse SDKs print themselves (for example, when another Anthropic credential exists on the machine and `ANTHROPIC_API_KEY` takes precedence over it). The exit code is 0 when the agent answered, 1 when the run ended without a valid answer, and 2 for a usage or configuration error such as a missing key or an unknown incident. Options: `--model`, `--effort`, `--tool-budget` (default 12) and `--turn-limit` (default 20). The request settings and the reasons for them are in [ADR-002](docs/adr/0002-agent-model-and-requests.md).

The agent's tests use a scripted stand-in for the model, so CI and `uv run pytest` need no key.

## Tracing

With Langfuse keys in `.env` (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`, as listed in `.env.example`), every `triage run` is one Langfuse trace. The run record's `trace_id` names it, and a `trace` line on stderr gives the ID and, when Langfuse can look it up, a link.

| Observation | Type | Holds |
|---|---|---|
| `triage <incident_id>` | agent | The overview in, and the answer (or the outcome) out; marked as an error unless the agent answered |
| `turn <n>` | generation | The full history sent and the response (thinking summary, text, tool calls); the model, effort and token usage as the API reports it; which model served it, marked as a warning if a fallback did |
| `<tool name>` | tool | The arguments, and exactly what the model was given back; marked as an error for a tool error, or a warning when over budget |

Every span carries the tags `case:<incident_id>`, `model:<model>`, `effort:<effort>` and `git:<sha>`, and metadata with the full git SHA (`-dirty` when the working tree has uncommitted changes). Anything shaped like an Anthropic or Langfuse key in the trace content (inputs, outputs and the metadata the agent sets) is replaced with `[redacted]` before it is sent. The Langfuse SDK separately attaches its own public key to each span as instrumentation metadata. That is visible only inside your own Langfuse project, and `triage trace export` strips it.

Tracing is optional, but needs all three variables. `LANGFUSE_BASE_URL` matters: Langfuse keys only work in the region that issued them, and without a URL the SDK would send traces to its default EU host, where keys from another region are rejected. If any variable is missing, the run is untraced: `trace_id` is null, and stderr says `tracing_disabled` and names what is missing. The deprecated `LANGFUSE_HOST` is accepted in place of `LANGFUSE_BASE_URL`, with a note. If the trace is rejected or can't be delivered when it is sent (wrong region, revoked keys, an outage), the run still finishes as normal, but `trace_id` is null, `trace_error` gives the exporter's reason, and stderr says `trace_export_failed` instead of printing a link. The Anthropic SDK's own OpenTelemetry spans are turned off, because each turn's generation already records the call. The reasons are in [ADR-002](docs/adr/0002-agent-model-and-requests.md).

To export a trace as JSON, for example for `docs/traces/`:

```bash
uv run --env-file .env triage trace export <trace_id> --out docs/traces/<name>.json
```

The export fetches every observation from Langfuse's Observations API, drops the SDK's span metadata (including its public key), OpenTelemetry resource attributes and the account's project ID, redacts anything key-shaped, and refuses to write if anything key-shaped survives. [`docs/traces/INC-0001.json`](docs/traces/INC-0001.json) is one such export.

## Cost

Each run record carries `cost_usd` and per-turn usage (`turn_records`), with every turn priced at the model that served it from a dated price table (`src/incident_triage/agent/pricing.py`, checked 2026-11-20). Measured on 2026-11-20, over 3 runs of each seed incident ([`docs/cost.md`](docs/cost.md), with every run record in [`docs/runs/2026-11-20/`](docs/runs/2026-11-20/)):

| | Cost per run (min / median / max) | Turns | Tool calls |
|---|---|---|---|
| INC-0001 | $0.0370 / $0.0508 / $0.0608 | 3 | 4 |
| INC-0002 | $0.0248 / $0.0419 / $0.0535 | 3 | 3 |

The spread comes from the prompt cache: each case's first run wrote the shared prefix, and later runs within 5 minutes read it. Langfuse's independent pricing matched every run to within $0.0000004. These are two easy seed incidents, not a prediction for M2's golden cases.
