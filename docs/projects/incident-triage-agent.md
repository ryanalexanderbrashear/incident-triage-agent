# Incident Triage Agent

**Status:** Approved
**Source:** user description
**Updated:** 2026-11-19

## Goal

What the product is, who it is for and why it is worth building: [PRODUCT.md](../../PRODUCT.md). Purpose and positioning live there, not here.

**Success criteria** — what is true when this project is done, checkable from a clean clone.

- **The MCP server stands alone.** `incident-mcp` starts over stdio and over Streamable HTTP. An in-process MCP `Client` test suite covers every tool, including not-found, malformed input and oversized-result cases. The server also attaches to a stock MCP host with one `claude mcp add` command and answers a real question there.
- **The golden set is real.** At least 25 incident cases live in the repo as JSONL, validated against a schema. Each has a labelled root-cause category and the log or ticket IDs that prove it. At least 5 are hard cases (red herring, two plausible causes) and at least 3 are adversarial (no determinable cause, and a log line containing an injected instruction).
- **One command runs the evals.** `make eval` runs every case, prints per-grader scores, writes a JSON report, and records an experiment run in Langfuse with a score on every case. It exits non-zero when the pass rate drops below the threshold committed in the repo.
- **Every run is traceable.** Each agent run is one Langfuse trace, with LLM generations and MCP tool calls as nested observations, tagged with case ID, model ID and git SHA. The README shows a real trace, and an exported copy is in the repo, so the evidence outlives the Langfuse account.
- **The evals can go red.** The README shows a deliberate regression (a degraded tool description or prompt) failing the gate, then the fix passing it.
- **The judge is measured, not trusted.** A model-graded judge scores explanation quality, and its agreement with at least 10 hand-labelled outputs is reported with the disagreements analysed. The pass/fail gate rests on deterministic graders only.
- **A written account of one improvement loop.** At least one change to a prompt or tool description is driven by eval failures, with before/after scores in the README.
- **Honest README.** One page of architecture and results, with a "what this does not show" section.

## Non-goals

- No UI, dashboard or web app. The interface is a CLI and the MCP server.
- No real log ingestion or integration with a real observability, ticketing or paging product. The data is synthetic.
- No remediation actions. Every tool is read-only.
- No LangChain, LangGraph or vector search. The agent loop is hand-written, so the seams are visible.
- No authentication or multi-tenant design on the server beyond noting it as the next step.
- No production deployment. The server runs locally or in CI.
- No claim of general triage ability. Results hold for this synthetic set only.

## Constraints

- Solo, about 25 to 30 hours over roughly two weeks of evenings.
- Python 3.11 or newer. Local Python is 3.11.2, and `uv` is not installed.
- Public GitHub repository, so synthetic data only, no employer material, and no secrets in the repo or in any trace.
- Langfuse Cloud free tier, and an Anthropic API key with a spend limit.
- The server must be new code written for this project, not a wrapper around an existing MCP server.

## Starting point

Greenfield. There is no code to read, so what follows was checked against package indexes today (2026-11-19) or is marked as an assumption.

**Verified: the SDKs have moved on.** Current PyPI releases are `mcp` 2.3.0 (the 2.x line renamed the server class to `MCPServer` and ships a `Client`), `langfuse` 5.2.0 (OpenTelemetry-based, needs Python 3.11 or newer, with built-in datasets, experiments and LLM-as-judge support), `anthropic` 2.11.0 and `pytest` 9.2.0. Older tutorials, and anything remembered from earlier major versions, will be wrong in places. The first milestone exists partly to find out where.

**Prior art, from experience rather than code to copy.** The author's earlier tool-calling work (separate tools for retrieving context, planning and generating) and an LLM proxy API instrumented with LangChain and Langfuse are the nearest precedent. This project reuses one lesson, that traces made wrong or missing context visible, and nothing else. None of that code is reused, and no employer material is used here.

**House conventions.** The author's usual testing tools are pytest and async Python (FastAPI). Assumed: ruff for lint and format, a type checker (pyright or mypy, undecided), GitHub Actions for CI, and ADRs for stack decisions, as in the author's other personal project.

**Integration surface.** Three external services: the Anthropic Messages API (tool use), Langfuse Cloud (OTel ingestion plus datasets and scores), and any MCP host for the attach check. All are reached with keys supplied through environment variables.

**What is genuinely unknown.**

1. Whether a synthetic golden set can be made hard enough to discriminate. If the agent scores 100% or 0% on day one, the project proves nothing.
2. How trace context should cross the MCP boundary, so the server's work appears under the agent's trace. Unverified, and treated as a stretch.
3. Real cost per eval run, which depends on tokens per case. Unmeasured until M1.
4. Whether the free Langfuse tier and its retention suffice. Unverified.

## Approach

A hand-written agent loop on the Anthropic SDK connects to a new `incident-mcp` server through the MCP `Client`. It lists the server's tools, passes them to the model as tool definitions, and runs the usual tool-use loop to a structured answer: root-cause category, explanation, and the evidence IDs it relies on. An eval harness replays the golden cases through the same loop, grades the answers, and records scores in Langfuse. Everything the agent does is wrapped in Langfuse observations, so one run is one trace.

The server exposes three read-only tools (`search_logs`, `get_ticket`, `get_runbook`) and one resource, over synthetic incidents stored as JSONL fixtures. Results are bounded and paginated, and errors are explicit so the agent can recover from them.

**Grading is layered.** Deterministic graders carry pass/fail: root-cause category match, evidence recall and precision against the labelled IDs, no invented IDs, a tool-call budget, and no obedience to an injected instruction. A model-graded judge scores only the explanation's quality, and is calibrated against hand labels before anyone reads its numbers. That ordering keeps the gate reproducible and is the honest use of model grading.

**The golden set is source-controlled.** JSONL in the repo is the source of truth, reviewed like code, and synced to a Langfuse dataset. The cases' logs may be drafted with LLM help, but every label is verified by hand, because a set labelled by the same kind of model it grades is circular.

**Alternatives rejected**

- **LangChain or LangGraph for the agent.** It would hide the tool loop and the trace shape, which are the things this project shows. Revisit only if the hand-written loop turns out to be a time sink in M1.
- **Wrapping an existing MCP server.** The brief is to build one, and a wrapper shows less than a server with its own schemas, limits and error design.
- **An eval framework such as `pydantic-evals` or Inspect as the core.** A thin harness (a case loader, graders as plain functions, a runner) is about 200 lines and shows the grader design directly. Langfuse's experiment support records the results. Open question 5 revisits this after a spike.
- **Golden set only in Langfuse.** A dataset only reachable through a SaaS login cannot be reviewed in a pull request or reproduced from a clone.
- **A real log backend or SQLite for the fixtures.** Files read into memory are enough for a few hundred lines per incident and keep the project runnable with no services.
- **SSE transport.** Stdio and Streamable HTTP cover local and served use. The server code is the same either way.

## Observability

The skeleton is the thing being observed, so these are decided in M1.

- **Correlation.** One agent run is one Langfuse trace. The trace ID is printed by the CLI and written into every eval report row, so a failing case links straight to its trace. Tags: case ID, model ID, dataset version, git SHA.
- **Server logging.** The server logs to **stderr** only, as structured JSON, with a request ID per tool call. Under stdio, stdout is the protocol channel, and a stray print there corrupts it.
- **What must never be logged.** API keys and any environment values. Data is synthetic by rule, but the redaction check still runs on traces before export.
- **Audit trail.** The eval report and the Langfuse experiment run are the record of what was scored and when. Exported trace JSON for the README examples is kept in `docs/traces/`.
- **First thing to look at when a case fails:** its trace, then the tool-call sequence, then the raw tool results the model saw.
- **Not built, and why.** No metrics stack, dashboards or alerting, because nothing is deployed. What would trigger building them: serving the MCP server to anyone other than the author.

## Milestones

Each milestone is an outcome that can be looked at. Hours are estimates, and the last is deliberately smaller than the sum of its parts.

### M1 — Walking skeleton (about 8 hours)

**Outcome:** From a clean clone, one command runs one incident end to end, with the real model and the real Langfuse project. The CLI answers an alert using the new `incident-mcp` server over stdio, and a single trace appears in Langfuse with generations and a nested tool-call observation. CI is green on the same repo, running the server tests without any API key. One golden case can be run by name.

**Retires:** the SDK-version unknowns (`mcp` 2.x, `langfuse` 5.x, `anthropic` 2.x), the shape of a trace around tool use, the credentials and CI-secret handling, and the cost per run. The observability decisions above are made and implemented here, not later.

**Why first:** everything later assumes the three-service wiring works. It is cheap to find out now that the tutorials are for another major version, and expensive to find out in M3.

### M2 — A golden set that discriminates (about 10 hours)

**Outcome:** The server has all three tools and the resource, with protocol-level tests. The golden set has at least 25 hand-verified cases. `make eval` runs them all, scores them with the deterministic graders, and records a Langfuse experiment run. A baseline is written down, along with what the failures show.

**Retires:** the biggest unknown, whether the task can be evaluated at all. The baseline must land somewhere informative (neither near 0% nor near 100%). If it does not, the cases or the tool design are reworked here, before any judge is built on top.

**Why second:** a judge, a CI gate and a regression demo are all worthless on a set that cannot tell good from bad. Dataset quality is the project's real risk, so it goes ahead of the glamorous parts.

### M3 — A judge, a gate and proof (about 8 hours)

**Outcome:** A model-graded judge scores explanation quality, with its agreement against 10 or more hand labels reported. A threshold gate fails CI when deterministic scores regress, using recorded agent outputs so CI needs no API key. A live eval run is available as a manual workflow. The README shows a deliberate regression turning the gate red, one eval-driven improvement with before/after scores, a real trace, and the "what this does not show" section.

**Retires:** whether the evals can drive a change, and whether the judge deserves any trust.

**Why last:** the gate and the judge both need the dataset and the baseline from M2.

**Stretch, only if M3 lands early:** propagate trace context across the MCP boundary so server-side work nests under the agent's trace.

### Audits

Each milestone ends with an audit, scheduled as a work item: use the thing, do not re-read the list. For M1, run it from a fresh clone on a machine without the working copy's environment. For M2, ask the agent three questions of your own that are not in the dataset. For M3, attach the server to a stock MCP host with `claude mcp add` and ask a real question. Findings become numbered items.

## Work items

Not a table here: one ticket each in the repository's GitHub issues, with milestones as GitHub milestones and `blocked by` links for dependencies. Filed 2026-11-19: 3 milestones, 27 issues and 55 dependency links, read back and matched against the agreed breakdown. All of them are in [the issue list](https://github.com/ryanalexanderbrashear/incident-triage-agent/issues). Milestone audits are issues 8, 19 and 26, and issue 27 is the stretch item.

## Risks and unknowns

- **The golden set is too easy, too hard, or circular.** It would make every later number meaningless. Response: hand-verify every label, include hard and adversarial cases from the start, check the baseline in M2 before building on it, and budget the labelling time explicitly (about 5 of M2's 10 hours).
- **The SDK majors differ from what is known.** `mcp` 2.x and `langfuse` 5.x are both newer than the available experience. Response: M1 is built to find this out, and versions are pinned in [ADR-001](../adr/0001-stack.md).
- **The judge flatters the agent.** A model grading a model's work tends to agree. Response: the judge never gates; it is calibrated against hand labels, and disagreements are published.
- **Nondeterminism makes scores noisy.** Response: record the baseline over three runs per case, report per-case pass rates, and gate CI on recorded outputs, not live calls.
- **Cost surprises.** Response: set a spend limit on the API key before M1, measure cost per case from the first trace, and cap eval concurrency.
- **Scope drift into an "agent platform".** Response: the non-goals above, and any new idea goes under Out of scope.
- **A secret or real data in a public repo.** Response: `.env` ignored from the first commit, a secret scan in CI, and the rule that all data is synthetic.
- **Langfuse free-tier limits or retention.** Unverified. Response: exported traces and screenshots live in the repo, so the portfolio does not depend on the account.
- **Overclaiming afterwards.** What this project shows is what it contains, and no more: a server built, a judge calibrated on a small hand-labelled sample, evals run on synthetic data. The README says so, in its "what this does not show" section.

## Open questions

**Block starting M1**

1. **Repository.** Create public `incident-triage-agent` under the author's GitHub account? Recommend yes, with an MIT licence. Creating it is outward-facing, so it waits for an explicit go-ahead.
2. **Anthropic key and spend limit.** Recommend a dedicated key with a monthly limit set in the console (for example $20), then adjust after measuring real cost in M1.
3. **Langfuse project.** Recommend a Langfuse Cloud project in the region nearest the author, with keys in `.env` and CI secrets. Confirm the free tier before relying on it.
4. **Tooling.** Recommend installing `uv` for the lockfile and reproducible runs. The fallback is `venv` and `pip`, which works but loses the lockfile.

**Can wait**

5. **Own harness or `pydantic-evals` / Langfuse's experiment runner?** Recommend a time-boxed 1-hour spike early in M2 and the thin own harness unless the spike shows a clear win.
6. **Judge model.** Recommend a different Claude tier from the agent's, to reduce self-agreement. Decide in M3.
7. **Trace propagation across MCP.** Recommend leaving it as the M3 stretch.
8. **Type checker.** *Decided 2026-11-19:* pyright, `standard` repo-wide and strict on the server and graders as they land. See [ADR-001](../adr/0001-stack.md).

## Out of scope

- A web UI or dashboard for browsing eval results. Langfuse already serves that purpose.
- Real integrations with a paging, logging or ticketing product. A follow-up project, if wanted.
- Multi-agent or planner/executor designs.
- Auth, rate limiting and multi-tenant use of the MCP server.
- Anything beyond the code and its README: write-ups and talks come after the project, from what it contains.
