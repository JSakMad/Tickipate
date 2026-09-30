import argparse
import hashlib
import logging
import os
from datetime import UTC, datetime, timedelta

import httpx
import psycopg
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv

from backend.events.models import canonical_symbol
from backend.events.repository import EventRepository, initialize_database
from backend.events.timing import EASTERN
from backend.ingestion import alpha_vantage, finnhub
from backend.ingestion.errors import BudgetExhausted, SourceUnavailable

logger = logging.getLogger(__name__)


def parse_symbols(value: str) -> list[str]:
    symbols = []
    for entry in value.split(","):
        symbol = canonical_symbol(entry)
        if symbol not in symbols:
            symbols.append(symbol)
    return symbols


def refresh_finnhub(client, repository, api_key: str, symbols: list[str], now: datetime) -> int:
    today = now.astimezone(EASTERN).date()
    start = today - relativedelta(months=1)
    end = today + timedelta(days=7)
    # Scope includes the allowlist and date bounds so a changed request cannot hit an old TTL.
    allowlist_hash = hashlib.sha256(",".join(sorted(symbols)).encode()).hexdigest()
    scope = f"{allowlist_hash}:{start}:{end}"
    last_refresh = repository.last_refresh("finnhub", scope)
    if last_refresh is not None and now - last_refresh < timedelta(hours=6):
        logger.info("source=finnhub cached_calendar=true")
        return 0
    observations = finnhub.fetch_earnings(client, repository, api_key, start, end)
    saved = 0
    with repository.connection.transaction():
        for observation in observations:
            if observation.event.symbol in symbols:
                repository.save(observation)
                saved += 1
        repository.complete_refresh("finnhub", scope, datetime.now(UTC))
    logger.info("source=finnhub calendar_records=%d", saved)
    return saved


def refresh_history(client, repository, api_key: str, symbols: list[str], now: datetime) -> int:
    today = now.astimezone(EASTERN).date()
    start = today - relativedelta(years=5)
    priorities = []
    for symbol in symbols:
        last_refresh = repository.last_refresh("alpha_vantage", symbol)
        priorities.append((last_refresh or datetime.min.replace(tzinfo=UTC), symbol))
    priorities.sort()
    completed = 0
    for last_refresh, symbol in priorities:
        if now - last_refresh < timedelta(days=1):
            continue
        try:
            observations = alpha_vantage.fetch_earnings(
                client, repository, api_key, symbol, start, today
            )
        except BudgetExhausted:
            logger.info("source=alpha_vantage backfill_deferred=true budget_exhausted=true")
            break
        with repository.connection.transaction():
            for observation in observations:
                repository.save(observation)
            repository.complete_refresh("alpha_vantage", symbol, datetime.now(UTC))
        completed += 1
        logger.info(
            "source=alpha_vantage symbol=%s historical_records=%d", symbol, len(observations)
        )
    return completed


def main() -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description="Refresh free-source earnings calendars.")
    parser.add_argument("--symbols", help="Comma-separated explicit ticker allowlist")
    parser.add_argument("--sources", choices=("all", "finnhub", "alpha_vantage"), default="all")
    parser.add_argument("--init-db", action="store_true", help="Create the Phase 1 database tables")
    arguments = parser.parse_args()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL is required")
    if not arguments.init_db and not arguments.symbols:
        parser.error("--symbols is required for ingestion")
    try:
        symbols = parse_symbols(arguments.symbols) if arguments.symbols else []
        keys = {
            "finnhub": os.getenv("FINNHUB_API_KEY"),
            "alpha_vantage": os.getenv("ALPHA_VANTAGE_API_KEY"),
        }
        requested = (
            ("finnhub", "alpha_vantage") if arguments.sources == "all" else (arguments.sources,)
        )
        if symbols:
            for source in requested:
                if not keys[source]:
                    parser.error(f"{source}: API key is required")
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute("SET TIME ZONE 'UTC'")
            repository = EventRepository(connection)
            if arguments.init_db:
                with connection.transaction():
                    initialize_database(connection)
            if not symbols:
                return 0
            # All refreshes share a database lock, including local runs and scheduled jobs.
            locked = connection.execute("SELECT pg_try_advisory_lock(84923011)").fetchone()[0]
            if not locked:
                logger.info("calendar_refresh_deferred=true another_refresh_running=true")
                return 0
            try:
                with httpx.Client(timeout=30.0, follow_redirects=False) as client:
                    now = datetime.now(UTC)
                    if "finnhub" in requested:
                        refresh_finnhub(client, repository, keys["finnhub"], symbols, now)
                    if "alpha_vantage" in requested:
                        refresh_history(client, repository, keys["alpha_vantage"], symbols, now)
            finally:
                connection.execute("SELECT pg_advisory_unlock(84923011)")
    except (SourceUnavailable, BudgetExhausted) as error:
        logger.error("%s; stopped without changing source or upgrading a plan", error)
        return 1
    except psycopg.Error, ValueError:
        # PostgreSQL exceptions can echo connection details or query parameters.
        logger.error("Database or configuration error; check settings and schema")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
