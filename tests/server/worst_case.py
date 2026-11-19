"""A fixture tree with every field and count at its model limit: the largest the server can serve.

Built from the constants in incident_triage.fixtures.models, so raising a limit there raises it
here, and the budget tests in test_protocol.py say whether the response budget still holds.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from incident_triage.fixtures import models as m

INCIDENT_ID = "INC-0001"
TICKET_ID = "TKT-0001"
# Runbook IDs at their maximum length; the first is the one get_runbook is measured on.
RUNBOOK_IDS = [f"RB-{i:02d}-" + "X" * (40 - 6) for i in range(m.MAX_RUNBOOKS_PER_INCIDENT)]


def _text(n: int, char: str = "x") -> str:
    return char * n


def _service(i: int) -> str:
    stem = f"svc{i:02d}-"
    return stem + "s" * (m.MAX_SERVICE_NAME_CHARS - len(stem))


def _log(n: int) -> dict[str, Any]:
    # Timestamps 00:00:00 .. spread across the day, in order; all before the alert at 23:59:59.
    seconds = n  # n <= 1000, so this stays within the first hour
    return {
        "id": f"LOG-0001-{n:04d}",
        "incident_id": INCIDENT_ID,
        "timestamp": f"2026-01-01T00:{seconds // 60:02d}:{seconds % 60:02d}Z",
        "service": _service(n % m.MAX_SERVICES_PER_INCIDENT),
        "level": "ERROR",
        "message": _text(m.MAX_LOG_MESSAGE_CHARS),
        "attributes": {
            f"{i:02d}" + "k" * (m.MAX_ATTRIBUTE_KEY_CHARS - 2): _text(m.MAX_ATTRIBUTE_VALUE_CHARS)
            for i in range(m.MAX_LOG_ATTRIBUTES)
        },
    }


def write_worst_case(root: Path) -> Path:
    """Write the worst-case tree under `root` and return `root`, ready for load_fixtures."""
    short = _text(m.MAX_SHORT_TEXT_CHARS)
    files: dict[str, Any] = {
        f"incidents/{INCIDENT_ID}/incident.json": {
            "id": INCIDENT_ID,
            "title": short,
            "service": _service(0),
            "severity": "SEV1",
            "alert": {
                "fired_at": "2026-01-01T23:59:59Z",
                "summary": short,
                "metric": short,
                "threshold": 1.0e300,
                "observed": -1.0e300,
            },
            "runbook_ids": RUNBOOK_IDS,
        },
        f"incidents/{INCIDENT_ID}/ticket.json": {
            "id": TICKET_ID,
            "incident_id": INCIDENT_ID,
            "title": short,
            "status": "investigating",
            "created_at": "2026-01-02T00:00:00Z",
            "reporter": short,
            "body": _text(m.MAX_TICKET_BODY_CHARS),
            "comments": [
                {
                    "author": short,
                    "created_at": "2026-01-02T00:00:00Z",
                    "body": _text(m.MAX_COMMENT_CHARS),
                }
                for _ in range(m.MAX_TICKET_COMMENTS)
            ],
        },
    }
    for runbook_id in RUNBOOK_IDS:
        files[f"runbooks/{runbook_id}.json"] = {
            "id": runbook_id,
            "title": short,
            "service": _service(0),
            "symptoms": [_text(m.MAX_RUNBOOK_ITEM_CHARS)] * m.MAX_RUNBOOK_SYMPTOMS,
            "steps": [_text(m.MAX_RUNBOOK_ITEM_CHARS)] * m.MAX_RUNBOOK_STEPS,
        }
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content), encoding="utf-8")
    logs = root / f"incidents/{INCIDENT_ID}/logs.jsonl"
    logs.write_text(
        "".join(json.dumps(_log(n)) + "\n" for n in range(1, m.MAX_LOGS_PER_INCIDENT + 1)),
        encoding="utf-8",
    )
    return root
