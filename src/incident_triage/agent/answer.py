"""The agent's structured answer, and the root-cause categories it chooses from.

The categories are provisional until #11 (golden-case schema), which owns the final set and
imports this enum, so the agent's answers and the golden labels can never disagree on spelling.

Labelling rule: the category is the trigger, the thing whose reversal ends the incident. A deploy
that raises concurrency until a pool runs out is `config_or_deploy_change`, not
`resource_exhaustion`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from incident_triage.fixtures.models import (
    INCIDENT_ID_PATTERN,
    LOG_ID_PATTERN,
    RUNBOOK_ID_PATTERN,
    TICKET_ID_PATTERN,
)

MAX_EXPLANATION_CHARS = 2_000
MAX_EVIDENCE_IDS = 30


class RootCauseCategory(StrEnum):
    CONFIG_OR_DEPLOY_CHANGE = "config_or_deploy_change"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    DEPENDENCY_FAILURE = "dependency_failure"
    EXPIRED_CREDENTIAL = "expired_credential"
    CODE_DEFECT = "code_defect"
    INFRASTRUCTURE = "infrastructure"
    CANNOT_DETERMINE = "cannot_determine"


def _strip_anchors(pattern: str) -> str:
    return pattern.removeprefix("^").removesuffix("$")


EVIDENCE_ID_PATTERN = "^(?:{})$".format(
    "|".join(
        _strip_anchors(p)
        for p in (LOG_ID_PATTERN, TICKET_ID_PATTERN, RUNBOOK_ID_PATTERN, INCIDENT_ID_PATTERN)
    )
)

EvidenceId = Annotated[str, StringConstraints(pattern=EVIDENCE_ID_PATTERN)]


class TriageAnswer(BaseModel):
    """What the agent concludes. Validated client-side, including the bounds the API can't take."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    root_cause_category: RootCauseCategory
    explanation: Annotated[
        str,
        StringConstraints(min_length=1, max_length=MAX_EXPLANATION_CHARS, strip_whitespace=True),
    ]
    evidence_ids: list[EvidenceId] = Field(max_length=MAX_EVIDENCE_IDS)


# The schema sent as output_config.format. Hand-written rather than derived from TriageAnswer,
# because structured outputs reject string-length and array-size constraints and require
# additionalProperties: false on every object; TriageAnswer enforces the bounds after parsing.
# tests/agent/test_answer.py keeps the two in step.
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "root_cause_category": {
            "type": "string",
            "enum": [category.value for category in RootCauseCategory],
            "description": "The trigger: the thing whose reversal ends the incident.",
        },
        "explanation": {
            "type": "string",
            "description": "How the evidence shows the cause, in a few sentences.",
        },
        "evidence_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "IDs of the log records and tickets that prove the cause, "
            "exactly as they appeared in tool results.",
        },
    },
    "required": ["root_cause_category", "explanation", "evidence_ids"],
    "additionalProperties": False,
}
