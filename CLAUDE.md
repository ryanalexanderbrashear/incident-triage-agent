# Incident Triage Agent: instructions for Claude Code

Start with [docs/HANDOFF.md](docs/HANDOFF.md). It says where the project stands, how to work, and what has been decided. The reasoning is in [docs/projects/incident-triage-agent.md](docs/projects/incident-triage-agent.md), and the work is in the GitHub issues.

Rules that apply to every change:

- Python 3.11+, managed with `uv`. Lockfile committed.
- Branch from `main`, one pull request per issue, and never base a PR on another branch. Return to `main` and pull before each new branch.
- Plan each issue (as an issue comment) and get the human's approval before writing code.
- Run `ruff check`, `ruff format --check`, the type checker and `pytest` each on their own, and report each exit status. Do not read a gate's result through a pipe.
- Any new test or checker is shown failing against deliberately broken input before it is trusted.
- Keep `README.md` and the project plan true with every change. The PR description names the documents checked.
- The MCP server must never write to stdout (it is the protocol channel under stdio). Log to stderr.
- No secrets in the repo, in issues, in traces or in chat. No real data anywhere: all fixtures are synthetic.
- Claim only what a run produced. Never write a score or figure that no run generated.
