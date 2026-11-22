"""`python -m incident_triage.golden validate [--golden PATH]` (what `make validate-golden` runs).

Exit 0 when every case is valid (drafts are reported, not errors); exit 1 with every problem on
stderr otherwise. The summary goes to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from incident_triage.fixtures import FixtureError, load_fixtures
from incident_triage.golden.loader import DEFAULT_PATH, load_golden
from incident_triage.golden.models import LabelStatus


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m incident_triage.golden")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Check every golden case against the fixtures.")
    validate.add_argument("--golden", type=Path, default=DEFAULT_PATH, help="default: %(default)s")
    args = parser.parse_args(argv)

    try:
        store = load_fixtures()
    except FixtureError as exc:
        sys.stderr.write(f"fixtures invalid: {exc}\n")
        return 1
    golden = load_golden(args.golden, store)
    for problem in golden.problems:
        sys.stderr.write(f"{problem}\n")
    drafts = sorted(c.id for c in golden.cases.values() if c.label_status is LabelStatus.DRAFT)
    summary = golden.summary() | {"problems": len(golden.problems), "drafts": drafts}
    sys.stdout.write(json.dumps(summary, indent=2) + "\n")
    return 0 if golden.ok else 1


if __name__ == "__main__":
    sys.exit(main())
