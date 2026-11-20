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

### Root-cause categories (provisional until #11)

`config_or_deploy_change`, `resource_exhaustion`, `dependency_failure`, `expired_credential`, `code_defect`, `infrastructure`, `cannot_determine`. **Labelling rule:** the category is the trigger, the thing whose reversal ends the incident. The enum lives in `agent/answer.py`, so #11's golden labels import the same spelling.

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
