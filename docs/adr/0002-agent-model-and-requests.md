# ADR-002: The agent's model and request settings

**Status:** Accepted
**Date:** 2026-11-19
**Issue:** [#5](https://github.com/ryanalexanderbrashear/incident-triage-agent/issues/5)

## Context

ADR-001 chose a hand-written tool-use loop on the Anthropic SDK (`anthropic` 2.11.0). #5 built it. Every eval run and every trace goes through this loop, so its request settings decide what the scores mean. The current model generation also changed several request rules that older examples rely on. These decisions were checked against the installed SDK's source and the Claude API reference, not taken from memory.

## Decision

| Setting | Value | Why |
|---|---|---|
| Model | `claude-opus-5-5` | The recommended default. Overridable with `triage run --model`. Moving to a cheaper model is a cost decision, made on #7's measurements, not assumed. |
| Effort | `medium`, set explicitly | It's Opus 5.5's default, one level below Opus 5's `high`, so it's stated rather than implied. Overridable with `--effort`. M2's effort sweep can tune it. |
| Thinking | `{"type": "adaptive", "display": "summarized"}` | Opus 5.5's thinking can't be disabled (`disabled` is a 400). `summarized` is billed the same as the default `omitted`, and gives #6's traces the model's reasons for each tool call. |
| Final answer | `output_config.format` with a JSON schema (`agent/answer.py`) | Forced `tool_choice` (`any` / `tool`) is a 400 on Opus 5.5, so a "submit answer" tool can't be forced. Structured outputs work alongside tools and guarantee the final turn is valid JSON. The wire schema is hand-written because structured outputs reject length and size constraints. `TriageAnswer` enforces those after parsing, and a test keeps the two in step. |
| Refusal fallback | `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`, so `client.beta.messages`) | A refused request is re-run on another model inside the same call. Because that means a different model answered, the run record keeps `served_by` (each turn's `response.model`), and the eval harness must count fallback runs separately. |
| History | Append-only; each `response.content` appended unchanged | Opus 5.5's thinking blocks are bound to the conversation that produced them. Editing or stripping an earlier turn silently loses them. `test_history_is_append_only_with_thinking_blocks_unchanged` enforces this. |
| Tool results | The structured copy only, as compact JSON; tool errors as `is_error: true` | MCP returns a structured result twice (structured content and JSON text). Passing both would roughly double the tokens (#10). |
| Parallel tool calls | All results returned in one user message | Splitting them across messages discourages the model from making parallel calls. |
| Caching | Top-level `cache_control: {"type": "ephemeral"}`, with only `messages` varying between turns | The tools and system prompt are a stable prefix. Whether it's long enough to be cached is unmeasured until #7: `cache_read_input_tokens` in the run record shows it. |
| Limits | 12 tool calls, 20 turns, `max_tokens` 16,000 (non-streaming) | Bounded cost per run. Over budget, the model gets one "answer now" error; ignoring it ends the run as `budget_exhausted`. |
| Credentials | `ANTHROPIC_API_KEY` from `.env`, loaded with `uv run --env-file .env` | No new dependency, and the key never touches the repository, the logs or stdout. |

### Root-cause categories (final as of #11, 2026-11-20)

`config_or_deploy_change`, `resource_exhaustion`, `dependency_failure`, `expired_credential`, `code_defect`, `infrastructure`, `cannot_determine`. **Labelling rule:** the category is the trigger, the thing whose reversal ends the incident. The enum lives in `agent/answer.py`, so #11's golden labels import the same spelling.

**Confirmed in #11** by the human, who owns labelling: these seven, with the trigger rule. Golden cases (`golden/cases.jsonl`) import the same enum, so labels and answers can't disagree on spelling. Changing the set from here on means changing this ADR and every golden case that uses a changed category.

**Evidence, for the graders (#15).** A golden case labels `evidence_ids`, the records that prove the cause, against which recall is measured. It also labels optional `supporting_ids`, records a good answer may cite without penalty, such as ones ruling out a red herring. **Precision counts a cited ID as correct if it's in either list.** This came out of #5's live runs, which cited records ruling out alternatives: penalising them would reward citing less, not reasoning better.

### Tracing (#6)

| Setting | Value | Why |
|---|---|---|
| Trace shape | One trace per run: an `agent` root, one `generation` per Messages API call, one `tool` per tool call | Matches the run record one-to-one, so a failed case's trace reads the same way as its record. |
| Anthropic SDK spans | Off (`AsyncAnthropic(open_telemetry=False)`, in `cli.make_anthropic`) | Langfuse registers the global OpenTelemetry provider, so the SDK would add an `anthropic.messages.create` span under every generation. Its `gen_ai.usage.input_tokens` includes cache reads, unlike the API's `usage.input_tokens`, which would give each turn two contradicting token records. `test_the_cli_client_emits_no_sdk_spans` proves it, against a control client that does emit one. |
| Usage on generations | `input`, `output`, `cache_read_input_tokens` and `cache_creation_input_tokens`, exactly as the API reports them | One meaning per number. Pricing is #7's job. |
| Content | Full: each generation's input is the full history sent, and tool observations hold exactly what the model received | The data is synthetic by rule, and content is what makes a trace useful for debugging. |
| Redaction | Every client is built by `tracing.langfuse_client`, with `mask=redact`, which replaces anything shaped like `sk-ant-…`, `sk-lf-…` or `pk-lf-…` in the trace content (inputs, outputs and the metadata the agent sets) | The plan's "redaction check still runs on traces before export". The test uses the production constructor, so removing the mask fails it. **Corrected in #7:** the mask does not see span metadata the Langfuse SDK adds itself, which includes its own public key (`scope.attributes.public_key`). That key is visible only inside the project. `triage trace export` strips it, along with OpenTelemetry resource attributes and the project ID, and refuses to write if anything key-shaped survives. |
| Trace attributes | `propagate_attributes`: tags `incident:`, `case:` (golden cases only, since #11), `model:`, `effort:`, `git:`; metadata with the incident ID, the case ID for a golden case, and the full git SHA (`-dirty` for uncommitted changes) | On every span, including other libraries' (verified on the `mcp` SDK's `tools/call` spans). |
| Config incomplete | Both keys **and** a URL (`LANGFUSE_BASE_URL`, or the deprecated `LANGFUSE_HOST`) are required. Otherwise the run continues untraced, logging `tracing_disabled` with the missing variables, and `trace_id` is null | Keyless dev and CI runs keep working. **Added in #47:** without a URL the SDK defaults to the EU host, which rejects US-region keys at export time. A preflight `auth_check()` can't catch that: Langfuse Cloud's read API answers across regions (measured), while its ingestion endpoint doesn't. The eval harness (#16) can require tracing. |
| Export failed | While the CLI flushes, a handler on the `opentelemetry.exporter.otlp` loggers collects ERRORs. If any arrive, `trace_id` is null, `trace_error` holds the reason, and the CLI logs `trace_export_failed`. The run's outcome and exit code are unchanged | The OTLP exporter reports a rejected or undeliverable batch only by logging it, and `flush()` exports synchronously. A run must never print a `trace_id` for a trace that wasn't delivered (#47). A test runs the real exporter against a stub answering 401, with the same stub answering 200 as its control. |
| Export | `flush()` in a `finally`, then `shutdown()` | A short-lived CLI would otherwise exit before the batch exporter sends. |
| Trace link | Best-effort, with a 5-second timeout | `Langfuse.get_trace_url()` makes a blocking API call that raises on failure. It must never fail a run. |

### Cost (#7)

| Setting | Value | Why |
|---|---|---|
| Where cost comes from | `RunRecord.cost_usd`, the sum of `turn_records[*].cost_usd`, from `agent/pricing.py` | Every run has a cost, traced or not. Langfuse's own pricing is the cross-check (it matched all six measured runs to within $0.0000004), not the source. |
| Per-turn pricing | Each turn at the model in its `served_by` | A refusal fallback can serve a turn on a model with different prices. Summing usage first could not price that. The pricing page doesn't say how fallbacks are billed, so this is an assumption. |
| Unknown model | `cost_usd` null, and `detail` names the model | Never a guessed figure. |
| Price table | Opus 5.5 $4 / $20, 5-minute cache write $5, cache read $0.20 per million; Opus 5 and Opus 4.8, Sonnet 5.5 and Haiku 5.5 (≤ 100K) also listed | From the pricing page, read 2026-11-20 (`pricing.VERIFIED`). Update the table, its date and `docs/cost.md` together. |

## Alternatives considered

| Alternative | Why not |
|---|---|
| The SDK's beta tool runner with `anthropic.lib.tools.mcp` helpers | Less code, but ADR-001 chose a visible hand-written loop. The helpers target the 1.x `ClientSession` and pass the model the text copy of each result. |
| A forced `submit_answer` tool | A 400 on Opus 5.5. An unforced one is weaker than a schema-guaranteed final turn. |
| Streaming | Answers are short and within non-streaming limits. It can be revisited if long thinking makes requests slow. |
| Fallback off | Simpler eval accounting (a refusal is just a failed run), but it gives up the recommended default. `served_by` makes "on" auditable instead. |

## Consequences

- **The judge** (plan open question 6) should be a different tier from `claude-opus-5-5`, for example `claude-sonnet-5-5`, to reduce self-agreement.
- **A model or effort change is a change to this ADR,** since scores from different settings aren't comparable.
- **Each `incident-mcp` result reaches the model once.** Reading a result costs its structured size in characters (#10's figures).
