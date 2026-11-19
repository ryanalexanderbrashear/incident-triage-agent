"""Load and cross-check the fixture files into an immutable, in-memory store.

The loader is pure: it never logs or prints (stdout is the MCP protocol channel under stdio).
It raises FixtureError, whose message is `<path>:<line>: <id>: <problem>`, and leaves reporting
to the caller.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from types import MappingProxyType
from typing import Literal, TypeVar, cast

from pydantic import BaseModel, ValidationError

from incident_triage.fixtures.models import (
    MAX_LOGS_PER_INCIDENT,
    MAX_SERVICES_PER_INCIDENT,
    Incident,
    LogRecord,
    Runbook,
    Ticket,
)

FixtureKind = Literal["incident", "log", "ticket", "runbook"]
_Model = TypeVar("_Model", bound=BaseModel)
_Register = Callable[[str, "FixtureKind", str], None]


class FixtureError(ValueError):
    """A fixture file is malformed or inconsistent. The message locates the problem."""

    def __init__(self, path: str, problem: str, *, line: int | None = None, id: str = "-"):
        self.path = path
        self.line = line
        self.id = id
        self.problem = problem
        where = f"{path}:{line}" if line is not None else path
        super().__init__(f"{where}: {id}: {problem}")


def default_root() -> Traversable:
    """The fixture data packaged with incident_triage, wherever the process was started."""
    return files("incident_triage.fixtures") / "data"


@dataclass(frozen=True)
class FixtureStore:
    incidents: Mapping[str, Incident]
    logs: Mapping[str, tuple[LogRecord, ...]]  # incident ID -> its logs, in timestamp order
    tickets: Mapping[str, Ticket]
    runbooks: Mapping[str, Runbook]
    _kinds: Mapping[str, FixtureKind]

    def logs_for(self, incident_id: str) -> tuple[LogRecord, ...]:
        return self.logs.get(incident_id, ())

    def ticket(self, ticket_id: str) -> Ticket | None:
        return self.tickets.get(ticket_id)

    def runbook(self, runbook_id: str) -> Runbook | None:
        return self.runbooks.get(runbook_id)

    def kind_of(self, fixture_id: str) -> FixtureKind | None:
        """What an ID refers to, or None if no fixture has it. For the golden-set validator."""
        return self._kinds.get(fixture_id)

    def counts(self) -> dict[str, int]:
        return {
            "incidents": len(self.incidents),
            "logs": sum(len(records) for records in self.logs.values()),
            "tickets": len(self.tickets),
            "runbooks": len(self.runbooks),
        }


def load_fixtures(root: Traversable | Path | None = None) -> FixtureStore:
    """Load every fixture under `root` (default: the packaged data) and cross-check it."""
    base: Traversable = default_root() if root is None else root
    kinds: dict[str, FixtureKind] = {}
    first_seen: dict[str, str] = {}

    def register(fixture_id: str, kind: FixtureKind, where: str) -> None:
        if fixture_id in kinds:
            raise FixtureError(
                where, f"duplicate ID, first defined in {first_seen[fixture_id]}", id=fixture_id
            )
        kinds[fixture_id] = kind
        first_seen[fixture_id] = where

    runbooks: dict[str, Runbook] = {}
    for path, rel in _children(base, "runbooks", suffix=".json"):
        runbook = _parse(Runbook, _read_json(path, rel), rel)
        if path.name != f"{runbook.id}.json":
            raise FixtureError(rel, f"file name must be {runbook.id}.json", id=runbook.id)
        register(runbook.id, "runbook", rel)
        runbooks[runbook.id] = runbook

    incidents: dict[str, Incident] = {}
    logs: dict[str, tuple[LogRecord, ...]] = {}
    tickets: dict[str, Ticket] = {}
    for directory, dir_rel in _children(base, "incidents", dirs=True):
        incident_rel = f"{dir_rel}/incident.json"
        incident = _parse(
            Incident, _read_json(directory / "incident.json", incident_rel), incident_rel
        )
        if directory.name != incident.id:
            raise FixtureError(
                incident_rel, f"directory must be named {incident.id}", id=incident.id
            )
        register(incident.id, "incident", incident_rel)
        for runbook_id in incident.runbook_ids:
            if runbook_id not in runbooks:
                raise FixtureError(incident_rel, f"unknown runbook {runbook_id}", id=incident.id)
        incidents[incident.id] = incident

        logs[incident.id] = _load_logs(
            directory / "logs.jsonl", f"{dir_rel}/logs.jsonl", incident, register
        )

        ticket_file = directory / "ticket.json"
        if ticket_file.is_file():
            ticket_rel = f"{dir_rel}/ticket.json"
            ticket = _parse(Ticket, _read_json(ticket_file, ticket_rel), ticket_rel)
            if ticket.incident_id != incident.id:
                raise FixtureError(ticket_rel, f"incident_id must be {incident.id}", id=ticket.id)
            register(ticket.id, "ticket", ticket_rel)
            tickets[ticket.id] = ticket

    return FixtureStore(
        incidents=MappingProxyType(incidents),
        logs=MappingProxyType(logs),
        tickets=MappingProxyType(tickets),
        runbooks=MappingProxyType(runbooks),
        _kinds=MappingProxyType(kinds),
    )


def _load_logs(
    path: Traversable, rel: str, incident: Incident, register: _Register
) -> tuple[LogRecord, ...]:
    if not path.is_file():
        raise FixtureError(rel, "missing", id=incident.id)
    expected_prefix = f"LOG-{incident.id.removeprefix('INC-')}-"
    records: list[LogRecord] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FixtureError(rel, f"invalid JSON: {exc.msg}", line=line_no) from None
        record = _parse(LogRecord, raw, rel, line=line_no)
        if record.incident_id != incident.id:
            raise FixtureError(
                rel, f"incident_id must be {incident.id}", line=line_no, id=record.id
            )
        if not record.id.startswith(expected_prefix):
            raise FixtureError(
                rel,
                f"log IDs in this file must start {expected_prefix}",
                line=line_no,
                id=record.id,
            )
        if records and record.timestamp < records[-1].timestamp:
            raise FixtureError(
                rel,
                f"timestamp earlier than previous line ({records[-1].id})",
                line=line_no,
                id=record.id,
            )
        register(record.id, "log", f"{rel}:{line_no}")
        records.append(record)
        if len(records) > MAX_LOGS_PER_INCIDENT:
            raise FixtureError(
                rel, f"more than {MAX_LOGS_PER_INCIDENT} log records", line=line_no, id=record.id
            )
    if not records:
        raise FixtureError(rel, "no log records", id=incident.id)
    if records[0].timestamp > incident.alert.fired_at:
        raise FixtureError(rel, "no log at or before the alert's fired_at", id=incident.id)
    services = {record.service for record in records}
    if len(services) > MAX_SERVICES_PER_INCIDENT:
        raise FixtureError(
            rel,
            f"{len(services)} distinct services; at most {MAX_SERVICES_PER_INCIDENT}",
            id=incident.id,
        )
    return tuple(records)


def _children(
    base: Traversable, name: str, *, suffix: str = "", dirs: bool = False
) -> Iterator[tuple[Traversable, str]]:
    parent = base / name
    if not parent.is_dir():
        raise FixtureError(name, "missing directory")
    for child in sorted(parent.iterdir(), key=lambda c: c.name):
        if child.name.startswith("."):
            continue
        wanted_file = not dirs and child.is_file() and child.name.endswith(suffix)
        if (dirs and child.is_dir()) or wanted_file:
            yield child, f"{name}/{child.name}"


def _read_json(path: Traversable, rel: str) -> object:
    if not path.is_file():
        raise FixtureError(rel, "missing")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FixtureError(rel, f"invalid JSON: {exc.msg}", line=exc.lineno) from None


def _parse(model: type[_Model], raw: object, rel: str, *, line: int | None = None) -> _Model:
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        fixture_id = _id_of(raw)
        problems = "; ".join(
            f"{'.'.join(str(part) for part in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        )
        raise FixtureError(rel, problems, line=line, id=str(fixture_id)) from None


def _id_of(raw: object) -> str:
    """The `id` field of a raw fixture, for error messages, or "-" when there is none."""
    if isinstance(raw, dict):
        value = cast(dict[str, object], raw).get("id")
        if isinstance(value, str):
            return value
    return "-"
