# Cost per run (M1 measurement)

**Measured:** 2026-11-20, at commit `4096ee8`, by issue [#7](https://github.com/ryanalexanderbrashear/incident-triage-agent/issues/7).
**Runs:** 3 live runs of each seed incident, `uv run --env-file .env triage run <id>`, one after another. Every run record is in [`docs/runs/2026-11-20/`](runs/2026-11-20/); every figure below is computed from those files.
**Settings:** `claude-opus-5-5`, effort `medium`, the defaults in [ADR-002](adr/0002-agent-model-and-requests.md).

## Results

| | INC-0001 | INC-0002 |
|---|---|---|
| Outcome | `answered` ×3 | `answered` ×3 |
| `root_cause_category` | `config_or_deploy_change` ×3 | `expired_credential` ×3 |
| Turns | 3 / 3 / 3 | 3 / 3 / 3 |
| Tool calls | 4 / 4 / 4 | 3 / 3 / 3 |
| Input tokens (uncached) | 8 / 8 / 8 | 8 / 8 / 8 |
| Output tokens | 974 / 1,007 / 1,035 | 711 / 739 / 784 |
| Cache write tokens | 2,848 / 5,848 / 7,703 | 1,571 / 4,992 / 7,242 |
| Cache read tokens | 7,794 / 10,192 / 13,190 | 8,040 / 10,415 / 13,705 |
| **Cost per run** | **$0.0370 / $0.0508 / $0.0608** | **$0.0248 / $0.0419 / $0.0535** |

Each cell is minimum / median / maximum over the three runs. The six runs cost **$0.2689** in total. Every run was served by `claude-opus-5-5` on every turn, with no refusal fallback.

## What the numbers show

- **The cache, not the model's output, drives the spread.** Each case's first run wrote the shared prefix (system prompt, tool definitions and the opening message) to the 5-minute prompt cache: 2,398 tokens for INC-0001, 2,375 for INC-0002, on turn 1. Runs 2 and 3, started within 5 minutes, read that prefix instead, and could also reuse later turns that matched exactly. The first runs cost $0.0608 and $0.0535; the later ones $0.0248–$0.0508. Output tokens varied by at most about 10% within a case.
- **Almost no input is billed at the full rate.** Uncached input was 8 tokens per run; everything else was a cache write ($5 per million) or a cache read ($0.20 per million).
- **For an eval run,** cases run back to back within 5 minutes will cost less than these first runs, and a cold start costs about what a first run did. With these two cases, a first run cost $0.05–$0.06.

**Limits.** These are two easy seed incidents, three runs each: enough to see the spread, not to predict what M2's harder golden cases will cost. Harder cases may take more turns and more tool calls. This measures cost, not answer quality; the seed incidents are not hand-verified golden cases.

## How cost is computed

`RunRecord.cost_usd` (in `src/incident_triage/agent/pricing.py`) prices each turn at the model that served it, using the Messages API's own usage counts:

| Model | Input | 5-min cache write | Cache read | Output |
|---|---|---|---|---|
| `claude-opus-5-5` | $4 | $5 | $0.20 | $20 |
| `claude-opus-5`, `claude-opus-4-8` | $5 | $6.25 | $0.50 | $25 |
| `claude-sonnet-5-5` | $2 | $2.50 | $0.10 | $10 |
| `claude-haiku-5-5` (prompts ≤ 100K) | $0.10 | $0.125 | $0.01 | $0.50 |

These are USD per million tokens, from <https://platform.claude.com/docs/en/about-claude/pricing.md>, read 2026-11-20. The page has no "last updated" date. A turn served by a model not in this table makes the run's cost null, with the model named in `detail`. The pricing page doesn't say how a refusal fallback is billed; pricing such a turn at its serving model's rates is an assumption.

**Cross-check.** Langfuse prices each generation independently. For all six runs its total matched `cost_usd` to within $0.0000004, which is our rounding to 6 decimal places.

**When prices change,** update the table in `pricing.py`, its `VERIFIED` date and this page together.

## Example trace

[`docs/traces/INC-0001.json`](traces/INC-0001.json) is INC-0001's first run above (trace `d0f39057bf8ffef64331fe588a7aef92`, $0.0608). It was exported with `triage trace export`, and it has 8 observations: the run, 3 generations and 4 tool calls. The export drops the Langfuse SDK's span metadata (which includes its public key), OpenTelemetry resource attributes and the account's project ID, redacts anything key-shaped, and refuses to write if anything key-shaped survives.
