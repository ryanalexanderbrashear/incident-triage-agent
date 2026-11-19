"""The answer schema sent to the API and the Pydantic model that validates it agree."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from incident_triage.agent.answer import (
    ANSWER_SCHEMA,
    MAX_EVIDENCE_IDS,
    MAX_EXPLANATION_CHARS,
    RootCauseCategory,
    TriageAnswer,
)

UNSUPPORTED_BY_STRUCTURED_OUTPUTS = {
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "minimum",
    "maximum",
}


def _walk(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        found.append(node)  # pyright: ignore[reportUnknownArgumentType]
        for value in node.values():  # pyright: ignore[reportUnknownVariableType]
            found.extend(_walk(value))
    elif isinstance(node, list):
        for value in node:  # pyright: ignore[reportUnknownVariableType]
            found.extend(_walk(value))
    return found


def test_schema_and_model_have_the_same_fields() -> None:
    assert set(ANSWER_SCHEMA["properties"]) == set(TriageAnswer.model_fields)
    assert set(ANSWER_SCHEMA["required"]) == set(TriageAnswer.model_fields)


def test_schema_enum_is_the_category_enum() -> None:
    enum = ANSWER_SCHEMA["properties"]["root_cause_category"]["enum"]
    assert enum == [category.value for category in RootCauseCategory]
    assert "cannot_determine" in enum


def test_schema_uses_only_what_structured_outputs_accepts() -> None:
    nodes = _walk(ANSWER_SCHEMA)
    for node in nodes:
        assert not (set(node) & UNSUPPORTED_BY_STRUCTURED_OUTPUTS), node
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False, node


def _answer(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "root_cause_category": "dependency_failure",
        "explanation": "The upstream failed.",
        "evidence_ids": ["LOG-0001-0001", "TKT-0001", "RB-DB-POOL", "INC-0001"],
    }
    return base | overrides


def test_a_valid_answer_parses() -> None:
    assert TriageAnswer.model_validate(_answer()).evidence_ids[1] == "TKT-0001"


@pytest.mark.parametrize(
    "overrides",
    [
        {"root_cause_category": "dns"},
        {"explanation": ""},
        {"explanation": "x" * (MAX_EXPLANATION_CHARS + 1)},
        {"evidence_ids": ["LOG-1"]},
        {"evidence_ids": ["see the logs"]},
        {"evidence_ids": [f"LOG-0001-{n:04d}" for n in range(MAX_EVIDENCE_IDS + 1)]},
        {"confidence": 0.9},
    ],
    ids=[
        "unknown category",
        "empty explanation",
        "long explanation",
        "malformed id",
        "prose id",
        "too many ids",
        "extra field",
    ],
)
def test_invalid_answers_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        TriageAnswer.model_validate(_answer(**overrides))
