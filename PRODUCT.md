# Product

## Users

Engineers reviewing the author's AI-engineering work, such as hiring managers and technical interviewers. A second audience is engineers looking for a small, readable reference that shows how an MCP server, a golden-dataset eval harness and Langfuse tracing fit together in one Python project.

## Product Purpose

A small incident-triage agent. Given an alert, it uses a purpose-built MCP server (logs, tickets and runbooks over synthetic data) to propose a root cause and cite the evidence for it.

The project exists as a worked example of building, measuring and observing an agent. It succeeds when every behavioural claim in its README is backed by an eval score or a trace that anyone can reproduce.

## Positioning

A reference implementation, not a product. What sets it apart is that each claim is checkable. The golden dataset is in the repository, every run produces per-case scores, and every agent run produces a trace.

## Capabilities and Constraints

- Python 3.11+; the MCP server is written from scratch for this project.
- The server is read-only, with one planned exception: a single simulated write tool that writes only to a local sink and is gated by dry-run, confirmation, idempotency and an audit log. It never touches a real chat, paging or ticketing product.
- All data is synthetic. No real logs, tickets or employer material appear in the repository, in prompts or in traces.
- The agent talks to the server over MCP, over stdio locally and Streamable HTTP when served.

## Evidence on Hand

None yet. Eval scores, calibration figures and trace screenshots are added as runs produce them. Never write a number here or in the README that a run did not produce.
