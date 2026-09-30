import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from psycopg.rows import dict_row

from backend.events.models import Observation
from backend.events.timing import EASTERN, normalize_congressional, normalize_earnings
from backend.ingestion import alpha_vantage, finnhub

OBSERVED_AT = datetime(2026, 9, 30, 17, tzinfo=UTC)


def fixture(name):
    return json.loads((Path(__file__).parent / "fixtures" / name).read_text())


def read_event(repository, event_id):
    with repository.connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SELECT * FROM public.events WHERE id = %s", (event_id,))
        return cursor.fetchone()


def test_fr1_two_sources_collapse_to_one_event_row(repository):
    calendar = finnhub.parse_earnings(fixture("finnhub_earnings.json"), OBSERVED_AT)
    history = alpha_vantage.parse_earnings(
        fixture("alpha_vantage_earnings.json"),
        "AAPL",
        date(2021, 9, 30),
        date(2026, 9, 30),
        OBSERVED_AT,
    )
    first_id = repository.save(calendar[0])
    second_id = repository.save(history[0])
    assert first_id == second_id
    assert repository.connection.execute("SELECT count(*) FROM public.events").fetchone()[0] == 1
    observations = repository.observations_as_of(first_id, OBSERVED_AT)
    assert {row["source"] for row in observations} == {"finnhub", "alpha_vantage"}
    assert observations[1]["inputs"]["epsActual"] is None


def test_fr1_amc_stores_after_close_anchor_with_exact_time_unavailable(repository):
    observation = finnhub.parse_earnings(fixture("finnhub_earnings.json"), OBSERVED_AT)[0]
    stored = read_event(repository, repository.save(observation))
    assert stored["event_timestamp"] == datetime(2025, 7, 31, 20, 0, 1, tzinfo=UTC)
    assert stored["event_timestamp"].astimezone(EASTERN).hour == 16
    assert stored["timestamp_basis"] == "session_anchor"
    assert stored["exact_timestamp"] is None
    assert stored["intraday_eligible"] is False


def test_fr1_congressional_timestamp_is_disclosure_not_trade_date(repository):
    disclosure = datetime(2024, 4, 18, 10, 15, tzinfo=EASTERN)
    event = normalize_congressional("AAPL", disclosure, "fixture", "record-1")
    observation = Observation(event, "fixture", OBSERVED_AT, inputs={"trade_date": "2024-03-02"})
    stored = read_event(repository, repository.save(observation))
    assert stored["event_timestamp"] == disclosure.astimezone(UTC)
    assert stored["event_date"] == date(2024, 4, 18)
    assert stored["event_date"] != date(2024, 3, 2)
    assert stored["timestamp_basis"] == "exact"


@pytest.mark.parametrize(
    "event_date, utc_hour",
    [
        (date(2025, 1, 15), 14),
        (date(2025, 7, 31), 13),
        (date(2025, 3, 7), 14),
        (date(2025, 3, 10), 13),
    ],
)
def test_bmo_anchor_handles_standard_time_and_dst(event_date, utc_hour):
    event = normalize_earnings("aapl", event_date, "bmo")
    assert event.symbol == "AAPL"
    assert event.event_timestamp == datetime(
        event_date.year, event_date.month, event_date.day, utc_hour, 29, 59, tzinfo=UTC
    )
    assert not event.intraday_eligible


def test_amc_uses_nyse_early_close():
    event = normalize_earnings("AAPL", date(2025, 11, 28), "amc")
    assert event.event_timestamp == datetime(2025, 11, 28, 18, 0, 1, tzinfo=UTC)


@pytest.mark.parametrize("session", ["unknown", "dmh"])
def test_unknown_and_during_market_sessions_do_not_invent_times(session):
    event = normalize_earnings("AAPL", date(2025, 7, 31), session)
    assert event.event_timestamp is None
    assert event.exact_timestamp is None
    assert not event.intraday_eligible


@pytest.mark.parametrize("event_date", [date(2025, 7, 4), date(2025, 7, 5)])
def test_non_trading_date_is_flagged_instead_of_shifted(event_date):
    event = normalize_earnings("AAPL", event_date, "amc")
    assert event.event_timestamp is None
    assert event.event_date == event_date
    assert event.timing_reason == "non_trading_date"


def test_exact_release_time_is_utc_and_eligible():
    exact = datetime(2025, 7, 31, 16, 5, tzinfo=EASTERN)
    event = normalize_earnings("AAPL", date(2025, 7, 31), "amc", exact)
    assert event.exact_timestamp == datetime(2025, 7, 31, 20, 5, tzinfo=UTC)
    assert event.event_timestamp == event.exact_timestamp
    assert event.intraday_eligible


def test_naive_release_timestamp_is_rejected():
    with pytest.raises(ValueError, match="explicit timezone"):
        normalize_earnings("AAPL", date(2025, 7, 31), "amc", datetime(2025, 7, 31, 16, 5))


def test_exact_release_timestamp_on_another_eastern_date_is_rejected():
    with pytest.raises(ValueError, match="Eastern event date"):
        normalize_earnings("AAPL", date(2025, 7, 31), "amc", OBSERVED_AT)


def test_source_timing_conflict_is_retained_and_excluded(repository):
    amc = normalize_earnings("AAPL", date(2025, 7, 31), "amc")
    bmo = normalize_earnings("AAPL", date(2025, 7, 31), "bmo")
    event_id = repository.save(Observation(amc, "finnhub", OBSERVED_AT))
    repository.save(Observation(bmo, "alpha_vantage", OBSERVED_AT))
    stored = read_event(repository, event_id)
    assert stored["timing_conflict"]
    assert stored["event_timestamp"] is None
    assert stored["timing_reason"] == "source_timing_conflict"
    assert len(repository.observations_as_of(event_id, OBSERVED_AT)) == 2


def test_unknown_source_does_not_erase_known_session(repository):
    known = normalize_earnings("AAPL", date(2025, 7, 31), "amc")
    unknown = normalize_earnings("AAPL", date(2025, 7, 31), "unknown")
    event_id = repository.save(Observation(known, "finnhub", OBSERVED_AT))
    repository.save(Observation(unknown, "alpha_vantage", OBSERVED_AT))
    assert read_event(repository, event_id)["session"] == "amc"


def test_repeated_snapshots_are_not_duplicated_but_reversions_are_saved(repository):
    event = normalize_earnings("AAPL", date(2025, 7, 31), "amc")
    first = Observation(event, "finnhub", OBSERVED_AT)
    event_id = repository.save(first)
    repository.save(replace(first, observed_at=OBSERVED_AT + timedelta(hours=1)))
    assert (
        repository.connection.execute("SELECT count(*) FROM public.event_observations").fetchone()[
            0
        ]
        == 1
    )
    changed = replace(
        first,
        event=normalize_earnings("AAPL", event.event_date, "bmo"),
        observed_at=OBSERVED_AT + timedelta(hours=2),
    )
    repository.save(changed)
    repository.save(replace(first, observed_at=OBSERVED_AT + timedelta(hours=3)))
    assert (
        repository.connection.execute("SELECT count(*) FROM public.event_observations").fetchone()[
            0
        ]
        == 3
    )
    assert read_event(repository, event_id)["session"] == "amc"


def test_backfilled_data_cannot_appear_in_an_earlier_knowledge_snapshot(repository):
    event = normalize_earnings("AAPL", date(2023, 7, 27), "amc")
    event_id = repository.save(Observation(event, "alpha_vantage", OBSERVED_AT))
    assert repository.observations_as_of(event_id, datetime(2023, 7, 27, tzinfo=UTC)) == []
    assert len(repository.observations_as_of(event_id, OBSERVED_AT)) == 1


def test_all_backend_tables_have_rls_enabled(repository):
    rows = repository.connection.execute(
        """SELECT relname, relrowsecurity FROM pg_class WHERE relnamespace = 'public'::regnamespace
        AND relname IN ('events', 'event_observations', 'source_requests', 'source_refreshes')"""
    ).fetchall()
    assert len(rows) == 4
    assert all(row[1] for row in rows)
