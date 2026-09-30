import os

import psycopg
import pytest

from backend.events.repository import EventRepository, initialize_database

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://postgres:tickipate_test@127.0.0.1:55432/tickipate_test",
)


@pytest.fixture(scope="session")
def test_database_url():
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def test_database():
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as connection:
        if connection.info.dbname != "tickipate_test":
            pytest.fail("Persistence tests require a dedicated database named tickipate_test")
        connection.execute("SET TIME ZONE 'UTC'")
        with connection.transaction():
            initialize_database(connection)
        yield connection


@pytest.fixture
def repository(test_database):
    test_database.execute(
        """TRUNCATE public.events, public.event_observations, public.source_requests,
        public.source_refreshes RESTART IDENTITY"""
    )
    return EventRepository(test_database)
