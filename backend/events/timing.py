from datetime import date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas_market_calendars as market_calendars

from backend.events.models import Event, Session, canonical_symbol, utc_timestamp

EASTERN = ZoneInfo("America/New_York")


@lru_cache(maxsize=1024)
def nyse_session(event_date: date) -> tuple[datetime, datetime] | None:
    schedule = market_calendars.get_calendar("NYSE").schedule(
        start_date=event_date, end_date=event_date
    )
    if schedule.empty:
        return None
    row = schedule.iloc[0]
    return row["market_open"].to_pydatetime(), row["market_close"].to_pydatetime()


def normalize_earnings(
    symbol: str,
    event_date: date,
    session: Session,
    exact_timestamp: datetime | None = None,
) -> Event:
    symbol = canonical_symbol(symbol)
    if session not in {"bmo", "amc", "dmh", "unknown"}:
        raise ValueError("Unsupported earnings session")
    timestamp = None
    exact = None
    basis = "unavailable"
    reason = "session_time_unavailable"
    if exact_timestamp is not None:
        exact = utc_timestamp(exact_timestamp)
        if exact.astimezone(EASTERN).date() != event_date:
            raise ValueError("Exact release timestamp must match the Eastern event date")
        timestamp = exact
        basis = "exact"
        reason = None
    elif session in {"bmo", "amc"}:
        bounds = nyse_session(event_date)
        if bounds is None:
            reason = "non_trading_date"
        else:
            market_open, market_close = bounds
            # Approved display anchors cannot establish the pre-release reference price.
            if session == "bmo":
                timestamp = market_open - timedelta(seconds=1)
            else:
                timestamp = market_close + timedelta(seconds=1)
            basis = "session_anchor"
            reason = "exact_release_time_unavailable"
    return Event(
        event_key=f"earnings:{symbol}:{event_date.isoformat()}",
        event_type="earnings",
        symbol=symbol,
        event_date=event_date,
        session=session,
        event_timestamp=timestamp,
        exact_timestamp=exact,
        timestamp_basis=basis,
        timing_reason=reason,
    )


def normalize_congressional(
    symbol: str, disclosure_timestamp: datetime, source: str, record_id: str
) -> Event:
    """Timing contract only; live congressional ingestion belongs to Phase 6."""
    disclosure = utc_timestamp(disclosure_timestamp)
    return Event(
        event_key=f"congressional:{source}:{record_id}",
        event_type="congressional",
        symbol=canonical_symbol(symbol),
        event_date=disclosure.astimezone(EASTERN).date(),
        session="unknown",
        event_timestamp=disclosure,
        exact_timestamp=disclosure,
        timestamp_basis="exact",
        timing_reason=None,
    )
