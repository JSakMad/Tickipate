from datetime import UTC, date, datetime, timedelta

import httpx

from backend.events.models import Observation, canonical_symbol
from backend.events.timing import normalize_earnings
from backend.ingestion.errors import SourceUnavailable
from backend.ingestion.http import RequestBudget, request_json

SESSION_CODES = {"pre-market": "bmo", "post-market": "amc"}


def parse_earnings(
    payload: dict, symbol: str, start: date, end: date, observed_at: datetime
) -> list[Observation]:
    symbol = canonical_symbol(symbol)
    rows = payload.get("quarterlyEarnings")
    if payload.get("symbol") != symbol or not isinstance(rows, list):
        raise SourceUnavailable("alpha_vantage: missing earnings array or mismatched symbol")
    observations = []
    try:
        for row in rows:
            event_date = date.fromisoformat(row["reportedDate"])
            if not start <= event_date <= end:
                continue
            session = SESSION_CODES.get(row.get("reportTime"), "unknown")
            event = normalize_earnings(symbol, event_date, session)
            inputs = {}
            for field in (
                "fiscalDateEnding",
                "reportedDate",
                "reportTime",
                "reportedEPS",
                "estimatedEPS",
                "surprise",
                "surprisePercentage",
            ):
                if field in row:
                    inputs[field] = row[field]
            observations.append(
                Observation(
                    event,
                    "alpha_vantage",
                    observed_at,
                    source_record_id=row.get("fiscalDateEnding"),
                    inputs=inputs,
                )
            )
    except KeyError, TypeError, ValueError, AttributeError:
        raise SourceUnavailable("alpha_vantage: malformed quarterly earnings row") from None
    return observations


def fetch_earnings(
    client: httpx.Client,
    budget: RequestBudget,
    api_key: str,
    symbol: str,
    start: date,
    end: date,
    observed_at: datetime | None = None,
) -> list[Observation]:
    if not api_key:
        raise ValueError("ALPHA_VANTAGE_API_KEY is required")
    if start > end:
        raise ValueError("History start date must not follow end date")
    symbol = canonical_symbol(symbol)
    payload = request_json(
        client,
        budget,
        "alpha_vantage",
        "https://www.alphavantage.co/query",
        limit=25,
        window=timedelta(days=1),
        params={"function": "EARNINGS", "symbol": symbol, "apikey": api_key},
    )
    return parse_earnings(payload, symbol, start, end, observed_at or datetime.now(UTC))
