# Each recipe line runs, and fails, on its own; make stops at the first failure.
.PHONY: validate-golden check

validate-golden:  ## Check golden/cases.jsonl against the fixtures (drafts are listed, not errors)
	uv run python -m incident_triage.golden validate

check:  ## The four gates, one after another
	uv run ruff check
	uv run ruff format --check
	uv run pyright
	uv run pytest
