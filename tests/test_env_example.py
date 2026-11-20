""".env.example lists every variable the code needs, and never carries a value.

The names are read from the code, so adding a required variable without updating the template
fails here. Every value must be empty, so a real key cannot be committed by editing the
template instead of the git-ignored .env.
"""

from __future__ import annotations

from pathlib import Path

from incident_triage.agent.cli import CREDENTIAL_VARIABLES
from incident_triage.agent.tracing import KEY_VARIABLES, URL_VARIABLES

TEMPLATE = Path(__file__).resolve().parents[1] / ".env.example"


def _assignments() -> dict[str, str]:
    """NAME=value lines; comments and blank lines skipped."""
    pairs: dict[str, str] = {}
    for raw in TEMPLATE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        assert sep, f"not a NAME=value line: {raw!r}"
        pairs[name.strip()] = value.strip()
    return pairs


def test_template_lists_every_variable_the_code_requires() -> None:
    # The preferred name of each alternative pair; the alternatives are mentioned in comments.
    required = {CREDENTIAL_VARIABLES[0], *KEY_VARIABLES, URL_VARIABLES[0]}
    assert required == {
        "ANTHROPIC_API_KEY",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_BASE_URL",
    }
    assert required <= set(_assignments())


def test_template_has_no_values() -> None:
    assignments = _assignments()
    assert assignments, "parsed no assignments: the check would pass vacuously"
    assert {name: value for name, value in assignments.items() if value} == {}


def test_alternative_names_are_mentioned_not_assigned() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    for alternative in (CREDENTIAL_VARIABLES[1], URL_VARIABLES[1]):
        assert alternative in text, f"{alternative} should be mentioned in a comment"
        assert alternative not in _assignments()
