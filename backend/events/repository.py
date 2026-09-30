import hashlib
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.events.models import Event, Observation, utc_timestamp
from backend.events.timing import normalize_earnings
from backend.ingestion.errors import BudgetExhausted


def initialize_database(connection: psycopg.Connection) -> None:
    connection.execute(Path(__file__).with_name("schema.sql").read_text())


class EventRepository:
    def __init__(self, connection: psycopg.Connection):
        self.connection = connection

    def save(self, observation: Observation) -> int:
        event = observation.event
        content = {"event": asdict(event), "inputs": observation.inputs}
        fingerprint = hashlib.sha256(
            json.dumps(content, sort_keys=True, default=str).encode()
        ).hexdigest()
        with self.connection.transaction():
            with self.connection.cursor(row_factory=dict_row) as cursor:
                # The unique identity locks the event while its source consensus is recomputed.
                cursor.execute(
                    """
                    INSERT INTO public.events (
                        event_key, event_type, symbol, event_date, session, event_timestamp,
                        exact_timestamp, timestamp_basis, timing_reason, intraday_eligible,
                        first_observed_at, last_observed_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (event_key) DO UPDATE SET
                        first_observed_at = LEAST(
                            events.first_observed_at, EXCLUDED.first_observed_at),
                        last_observed_at = GREATEST(
                            events.last_observed_at, EXCLUDED.last_observed_at)
                    RETURNING id
                    """,
                    (
                        event.event_key,
                        event.event_type,
                        event.symbol,
                        event.event_date,
                        event.session,
                        event.event_timestamp,
                        event.exact_timestamp,
                        event.timestamp_basis,
                        event.timing_reason,
                        event.intraday_eligible,
                        observation.observed_at,
                        observation.observed_at,
                    ),
                )
                event_id = cursor.fetchone()["id"]
                cursor.execute(
                    """SELECT fingerprint FROM public.event_observations
                    WHERE event_id = %s AND source = %s
                    ORDER BY observed_at DESC, id DESC LIMIT 1""",
                    (event_id, observation.source),
                )
                previous = cursor.fetchone()
                # Preserve changes and reversions, without storing the same daily snapshot again.
                if previous is None or previous["fingerprint"] != fingerprint:
                    cursor.execute(
                        """INSERT INTO public.event_observations (
                            event_id, source, source_record_id, observed_at, session,
                            event_timestamp, exact_timestamp, timestamp_basis, timing_reason,
                            inputs, fingerprint
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (event_id, source, observed_at, fingerprint) DO NOTHING""",
                        (
                            event_id,
                            observation.source,
                            observation.source_record_id,
                            observation.observed_at,
                            event.session,
                            event.event_timestamp,
                            event.exact_timestamp,
                            event.timestamp_basis,
                            event.timing_reason,
                            Jsonb(observation.inputs),
                            fingerprint,
                        ),
                    )
                latest = self.observations_as_of(event_id, datetime.max.replace(tzinfo=UTC))
                reconciled, conflict = reconcile_timing(event, latest)
                cursor.execute(
                    """UPDATE public.events SET session = %s, event_timestamp = %s,
                        exact_timestamp = %s, timestamp_basis = %s, timing_reason = %s,
                        timing_conflict = %s, intraday_eligible = %s WHERE id = %s""",
                    (
                        reconciled.session,
                        reconciled.event_timestamp,
                        reconciled.exact_timestamp,
                        reconciled.timestamp_basis,
                        reconciled.timing_reason,
                        conflict,
                        reconciled.intraday_eligible,
                        event_id,
                    ),
                )
        return event_id

    def observations_as_of(self, event_id: int, as_of: datetime) -> list[dict]:
        with self.connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """SELECT DISTINCT ON (source) * FROM public.event_observations
                WHERE event_id = %s AND observed_at <= %s
                ORDER BY source, observed_at DESC, id DESC""",
                (event_id, utc_timestamp(as_of)),
            )
            return cursor.fetchall()

    def reserve_request(self, source: str, limit: int, window: timedelta) -> int:
        # Reservation commits before the HTTP request; failed calls still spend quota.
        with self.connection.transaction():
            self.connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (source,))
            now = self.connection.execute("SELECT clock_timestamp()").fetchone()[0]
            self.connection.execute(
                "DELETE FROM public.source_requests WHERE requested_at <= %s",
                (now - timedelta(days=1),),
            )
            used = self.connection.execute(
                """SELECT count(*) FROM public.source_requests
                WHERE source = %s AND requested_at > %s""",
                (source, now - window),
            ).fetchone()[0]
            if used >= limit:
                raise BudgetExhausted(f"{source}: persisted free-tier budget exhausted")
            self.connection.execute(
                "INSERT INTO public.source_requests (source, requested_at) VALUES (%s, %s)",
                (source, now),
            )
        return limit - used - 1

    def last_refresh(self, source: str, scope: str) -> datetime | None:
        row = self.connection.execute(
            "SELECT completed_at FROM public.source_refreshes WHERE source = %s AND scope = %s",
            (source, scope),
        ).fetchone()
        return row[0] if row else None

    def complete_refresh(self, source: str, scope: str, at: datetime) -> None:
        self.connection.execute(
            """INSERT INTO public.source_refreshes (source, scope, completed_at) VALUES (%s, %s, %s)
            ON CONFLICT (source, scope) DO UPDATE SET completed_at = EXCLUDED.completed_at""",
            (source, scope, utc_timestamp(at)),
        )


def reconcile_timing(event: Event, latest: list[dict]) -> tuple[Event, bool]:
    sessions = set()
    exact_times = set()
    for observation in latest:
        if observation["session"] != "unknown":
            sessions.add(observation["session"])
        if observation["exact_timestamp"] is not None:
            exact_times.add(observation["exact_timestamp"])
    if len(sessions) > 1 or len(exact_times) > 1:
        return replace(
            event,
            session="unknown",
            event_timestamp=None,
            exact_timestamp=None,
            timestamp_basis="unavailable",
            timing_reason="source_timing_conflict",
        ), True
    if event.event_type != "earnings":
        return event, False
    session = next(iter(sessions), "unknown")
    exact = next(iter(exact_times), None)
    return normalize_earnings(event.symbol, event.event_date, session, exact), False
