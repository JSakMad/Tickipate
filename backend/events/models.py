from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal

Session = Literal["bmo", "amc", "dmh", "unknown"]


def utc_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must include an explicit timezone")
    return value.astimezone(UTC)


def canonical_symbol(value: str) -> str:
    symbol = value.strip().upper()
    if not symbol or len(symbol) > 32 or any(character.isspace() for character in symbol):
        raise ValueError("A nonempty ticker symbol is required")
    return symbol


@dataclass(frozen=True)
class Event:
    event_key: str
    event_type: Literal["earnings", "congressional"]
    symbol: str
    event_date: date
    session: Session
    event_timestamp: datetime | None
    exact_timestamp: datetime | None
    timestamp_basis: Literal["exact", "session_anchor", "unavailable"]
    timing_reason: str | None

    def __post_init__(self):
        object.__setattr__(self, "symbol", canonical_symbol(self.symbol))
        for field_name in ("event_timestamp", "exact_timestamp"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, utc_timestamp(value))
        if self.timestamp_basis == "exact":
            if self.exact_timestamp is None or self.event_timestamp != self.exact_timestamp:
                raise ValueError("Exact timing needs the observed public timestamp")
        elif self.timestamp_basis == "session_anchor":
            if self.exact_timestamp is not None or self.event_timestamp is None:
                raise ValueError("A session anchor cannot be an exact timestamp")
        elif self.timestamp_basis == "unavailable":
            if self.event_timestamp is not None or self.exact_timestamp is not None:
                raise ValueError("Unavailable timing must have null timestamps")
        else:
            raise ValueError("Unsupported timestamp basis")

    @property
    def intraday_eligible(self) -> bool:
        return self.timestamp_basis == "exact" and self.exact_timestamp is not None


@dataclass(frozen=True)
class Observation:
    event: Event
    source: str
    observed_at: datetime
    source_record_id: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "observed_at", utc_timestamp(self.observed_at))
        if not self.source.strip():
            raise ValueError("An observation needs a source")
