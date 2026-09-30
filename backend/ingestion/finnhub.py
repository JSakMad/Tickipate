from datetime import UTC, date, datetime, timedelta

import httpx

from backend.events.models import Observation
from backend.events.timing import normalize_earnings
from backend.ingestion.errors import SourceUnavailable
from backend.ingestion.http import RequestBudget, request_json


def parse_earnings(payload: dict, observed_at: datetime) -> list[Observation]:
    rows = payload.get("earningsCalendar")
    if not isinstance(rows, list):
        raise SourceUnavailable("finnhub: earningsCalendar is missing or invalid")
    observations = []
    try:
        for row in rows:
            event_date = date.fromisoformat(row["date"])
            hour = row.get("hour")
            session = hour if hour in {"bmo", "amc", "dmh"} else "unknown"
            event = normalize_earnings(row["symbol"], event_date, session)
            inputs = {}
            for field in (
                "date",
                "hour",
                "quarter",
                "year",
                "epsActual",
                "epsEstimate",
                "revenueActual",
                "revenueEstimate",
            ):
                if field in row:
                    inputs[field] = row[field]
            observations.append(Observation(event, "finnhub", observed_at, inputs=inputs))
    except KeyError, TypeError, ValueError, AttributeError:
        raise SourceUnavailable("finnhub: malformed earnings calendar row") from None
    return observations


def fetch_earnings(
    client: httpx.Client,
    budget: RequestBudget,
    api_key: str,
    start: date,
    end: date,
    observed_at: datetime | None = None,
) -> list[Observation]:
    if not api_key:
        raise ValueError("FINNHUB_API_KEY is required")
    if start > end:
        raise ValueError("Calendar start date must not follow end date")
    payload = request_json(
        client,
        budget,
        "finnhub",
        "https://finnhub.io/api/v1/calendar/earnings",
        limit=60,
        window=timedelta(minutes=1),
        params={"from": start.isoformat(), "to": end.isoformat(), "international": "false"},
        headers={"X-Finnhub-Token": api_key},
    )
    observations = parse_earnings(payload, observed_at or datetime.now(UTC))
    for observation in observations:
        if not start <= observation.event.event_date <= end:
            raise SourceUnavailable("finnhub: calendar returned a row outside the requested range")
    return observations
