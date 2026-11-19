# ADR-001: Stack, pinned versions and rejected alternatives

**Status:** Accepted
**Date:** 2026-11-19 (versions checked on PyPI that day)
**Issue:** [#2](https://github.com/ryanalexanderbrashear/incident-triage-agent/issues/2)

## Context

The project shows an MCP server, a golden-dataset eval harness and Langfuse tracing working together (see [PRODUCT.md](../../PRODUCT.md) and [the project plan](../projects/incident-triage-agent.md)). Its three SDKs are on major versions newer than most tutorials and training data: `mcp` 2.x renamed the server class, and `langfuse` 5.x replaced its client API with an OpenTelemetry-based one. Code written from memory of earlier majors will be wrong in places. This record fixes what is used, at which version, why, and what was turned down.

## Decision

### Versions

| Package | Pin | Role | Notes |
|---|---|---|---|
| Python | ≥ 3.11 (`.python-version`: 3.11) | runtime | 3.11 is `langfuse` 5's floor. |
| `uv` | 0.13.9 (CI) | env + lockfile | `uv.lock` committed; CI installs with `uv sync --locked`. |
| `mcp` | 2.3.0 | server and client | Server class `mcp.server.MCPServer` (renamed from `FastMCP`); client `mcp.Client`, where a URL argument means Streamable HTTP. [Docs](https://py.sdk.modelcontextprotocol.io/). |
| `langfuse` | 5.2.0 | tracing, datasets, scores | OpenTelemetry-based. Use `get_client()` with `start_as_current_observation` or `@observe`; the v2 `trace()` / `span()` / `generation()` API is gone. [Docs](https://langfuse.com/docs/observability/sdk/overview). |
| `anthropic` | 2.11.0 | model calls | Messages API with tool use. |
| `pytest` | 9.2.0 | tests | |
| `pytest-asyncio` | 1.5.0 | async tests | |
| `ruff` | 0.17.6 | lint + format | |
| `pyright` | 1.1.414 | type checker | See below. |

Runtime packages are exact-pinned in `pyproject.toml`, and `uv.lock` pins everything transitively. [`tests/test_stack.py`](../../tests/test_stack.py) asserts the three SDK versions match this table, and that the API names above exist. On 2026-11-19, all of them were verified by installing this set on Python 3.11 and importing them.

**Changing a pin:** edit this table, `pyproject.toml` and `tests/test_stack.py`, then run `uv lock`, all in the same pull request.

### Type checker: pyright

`standard` mode across `src` and `tests`. The MCP server and grader packages are added to pyright's `strict` list when each first lands, because those are the code whose types are contracts: tool schemas and grade results. This resolves the plan's open question 8.

### Architecture

- **A hand-written agent loop on the Anthropic SDK**, with the MCP `Client` supplying the tools. It lists the server's tools, passes them to the model, and runs the tool-use loop to a structured answer.
- **A new MCP server (`incident-mcp`)**, with three read-only tools (`search_logs`, `get_ticket`, `get_runbook`) and one resource, over stdio and Streamable HTTP. It logs to stderr only.
- **Layered grading.** Deterministic graders decide pass/fail and gate CI. A model-graded judge scores explanation quality only, and never gates.
- **The golden set is JSONL in the repository**, the source of truth, and is synced to a Langfuse dataset. Labels are hand-verified.
- **A thin eval harness of our own, by default.** The plan's open question 5 is still open: a one-hour spike early in M2 may replace this with `pydantic-evals` or Langfuse's experiment runner. If it does, that is recorded as a new ADR that supersedes this bullet.

## Alternatives considered

| Alternative | Why not |
|---|---|
| LangChain or LangGraph for the agent | It hides the tool loop and the trace shape, which are what this project shows. Revisit only if the hand-written loop becomes a time sink in M1. |
| Wrapping an existing MCP server | The brief is to build one. A wrapper shows less than a server with its own schemas, limits and error design. |
| `pydantic-evals` or Inspect as the harness core | A thin harness is about 200 lines and shows the grader design directly. Pending the M2 spike (open question 5). |
| Golden set only in Langfuse | It could not be reviewed in a pull request or reproduced from a clone. |
| A real log backend or SQLite for fixtures | In-memory files are enough for a few hundred lines per incident, and need no services. |
| SSE transport | Stdio and Streamable HTTP cover local and served use. |
| mypy | Either would do: both can be strict per path. pyright was the plan's recommendation and is already in CI from #1. This is a preference, not a measured finding. |
| `venv` + `pip` | Works, but loses the lockfile and reproducible installs. |
| Langfuse's v2 client API (`trace()`, `span()`, `generation()`) | Removed in 5.x. Most examples online still use it. |

## Consequences

### Intel Macs get an older `cryptography`

`mcp` 2.3.0 depends on `pyjwt[crypto]` 2.15.1, which depends on `cryptography`. Since 49.0.0 (2026-06-12), `cryptography` has shipped macOS wheels only for Apple Silicon. On an Intel Mac, uv falls back to building it from source, and that fails without OpenSSL headers and `pkg-config` (`openssl-sys`: "Could not find directory of OpenSSL installation").

`pyproject.toml` therefore carries two complementary `[tool.uv] constraint-dependencies`:

- `cryptography<49` when `sys_platform == 'darwin' and platform_machine == 'x86_64'`, which resolves to 48.0.1, the last release with a `universal2` wheel;
- `cryptography>=50` everywhere else.

One constraint alone is not enough. uv picks a single version that satisfies every platform when it can, so `<49` by itself pins 48.0.1 everywhere, CI included. The pair forces a split, and `uv.lock` holds both versions.

When testing a change to these constraints, delete `uv.lock` first. uv keeps the versions an existing lockfile already chose, so removing a constraint against the old lockfile appears to change nothing.

The project uses `cryptography` only transitively. CI, on Linux with 50.0.2, is the reference environment.

**Revisit when:** the project stops needing to support Intel Macs, or a `cryptography` security fix matters to this project and is not released on 48.x. The fallback is to drop the first constraint and build from source on Intel Macs (`brew install openssl pkgconf`; a Rust toolchain is fetched automatically during the build).

### Other consequences

- `mcp`, `langfuse` and `anthropic` are installed before the code that imports them exists (#4 onwards). That is deliberate: the pins are enforced and proven installable from the start.
- Anything written from memory of `mcp` 1.x or `langfuse` 2.x–3.x must be checked against the docs linked above.
