import asyncio
import os
from collections import Counter
from uuid import uuid4

import psycopg
import pytest
import redis
from httpx import ASGITransport, AsyncClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from drop import failpoints
from drop.main import create_app
from drop.migrate import migrate

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://drop:drop@localhost:5432/drop_test"
)
# Tests use Redis database 1 so they never touch the development data in database 0.
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/1")
os.environ["REDIS_URL"] = TEST_REDIS_URL


def _ensure_database_exists(url: str) -> None:
    name = conninfo_to_dict(url)["dbname"]
    admin_url = make_conninfo(url, dbname="postgres")
    with psycopg.connect(admin_url, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


@pytest.fixture(scope="session")
def database_url() -> str:
    """A clean, fully migrated test database, built once per test run."""
    _ensure_database_exists(TEST_DATABASE_URL)
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    migrate(TEST_DATABASE_URL)
    return TEST_DATABASE_URL


@pytest.fixture
def db(database_url):
    """A direct connection for assertions, with every table emptied before the test."""
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("TRUNCATE orders, inventory_units, holds, inventory_counters, drops CASCADE")
        yield conn


@pytest.fixture
def cache(db):
    """A direct Redis connection for assertions, emptied before the test."""
    with redis.Redis.from_url(TEST_REDIS_URL, decode_responses=True) as client:
        client.flushdb()
        yield client


@pytest.fixture(autouse=True)
def no_leftover_failpoints():
    yield
    failpoints.clear()


@pytest.fixture
async def client(database_url, db, cache):
    app = create_app(database_url, TEST_REDIS_URL, sweep_interval=0)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.fixture
def app_state(client):
    """The running app's pool and strategies, for tests that call internals directly."""
    return client._transport.app.state


@pytest.fixture
def make_drop(client):
    async def _make(total_units: int = 3, strategy: str = "naive", **extra) -> dict:
        body = {"name": "test drop", "total_units": total_units, "strategy": strategy, **extra}
        response = await client.post("/drops", json=body)
        assert response.status_code == 201, response.text
        return response.json()

    return _make


@pytest.fixture
def stampede(client):
    """Fire many reservation requests at the same instant, each from a different buyer."""

    async def _stampede(drop_id: str, buyers: int) -> Counter:
        responses = await asyncio.gather(
            *(
                client.post(f"/drops/{drop_id}/reservations", headers=user_headers())
                for _ in range(buyers)
            )
        )
        return Counter(r.status_code for r in responses)

    return _stampede


def user_headers(user_id: str | None = None) -> dict[str, str]:
    return {"X-User-Id": user_id or str(uuid4())}
