"""The triage agent: a hand-written tool-use loop over incident-mcp (ADR-001, ADR-002)."""

from incident_triage.agent.answer import RootCauseCategory, TriageAnswer
from incident_triage.agent.loop import RunRecord, run_triage

__all__ = ["RootCauseCategory", "RunRecord", "TriageAnswer", "run_triage"]
