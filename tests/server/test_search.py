"""search_logs as a pure function over the packaged seed fixtures."""

from __future__ import annotations

import pytest

from incident_triage.fixtures import FixtureStore, LogLevel, load_fixtures
from incident_triage.server import search as search_module
from incident_triage.server.search import UnknownIncidentError, search_logs


@pytest.fixture(scope="module")
def store() -> FixtureStore:
    return load_fixtures()


def test_empty_query_returns_first_page_in_timestamp_order(store: FixtureStore) -> None:
    result = search_logs(store, incident_id="INC-0001", limit=5)
    assert result.total_matches == len(store.logs_for("INC-0001"))
    assert result.returned == 5
    assert [r.id for r in result.records] == [f"LOG-0001-{n:04d}" for n in range(1, 6)]
    timestamps = [r.timestamp for r in result.records]
    assert timestamps == sorted(timestamps)


def test_terms_are_anded_and_case_insensitive(store: FixtureStore) -> None:
    both = search_logs(store, incident_id="INC-0001", query="TIMED db")
    assert both.total_matches > 0
    for record in both.records:
        assert "timed" in record.message.lower()
    only_one = search_logs(store, incident_id="INC-0001", query="timed nonexistentterm")
    assert only_one.total_matches == 0


def test_query_matches_attribute_keys_and_values(store: FixtureStore) -> None:
    by_value = search_logs(store, incident_id="INC-0001", query="WORKER_CONCURRENCY")
    assert [r.message for r in by_value.records] == ["config change applied"]
    by_key = search_logs(store, incident_id="INC-0002", query="not_after")
    assert by_key.total_matches == 1


def test_no_match_is_an_empty_result(store: FixtureStore) -> None:
    result = search_logs(store, incident_id="INC-0002", query="kubernetes")
    assert (result.total_matches, result.returned, result.next_offset, result.records) == (
        0,
        0,
        None,
        [],
    )


def test_level_is_a_minimum(store: FixtureStore) -> None:
    result = search_logs(store, incident_id="INC-0001", level=LogLevel.ERROR, limit=50)
    assert result.total_matches > 0
    assert {r.level for r in result.records} <= {LogLevel.ERROR, LogLevel.FATAL}
    warn_and_up = search_logs(store, incident_id="INC-0001", level=LogLevel.WARN, limit=50)
    assert warn_and_up.total_matches > result.total_matches


def test_service_filter_is_exact(store: FixtureStore) -> None:
    result = search_logs(store, incident_id="INC-0001", service="orders-db-proxy", limit=50)
    assert result.total_matches > 0
    assert {r.service for r in result.records} == {"orders-db-proxy"}
    assert search_logs(store, incident_id="INC-0001", service="orders-db").total_matches == 0


def test_pagination_walks_every_match_once(store: FixtureStore) -> None:
    seen: list[str] = []
    offset: int | None = 0
    while offset is not None:
        page = search_logs(store, incident_id="INC-0002", limit=7, offset=offset)
        assert page.offset == offset
        assert page.returned == len(page.records) <= 7
        seen.extend(r.id for r in page.records)
        offset = page.next_offset
    assert seen == [r.id for r in store.logs_for("INC-0002")]


def test_offset_past_the_end_is_empty_not_an_error(store: FixtureStore) -> None:
    result = search_logs(store, incident_id="INC-0001", offset=10_000)
    assert result.returned == 0
    assert result.next_offset is None
    assert result.total_matches == len(store.logs_for("INC-0001"))


def test_unknown_incident_names_the_known_ones(store: FixtureStore) -> None:
    with pytest.raises(
        UnknownIncidentError, match=r"INC-0099; known incidents: INC-0001, INC-0002"
    ):
        search_logs(store, incident_id="INC-0099")


def test_seed_pages_end_by_limit_or_end_not_size(store: FixtureStore) -> None:
    assert search_logs(store, incident_id="INC-0001", limit=5).limited_by == "limit"
    assert search_logs(store, incident_id="INC-0001", limit=50).limited_by == "end"


def test_size_cap_splits_between_records_and_walks_every_one(
    store: FixtureStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(search_module, "RESPONSE_BUDGET_CHARS", 2_000)
    seen: list[str] = []
    offset: int | None = 0
    pages = 0
    while offset is not None:
        page = search_logs(store, incident_id="INC-0002", limit=50, offset=offset)
        pages += 1
        assert page.returned >= 1
        if page.next_offset is not None:
            assert page.limited_by == "size"
        seen.extend(r.id for r in page.records)
        offset = page.next_offset
    assert pages > 1, "a 2,000-char budget must split the seed incident's logs"
    assert seen == [r.id for r in store.logs_for("INC-0002")]


def test_a_record_larger_than_the_budget_is_still_returned_alone(
    store: FixtureStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(search_module, "RESPONSE_BUDGET_CHARS", 1)
    page = search_logs(store, incident_id="INC-0001", limit=50)
    assert page.returned == 1
    assert page.limited_by == "size"
    assert page.next_offset == 1
