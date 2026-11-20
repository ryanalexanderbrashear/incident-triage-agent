"""A golden case: one fixture incident plus its hand-verified labels (schema version 1).

Golden labels live in golden/cases.jsonl at the repository root, never in the fixtures: the
fixtures are served to the agent, and a label there would be one tool call from leaking.

Only the human sets label_status to "verified" (HANDOFF: "Draft cases, then ask for
verification"). Cases drafted with LLM help stay "draft" until then.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from incident_triage.agent.answer import RootCauseCategory
from incident_triage.fixtures.models import INCIDENT_ID_PATTERN

CASE_ID_PATTERN = r"^GC-\d{4}$"
MAX_RATIONALE_CHARS = 2_000
MAX_LABELLED_IDS = 100

CaseId = Annotated[str, StringConstraints(pattern=CASE_ID_PATTERN)]
IncidentId = Annotated[str, StringConstraints(pattern=INCIDENT_ID_PATTERN)]
# Shape only here; existence, kind and ownership are checked against the fixtures by the loader.
LabelledId = Annotated[str, StringConstraints(min_length=1, max_length=40)]


class Difficulty(StrEnum):
    STRAIGHTFORWARD = "straightforward"  # #12
    HARD = "hard"  # #13: a red herring, or two plausible causes
    ADVERSARIAL = "adversarial"  # #14: cannot determine, or an injected instruction


class LabelStatus(StrEnum):
    DRAFT = "draft"
    VERIFIED = "verified"


class GoldenCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    id: CaseId
    incident_id: IncidentId
    difficulty: Difficulty
    root_cause_category: RootCauseCategory
    # The records that prove the cause: evidence recall is measured against these.
    evidence_ids: tuple[LabelledId, ...] = Field(max_length=MAX_LABELLED_IDS)
    # Records a good answer may also cite without penalty (for example, ones that rule out a red
    # herring): evidence precision counts evidence_ids and supporting_ids as correct.
    supporting_ids: tuple[LabelledId, ...] = Field(default=(), max_length=MAX_LABELLED_IDS)
    # Records carrying an injected instruction (#14), for the injection-resistance grader (#15).
    injection_ids: tuple[LabelledId, ...] = Field(default=(), max_length=MAX_LABELLED_IDS)
    rationale: Annotated[
        str, StringConstraints(min_length=1, max_length=MAX_RATIONALE_CHARS, strip_whitespace=True)
    ]
    label_status: LabelStatus
    verified_on: date | None = None

    @model_validator(mode="after")
    def _consistent(self) -> GoldenCase:
        problems: list[str] = []
        cannot = self.root_cause_category is RootCauseCategory.CANNOT_DETERMINE
        if cannot and self.evidence_ids:
            problems.append("a cannot_determine case has no evidence_ids (nothing proves a cause)")
        if not cannot and not self.evidence_ids:
            problems.append("evidence_ids is required unless the category is cannot_determine")
        for name, ids in (
            ("evidence_ids", self.evidence_ids),
            ("supporting_ids", self.supporting_ids),
            ("injection_ids", self.injection_ids),
        ):
            if len(set(ids)) != len(ids):
                problems.append(f"{name} has duplicates")
        overlap = set(self.evidence_ids) & set(self.supporting_ids)
        if overlap:
            problems.append(
                f"in both evidence_ids and supporting_ids: {', '.join(sorted(overlap))}"
            )
        injected_evidence = set(self.injection_ids) & set(self.evidence_ids)
        if injected_evidence:
            problems.append(
                f"an injected record cannot prove the cause: {', '.join(sorted(injected_evidence))}"
            )
        if self.label_status is LabelStatus.VERIFIED and self.verified_on is None:
            problems.append("a verified case needs verified_on (the date the human verified it)")
        if self.label_status is LabelStatus.DRAFT and self.verified_on is not None:
            problems.append("a draft case has no verified_on")
        if problems:
            raise ValueError("; ".join(problems))
        return self
