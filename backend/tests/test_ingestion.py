import logging
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from backend.ingestion import alpha_vantage, finnhub
from backend.ingestion.errors import BudgetExhausted, SourceLimitError, SourceUnavailable
from backend.ingestion.refresh import parse_symbols, refresh_finnhub, refresh_history

OBSERVED_AT = datetime(2026, 9, 30, 17, tzinfo=UTC)
START = date(2021, 9, 30)
END = date(2026, 9, 30)


def earnings_response(symbol="AAPL"):
    return {
        "symbol": symbol,
        "quarterlyEarnings": [
            {"reportedDate": "2025-07-31", "reportTime": "post-market"},
            {"reportedDate": "2020-07-30", "reportTime": "pre-market"},
            {"reportedDate": "2026-10-01", "reportTime": "post-market"},
        ],
    }


def test_alpha_vantage_filters_to_the_requested_five_year_window():
    observations = alpha_vantage.parse_earnings(
        earnings_response(), "AAPL", START, END, OBSERVED_AT
    )
    assert len(observations) == 1
    assert observations[0].event.event_date == date(2025, 7, 31)
    assert observations[0].event.session == "amc"
    assert not observations[0].event.intraday_eligible


@pytest.mark.parametrize("source_time", [None, "None", "", "not-known"])
def test_alpha_vantage_missing_session_remains_unknown(source_time):
    payload = {
        "symbol": "AAPL",
        "quarterlyEarnings": [
            {"reportedDate": "2025-07-31", "reportTime": source_time},
        ],
    }
    event = alpha_vantage.parse_earnings(payload, "AAPL", START, END, OBSERVED_AT)[0].event
    assert event.session == "unknown"
    assert event.event_timestamp is None


@pytest.mark.parametrize("hour", [None, "", "unrecognized", "dmh"])
def test_finnhub_missing_or_unspecified_session_does_not_invent_a_timestamp(hour):
    payload = {"earningsCalendar": [{"symbol": "AAPL", "date": "2025-07-31", "hour": hour}]}
    event = finnhub.parse_earnings(payload, OBSERVED_AT)[0].event
    assert event.event_timestamp is None


@pytest.mark.parametrize("payload", [{}, {"quarterlyEarnings": []}, earnings_response("MSFT")])
def test_alpha_vantage_invalid_schema_or_symbol_is_reported(payload):
    with pytest.raises(SourceUnavailable):
        alpha_vantage.parse_earnings(payload, "AAPL", START, END, OBSERVED_AT)


def test_malformed_finnhub_row_fails_the_batch():
    with pytest.raises(SourceUnavailable, match="malformed"):
        finnhub.parse_earnings({"earningsCalendar": [{"symbol": "AAPL"}]}, OBSERVED_AT)


def test_finnhub_uses_one_batched_calendar_request_and_a_header_for_the_key(repository):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"earningsCalendar": []})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert finnhub.fetch_earnings(client, repository, "synthetic-key", START, END) == []
    assert len(requests) == 1
    assert requests[0].headers["X-Finnhub-Token"] == "synthetic-key"
    assert "synthetic-key" not in str(requests[0].url)
    assert requests[0].url.params["from"] == START.isoformat()


def test_finnhub_out_of_range_rows_are_reported(repository):
    payload = {"earningsCalendar": [{"symbol": "AAPL", "date": "2020-07-30"}]}
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(SourceUnavailable, match="outside"):
            finnhub.fetch_earnings(client, repository, "synthetic-key", START, END)


@pytest.mark.parametrize(
    "payload",
    [
        {"Information": "premium required"},
        {"Note": "quota exceeded"},
        {"Error Message": "bad key"},
    ],
)
def test_alpha_vantage_entitlement_messages_stop_without_retries(repository, payload):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(SourceLimitError):
            alpha_vantage.fetch_earnings(client, repository, "synthetic-key", "AAPL", START, END)
    assert len(calls) == 1


@pytest.mark.parametrize("status", [401, 402, 403, 429])
def test_access_and_rate_errors_are_explicit(repository, status):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status))) as client:
        with pytest.raises(SourceLimitError):
            alpha_vantage.fetch_earnings(client, repository, "synthetic-key", "AAPL", START, END)


def test_keys_are_redacted_from_httpx_info_logs(repository, caplog):
    caplog.set_level(logging.INFO)
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=earnings_response()))
    ) as client:
        alpha_vantage.fetch_earnings(client, repository, "synthetic-secret-key", "AAPL", START, END)
    assert "synthetic-secret-key" not in caplog.text
    assert "local_quota_remaining=24" in caplog.text


def test_transport_error_does_not_expose_key_and_still_spends_budget(repository):
    def fail(request):
        raise httpx.ConnectError(f"failed {request.url}", request=request)

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(SourceUnavailable) as error:
            alpha_vantage.fetch_earnings(client, repository, "synthetic-key", "AAPL", START, END)
    assert "synthetic-key" not in str(error.value)
    assert (
        repository.connection.execute("SELECT count(*) FROM public.source_requests").fetchone()[0]
        == 1
    )


def test_observed_at_records_receipt_time_instead_of_historical_event_time(repository):
    before = datetime.now(UTC)
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=earnings_response()))
    ) as client:
        observations = alpha_vantage.fetch_earnings(
            client, repository, "synthetic-key", "AAPL", START, END
        )
    assert before <= observations[0].observed_at <= datetime.now(UTC)
    assert observations[0].observed_at > observations[0].event.event_timestamp


def test_calendar_refresh_filters_allowlist_and_obeys_six_hour_ttl(repository):
    now = datetime.now(UTC)
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "earningsCalendar": [
                    {"symbol": "AAPL", "date": now.date().isoformat(), "hour": "amc"},
                    {"symbol": "EXCLUDED", "date": now.date().isoformat(), "hour": "amc"},
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert refresh_finnhub(client, repository, "synthetic-key", ["AAPL"], now) == 1
        assert (
            refresh_finnhub(client, repository, "synthetic-key", ["AAPL"], now + timedelta(hours=5))
            == 0
        )
        assert (
            refresh_finnhub(client, repository, "synthetic-key", ["AAPL"], now + timedelta(hours=7))
            == 1
        )
    assert len(calls) == 2
    assert repository.connection.execute("SELECT symbol FROM public.events").fetchall() == [
        ("AAPL",)
    ]


def test_historical_backfill_stops_at_25_calls_and_resumes_unfetched_symbols_first(repository):
    now = datetime.now(UTC)
    symbols = [f"FIXTURE{index:02}" for index in range(30)]
    requested = []

    def handle(request):
        symbol = request.url.params["symbol"]
        requested.append(symbol)
        return httpx.Response(200, json=earnings_response(symbol))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert refresh_history(client, repository, "synthetic-key", symbols, now) == 25
        assert len(requested) == 25
        assert refresh_history(client, repository, "synthetic-key", symbols, now) == 0
        assert len(requested) == 25
        repository.connection.execute(
            "UPDATE public.source_requests "
            "SET requested_at = clock_timestamp() - interval '25 hours'"
        )
        assert refresh_history(client, repository, "synthetic-key", symbols, now) == 5
    assert len(set(requested)) == 30


def test_allowlist_normalizes_case_and_removes_duplicates_without_alias_guessing():
    assert parse_symbols("aapl,MSFT,AAPL,BRK.B") == ["AAPL", "MSFT", "BRK.B"]
    with pytest.raises(ValueError):
        parse_symbols("AAPL,")


def test_finnhub_budget_blocks_the_61st_request(repository):
    for _ in range(60):
        repository.reserve_request("finnhub", 60, timedelta(minutes=1))
    with pytest.raises(BudgetExhausted):
        repository.reserve_request("finnhub", 60, timedelta(minutes=1))
