import os
from uuid import uuid4

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from drop.main import create_app
from drop.migrate import migrate

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://drop:drop@localhost:5432/drop_test"
)


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
        conn.execute("TRUNCATE holds, inventory_counters, drops CASCADE")
        yield conn


@pytest.fixture
async def client(database_url, db):
    app = create_app(database_url)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.fixture
def make_drop(client):
    async def _make(total_units: int = 3, strategy: str = "naive", **extra) -> dict:
        body = {"name": "test drop", "total_units": total_units, "strategy": strategy, **extra}
        response = await client.post("/drops", json=body)
        assert response.status_code == 201, response.text
        return response.json()

    return _make


def user_headers() -> dict[str, str]:
    return {"X-User-Id": str(uuid4())}
