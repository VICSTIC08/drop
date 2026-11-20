from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from psycopg import AsyncConnection

from drop import config
from drop.db import make_pool
from drop.schemas import DropCreate, DropOut, HoldOut
from drop.strategies import STRATEGIES
from drop.strategies.base import DropNotFound, Outcome

DROP_COLUMNS = """
    d.id, d.name, d.total_units, d.strategy, d.hold_seconds, d.created_at, c.available
"""


async def get_conn(request: Request):
    """Borrow one pooled connection for the length of one request."""
    async with request.app.state.pool.connection() as conn:
        yield conn


Conn = Annotated[AsyncConnection, Depends(get_conn)]


def create_app(database_url: str | None = None) -> FastAPI:
    url = database_url or config.database_url()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = make_pool(url, max_size=config.pool_max_size())
        await pool.open()
        app.state.pool = pool
        yield
        await pool.close()

    app = FastAPI(title="drop", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz(conn: Conn):
        await conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.post("/drops", status_code=201, response_model=DropOut)
    async def create_drop(body: DropCreate, conn: Conn):
        if body.strategy not in STRATEGIES:
            raise HTTPException(422, f"unknown strategy: {body.strategy}")
        async with conn.transaction():
            cur = await conn.execute(
                """
                INSERT INTO drops (name, total_units, strategy, hold_seconds)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (body.name, body.total_units, body.strategy, body.hold_seconds),
            )
            drop_id = (await cur.fetchone())["id"]
            await conn.execute(
                "INSERT INTO inventory_counters (drop_id, available) VALUES (%s, %s)",
                (drop_id, body.total_units),
            )
        return await _load_drop(conn, drop_id)

    @app.get("/drops/{drop_id}", response_model=DropOut)
    async def get_drop(drop_id: UUID, conn: Conn):
        return await _load_drop(conn, drop_id)

    @app.post("/drops/{drop_id}/reservations", status_code=201, response_model=HoldOut)
    async def reserve(
        drop_id: UUID,
        conn: Conn,
        user_id: Annotated[UUID, Header(alias="X-User-Id")],
    ):
        cur = await conn.execute("SELECT strategy FROM drops WHERE id = %s", (drop_id,))
        row = await cur.fetchone()
        if row is None:
            raise HTTPException(404, "drop not found")
        strategy = STRATEGIES[row["strategy"]]
        try:
            result = await strategy.reserve(conn, drop_id, user_id)
        except DropNotFound:
            raise HTTPException(404, "drop not found") from None
        if result.outcome is not Outcome.HELD:
            raise HTTPException(409, result.outcome.value)
        return HoldOut(
            hold_id=result.hold_id,
            drop_id=drop_id,
            user_id=user_id,
            expires_at=result.expires_at,
        )

    return app


async def _load_drop(conn: AsyncConnection, drop_id: UUID) -> dict:
    cur = await conn.execute(
        f"""
        SELECT {DROP_COLUMNS}
        FROM drops d
        JOIN inventory_counters c ON c.drop_id = d.id
        WHERE d.id = %s
        """,
        (drop_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(404, "drop not found")
    return row


app = create_app()
