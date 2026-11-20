"""Agent tests never reach a real Anthropic or Langfuse account, whatever the shell exports."""

from __future__ import annotations

import pytest

from incident_triage.agent.tracing import KEY_VARIABLES


@pytest.fixture(autouse=True)
def _no_langfuse_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*KEY_VARIABLES, "LANGFUSE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
