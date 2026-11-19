"""Protocol-level coverage: not-found, malformed input and oversized results, for every tool and
the incident:// resource.

One table of cases runs three ways:
- in-process, one test per case, so a failure names its case;
- over a real stdio subprocess against the seed fixtures, all cases in one session, because an
  in-process Client dispatches without JSON-RPC framing and so proves nothing about the wire;
- over a real stdio subprocess serving the worst-case fixtures (every field and count at its
  limit), for the oversized-result cases.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pytest
from mcp import Client, StdioServerParameters
from mcp.shared.exceptions import MCPError
from worst_case import INCIDENT_ID, RUNBOOK_IDS, TICKET_ID, write_worst_case

from incident_triage.fixtures import load_fixtures
from incident_triage.fixtures.models import MAX_LOGS_PER_INCIDENT
from incident_triage.server import build_server
from incident_triage.server.budget import RESPONSE_BUDGET_CHARS

HERE = Path(__file__).parent

Kind = Literal["ok", "tool_error", "protocol_error"]


@dataclass(frozen=True)
class Observed:
    kind: Kind
    text: str = ""
    code: int | None = None
    structured: dict[str, Any] | None = None
    text_chars: int = 0
    structured_chars: int = 0


@dataclass(frozen=True)
class Case:
    name: str
    call: tuple[str, ...] | tuple[str, str, dict[str, Any]]  # ("read", uri) or ("tool", name, args)
    kind: Kind
    fragment: str = ""
    code: int | None = None
    check: Callable[[Observed], bool] = field(default=lambda _: True)

    def problems(self, seen: Observed) -> list[str]:
        found: list[str] = []
        if seen.kind != self.kind:
            found.append(f"expected {self.kind}, got {seen.kind}: {seen.text[:160]!r}")
        if self.fragment and self.fragment not in seen.text:
            found.append(f"expected {self.fragment!r} in {seen.text[:160]!r}")
        if self.code is not None and seen.code != self.code:
            found.append(f"expected code {self.code}, got {seen.code}")
        if seen.kind == "ok" and not self.check(seen):
            found.append("check failed on the result")
        size = max(seen.text_chars, seen.structured_chars, len(seen.text))
        if size > RESPONSE_BUDGET_CHARS:  # errors too: an echoed input must not make one huge
            found.append(f"response is {size} chars, over the {RESPONSE_BUDGET_CHARS} budget")
        return found


async def observe(client: Client, call: tuple[Any, ...]) -> Observed:
    if call[0] == "read":
        try:
            result = await client.read_resource(call[1])
        except MCPError as exc:
            return Observed("protocol_error", text=exc.message, code=exc.code)
        text = "".join(getattr(c, "text", "") for c in result.contents)
        return Observed("ok", text=text, text_chars=len(text))
    _, tool, arguments = call
    try:
        result = await client.call_tool(tool, arguments)
    except MCPError as exc:
        return Observed("protocol_error", text=exc.message, code=exc.code)
    text = " ".join(getattr(block, "text", "") for block in result.content)
    structured = result.structured_content
    return Observed(
        "tool_error" if result.is_error else "ok",
        text=text,
        structured=structured,
        text_chars=sum(len(getattr(block, "text", "")) for block in result.content),
        structured_chars=len(json.dumps(structured)) if structured is not None else 0,
    )


def tool(name: str, **arguments: Any) -> tuple[str, str, dict[str, Any]]:
    return ("tool", name, arguments)


def within_budget(seen: Observed) -> bool:
    return max(seen.text_chars, seen.structured_chars) <= RESPONSE_BUDGET_CHARS


def _sc(seen: Observed) -> dict[str, Any]:
    assert seen.structured is not None
    return seen.structured


PATTERN = "should match pattern"
INVALID_PARAMS = -32602

SEED_CASES: list[Case] = [
    # search_logs
    Case(
        "search: hit",
        tool("search_logs", incident_id="INC-0001", query="pool"),
        "ok",
        check=lambda s: _sc(s)["total_matches"] > 0,
    ),
    Case(
        "search: no match is ok and empty",
        tool("search_logs", incident_id="INC-0001", query="kubernetes"),
        "ok",
        check=lambda s: _sc(s)["total_matches"] == 0 and _sc(s)["records"] == [],
    ),
    Case(
        "search: unknown incident",
        tool("search_logs", incident_id="INC-0099"),
        "tool_error",
        "unknown incident_id INC-0099",
    ),
    Case(
        "search: malformed incident id",
        tool("search_logs", incident_id="INC-1"),
        "tool_error",
        PATTERN,
    ),
    Case(
        "search: wrong-kind id", tool("search_logs", incident_id="TKT-0001"), "tool_error", PATTERN
    ),
    Case("search: wrong type id", tool("search_logs", incident_id=1), "tool_error", "valid string"),
    Case("search: missing id", tool("search_logs"), "tool_error", "Field required"),
    Case(
        "search: unknown argument",
        tool("search_logs", incident_id="INC-0001", levl="ERROR"),
        "tool_error",
        "unknown argument 'levl' for search_logs",
    ),
    Case(
        "search: oversized query",
        tool("search_logs", incident_id="INC-0001", query="x" * 201),
        "tool_error",
        "at most 200",
    ),
    Case(
        "search: limit above max",
        tool("search_logs", incident_id="INC-0001", limit=51),
        "tool_error",
        "less than or equal to 50",
    ),
    Case(
        "search: limit below min",
        tool("search_logs", incident_id="INC-0001", limit=0),
        "tool_error",
        "greater than or equal to 1",
    ),
    Case(
        "search: wrong type limit",
        tool("search_logs", incident_id="INC-0001", limit="ten"),
        "tool_error",
        "valid integer",
    ),
    Case(
        "search: negative offset",
        tool("search_logs", incident_id="INC-0001", offset=-1),
        "tool_error",
        "greater than or equal to 0",
    ),
    Case(
        "search: unknown level",
        tool("search_logs", incident_id="INC-0001", level="WARNING"),
        "tool_error",
        "Input should be",
    ),
    Case(
        "search: seed page within budget",
        tool("search_logs", incident_id="INC-0001", limit=50),
        "ok",
        check=lambda s: within_budget(s) and _sc(s)["limited_by"] == "end",
    ),
    # get_ticket
    Case(
        "ticket: hit",
        tool("get_ticket", ticket_id="TKT-0001"),
        "ok",
        check=lambda s: _sc(s)["id"] == "TKT-0001" and within_budget(s),
    ),
    Case(
        "ticket: unknown",
        tool("get_ticket", ticket_id="TKT-0099"),
        "tool_error",
        "unknown ticket_id TKT-0099",
    ),
    Case("ticket: malformed", tool("get_ticket", ticket_id="ticket 1"), "tool_error", PATTERN),
    Case(
        "ticket: wrong-kind id", tool("get_ticket", ticket_id="RB-DB-POOL"), "tool_error", PATTERN
    ),
    Case("ticket: wrong type", tool("get_ticket", ticket_id=1), "tool_error", "valid string"),
    Case("ticket: null id", tool("get_ticket", ticket_id=None), "tool_error", "valid string"),
    Case("ticket: missing id", tool("get_ticket"), "tool_error", "Field required"),
    Case(
        "ticket: unknown argument",
        tool("get_ticket", ticket_id="TKT-0001", drop_tables=True),
        "tool_error",
        "unknown argument 'drop_tables' for get_ticket",
    ),
    Case(
        "ticket: oversized id",
        tool("get_ticket", ticket_id="TKT-" + "0" * 20_000),
        "tool_error",
        PATTERN,
    ),
    # get_runbook
    Case(
        "runbook: hit",
        tool("get_runbook", runbook_id="RB-DB-POOL"),
        "ok",
        check=lambda s: _sc(s)["id"] == "RB-DB-POOL" and within_budget(s),
    ),
    Case(
        "runbook: unknown",
        tool("get_runbook", runbook_id="RB-NOPE"),
        "tool_error",
        "unknown runbook_id RB-NOPE",
    ),
    Case("runbook: malformed", tool("get_runbook", runbook_id="rb-db-pool"), "tool_error", PATTERN),
    Case(
        "runbook: wrong-kind id", tool("get_runbook", runbook_id="TKT-0001"), "tool_error", PATTERN
    ),
    Case(
        "runbook: wrong type",
        tool("get_runbook", runbook_id=["RB-DB-POOL"]),
        "tool_error",
        "valid string",
    ),
    Case("runbook: missing id", tool("get_runbook"), "tool_error", "Field required"),
    Case(
        "runbook: unknown argument",
        tool("get_runbook", runbook_id="RB-DB-POOL", verbose=True),
        "tool_error",
        "unknown argument 'verbose' for get_runbook",
    ),
    Case(
        "runbook: oversized id",
        tool("get_runbook", runbook_id="RB-" + "X" * 20_000),
        "tool_error",
        "at most 40",
    ),
    Case(
        "search: oversized unknown-argument name is clipped",
        tool("search_logs", incident_id="INC-0001", **{"k" * 20_000: 1}),
        "tool_error",
        "unknown argument 'kkkk",
    ),
    Case(
        "search: many unknown arguments are capped",
        tool("search_logs", incident_id="INC-0001", **{f"a{i}": i for i in range(500)}),
        "tool_error",
        "more",
    ),
    # incident:// resource
    Case(
        "resource: hit",
        ("read", "incident://INC-0002"),
        "ok",
        check=lambda s: json.loads(s.text)["id"] == "INC-0002" and within_budget(s),
    ),
    Case(
        "resource: unknown incident",
        ("read", "incident://INC-0099"),
        "protocol_error",
        "unknown incident_id INC-0099",
        INVALID_PARAMS,
    ),
    Case(
        "resource: malformed id",
        ("read", "incident://inc-0001"),
        "protocol_error",
        "malformed incident_id 'inc-0001'",
        INVALID_PARAMS,
    ),
    Case(
        "resource: empty id",
        ("read", "incident://"),
        "protocol_error",
        "malformed incident_id ''",
        INVALID_PARAMS,
    ),
    Case(
        "resource: extra path",
        ("read", "incident://INC-0001/logs"),
        "protocol_error",
        "Unknown resource",
        INVALID_PARAMS,
    ),
    Case(
        "resource: path traversal",
        ("read", "incident://../etc/passwd"),
        "protocol_error",
        "Unknown resource",
        INVALID_PARAMS,
    ),
    Case(
        "resource: wrong scheme",
        ("read", "ticket://TKT-0001"),
        "protocol_error",
        "Unknown resource",
        INVALID_PARAMS,
    ),
    Case(
        "resource: oversized id",
        ("read", "incident://" + "x" * 20_000),
        "protocol_error",
        "malformed incident_id",
        INVALID_PARAMS,
    ),
    # unknown tool
    Case(
        "unknown tool", tool("delete_everything"), "tool_error", "Unknown tool: delete_everything"
    ),
]

WORST_CASES: list[Case] = [
    Case(
        "worst: search page capped by size",
        tool("search_logs", incident_id=INCIDENT_ID, limit=50),
        "ok",
        check=lambda s: (
            within_budget(s)
            and _sc(s)["limited_by"] == "size"
            and _sc(s)["returned"] >= 1
            and _sc(s)["next_offset"] == _sc(s)["returned"]
        ),
    ),
    Case(
        "worst: search one record",
        tool("search_logs", incident_id=INCIDENT_ID, limit=1),
        "ok",
        check=lambda s: within_budget(s) and _sc(s)["returned"] == 1,
    ),
    Case(
        "worst: ticket within budget",
        tool("get_ticket", ticket_id=TICKET_ID),
        "ok",
        check=within_budget,
    ),
    Case(
        "worst: runbook within budget",
        tool("get_runbook", runbook_id=RUNBOOK_IDS[0]),
        "ok",
        check=within_budget,
    ),
    Case(
        "worst: overview within budget",
        ("read", f"incident://{INCIDENT_ID}"),
        "ok",
        check=within_budget,
    ),
]


@pytest.fixture(scope="module")
def worst_case_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_worst_case(tmp_path_factory.mktemp("worst_case"))


# --- in-process: one test per case -----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("case", SEED_CASES, ids=lambda c: c.name)
async def test_seed_case_in_process(case: Case) -> None:
    async with Client(build_server()) as client:
        seen = await observe(client, case.call)
    assert case.problems(seen) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case", WORST_CASES, ids=lambda c: c.name)
async def test_worst_case_in_process(case: Case, worst_case_root: Path) -> None:
    async with Client(build_server(load_fixtures(worst_case_root))) as client:
        seen = await observe(client, case.call)
    assert case.problems(seen) == [], seen.text[:200]


@pytest.mark.asyncio
async def test_worst_case_pages_walk_every_record_within_budget(worst_case_root: Path) -> None:
    seen_ids: list[str] = []
    offset: int | None = 0
    async with Client(build_server(load_fixtures(worst_case_root))) as client:
        while offset is not None:
            page = await observe(
                client, tool("search_logs", incident_id=INCIDENT_ID, limit=50, offset=offset)
            )
            assert page.kind == "ok" and within_budget(page)
            seen_ids.extend(record["id"] for record in _sc(page)["records"])
            offset = _sc(page)["next_offset"]
    assert len(seen_ids) == len(set(seen_ids)) == MAX_LOGS_PER_INCIDENT


# --- over real stdio: every case in one session ----------------------------------------------


async def _run_over_stdio(
    params: StdioServerParameters, cases: list[Case]
) -> tuple[int, list[str]]:
    ran = 0
    failures: list[str] = []
    async with Client(params) as client:
        for case in cases:
            seen = await observe(client, case.call)
            ran += 1
            failures.extend(f"{case.name}: {problem}" for problem in case.problems(seen))
    return ran, failures


@pytest.mark.asyncio
async def test_seed_cases_over_stdio() -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "incident_triage.server"])
    ran, failures = await _run_over_stdio(params, SEED_CASES)
    assert failures == []
    assert ran == len(SEED_CASES), "the session ended before every case ran"


@pytest.mark.asyncio
async def test_worst_cases_over_stdio(worst_case_root: Path) -> None:
    serve_worst_case = (
        "import sys; from incident_triage.fixtures import load_fixtures; "
        "from incident_triage.server import build_server; "
        "build_server(load_fixtures(__import__('pathlib').Path(sys.argv[1]))).run()"
    )
    params = StdioServerParameters(
        command=sys.executable, args=["-c", serve_worst_case, str(worst_case_root)]
    )
    ran, failures = await _run_over_stdio(params, WORST_CASES)
    assert failures == []
    assert ran == len(WORST_CASES), "the session ended before every case ran"


def test_case_names_are_unique() -> None:
    names = [case.name for case in (*SEED_CASES, *WORST_CASES)]
    assert len(names) == len(set(names))


def _iter_surfaces() -> Iterator[str]:
    yield from ("search_logs", "get_ticket", "get_runbook")


def test_every_tool_has_not_found_malformed_and_unknown_argument_cases() -> None:
    by_tool: dict[str, set[str]] = {name: set() for name in _iter_surfaces()}
    for case in SEED_CASES:
        if case.call[0] == "tool" and case.call[1] in by_tool:
            by_tool[case.call[1]].add(case.fragment)
    for name, fragments in by_tool.items():
        assert any(f.startswith("unknown ") and "_id" in f for f in fragments), f"{name}: not-found"
        assert PATTERN in fragments or "valid string" in fragments, f"{name}: malformed"
        assert any(f.startswith("unknown argument") for f in fragments), f"{name}: unknown argument"
