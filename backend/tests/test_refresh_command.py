import sys
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from backend.events.timing import EASTERN
from backend.ingestion import refresh


def test_refresh_command_ingests_both_sources_into_one_persisted_event(
    repository, test_database_url, monkeypatch
):
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.setenv("FINNHUB_API_KEY", "synthetic-finnhub-key")
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "synthetic-alpha-key")
    monkeypatch.setattr(sys, "argv", ["refresh", "--symbols", "AAPL"])
    real_client = httpx.Client
    release_date = (datetime.now(UTC).astimezone(EASTERN).date() - timedelta(days=1)).isoformat()

    def handle(request):
        if request.url.host == "finnhub.io":
            return httpx.Response(
                200,
                json={
                    "earningsCalendar": [
                        {"symbol": "AAPL", "date": release_date, "hour": "amc"},
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "symbol": "AAPL",
                "quarterlyEarnings": [
                    {"reportedDate": release_date, "reportTime": "post-market"},
                ],
            },
        )

    monkeypatch.setattr(
        refresh.httpx, "Client", lambda **_: real_client(transport=httpx.MockTransport(handle))
    )
    assert refresh.main() == 0
    assert repository.connection.execute("SELECT count(*) FROM public.events").fetchone()[0] == 1
    assert repository.connection.execute(
        "SELECT DISTINCT source FROM public.event_observations ORDER BY source"
    ).fetchall() == [("alpha_vantage",), ("finnhub",)]


def test_refresh_command_requires_explicit_allowlist(monkeypatch, test_database_url):
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.setattr(sys, "argv", ["refresh"])
    with pytest.raises(SystemExit) as error:
        refresh.main()
    assert error.value.code == 2
