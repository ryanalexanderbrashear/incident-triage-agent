# Draft breakdown (temporary)

This is a draft for agreement before anything is filed. Once the repository exists, each item becomes a GitHub issue with its milestone, and the dependency links are added in a third pass from the issue numbers the filing returns. Then delete this file. It is not the tracker, and it goes stale the moment it is copied.

"Depends on" uses the draft numbers below only to show intent. Never map a draft number to an issue number by arithmetic.

## M1 — Walking skeleton

| # | Item | Done when | Depends on |
|---|---|---|---|
| 1 | Repo, `pyproject`, lint, CI | A clean clone passes `ruff` and one placeholder `pytest` in GitHub Actions. `.env` is ignored and a secret scan runs. | none |
| 2 | ADR-001: stack, pinned versions, rejected alternatives | The ADR names `mcp`, `langfuse` and `anthropic` versions and the type checker, and lists the alternatives from the plan. | 1 |
| 3 | Incident fixture schema and 2 seed incidents | Pydantic models load and validate two synthetic incidents with logs, one ticket and one runbook each. | 1 |
| 4 | `incident-mcp` v0: `search_logs` over stdio and Streamable HTTP | An in-process `Client` test calls the tool, including a no-match case, and the server logs to stderr with a request ID. | 3 |
| 5 | Agent loop v0 and CLI | `triage run <case>` bridges the MCP tools into an Anthropic tool-use loop and prints a structured answer. | 4 |
| 6 | Langfuse tracing v0 | One run is one trace with generation and tool-call observations, tagged with case ID, model ID and git SHA, and the CLI prints the trace ID. | 5 |
| 7 | Record cost per run and one example trace | Tokens and cost for the seed cases are noted, and an exported trace sits in `docs/traces/`. | 6 |
| 8 | Audit M1 | Run from a fresh clone, then file the findings as items. | 1–7 |

## M2 — A golden set that discriminates

| # | Item | Done when | Depends on |
|---|---|---|---|
| 9 | `get_ticket` and `get_runbook` tools, plus an `incident://` resource | Each has a bounded, paginated or explicit-error response and an in-process test. | 4 |
| 10 | Server protocol tests | Not-found, malformed input and oversized-result cases are covered for every tool. | 9 |
| 11 | Golden-case schema and validator | `make validate-golden` rejects a case missing a label or an evidence ID that does not exist in the fixtures. | 3 |
| 12 | Golden cases 1–12 (straightforward) | Twelve hand-verified cases across at least 4 root-cause categories. | 11 |
| 13 | Golden cases 13–20 (hard) | Eight cases with a red herring or two plausible causes, each hand-verified. | 11 |
| 14 | Golden cases 21–25 (adversarial) | Three or more are "cannot determine", and at least one has an injected instruction in a log line. | 11 |
| 15 | Deterministic graders | Category match, evidence recall and precision, no invented IDs, tool-call budget, and injection resistance are plain functions with unit tests. Each is shown failing on a known-bad answer. | 11 |
| 16 | Eval runner, report and Langfuse experiment | `make eval` runs all cases concurrently, writes a JSON report and records an experiment run with per-case scores. | 6, 15 |
| 17 | Early spike: own harness vs `pydantic-evals` and Langfuse's experiment runner | A one-page note decides open question 5. Time-boxed to 1 hour. | 15 |
| 18 | Baseline over 3 runs per case, with a written analysis | Per-case pass rates and what the failures show are written up. If the baseline is near 0% or 100%, cases or tools are reworked first. | 12–16 |
| 19 | Audit M2 | Ask the agent three questions that are not in the dataset, and use the eval report as a reader would. | 9–18 |

## M3 — A judge, a gate and proof

| # | Item | Done when | Depends on |
|---|---|---|---|
| 20 | Hand-label 10 or more agent outputs | Labels are stored in the repo, made before the judge is run. | 18 |
| 21 | Model-graded judge and calibration report | Agreement with the hand labels is reported, with the disagreements analysed. The judge is not wired into the gate. | 20 |
| 22 | Threshold gate and CI job | `make eval-check` exits non-zero below the committed threshold. CI runs it on recorded outputs with no API key. A manual workflow runs the live eval. | 18 |
| 23 | Regression demo | A degraded tool description turns the gate red, and the fix turns it green. Both are recorded in the README. | 22 |
| 24 | Eval-driven improvement | One prompt or tool-description change is driven by baseline failures, with before/after scores. | 18 |
| 25 | README, architecture diagram and limitations | One page, with real numbers from real runs and a "what this does not show" section. | 21–24 |
| 26 | Audit M3 | Run from a fresh clone, then attach the server with `claude mcp add` and ask a real question. | 20–25 |
| 27 | Stretch: trace context across the MCP boundary | Server-side spans nest under the agent trace. Only if M3 lands early. | 6, 9 |

27 items, 3 milestones. Items 8, 19 and 26 are audits.
