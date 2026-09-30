from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import psycopg

from backend.events.repository import EventRepository
from backend.ingestion.errors import BudgetExhausted


def test_alpha_vantage_budget_is_shared_and_atomic_across_connections(
    repository, test_database_url
):
    def reserve(_):
        with psycopg.connect(test_database_url, autocommit=True) as connection:
            try:
                EventRepository(connection).reserve_request("alpha_vantage", 25, timedelta(days=1))
                return True
            except BudgetExhausted:
                return False

    with ThreadPoolExecutor(max_workers=4) as executor:
        granted = list(executor.map(reserve, range(30)))
    assert sum(granted) == 25
    assert (
        repository.connection.execute("SELECT count(*) FROM public.source_requests").fetchone()[0]
        == 25
    )
