from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from psycopg import AsyncConnection
from redis.asyncio import Redis

from drop import config
from drop.checkout import CheckoutError, checkout
from drop.db import make_pool
from drop.schemas import DropCreate, DropOut, HoldOut, OrderOut
from drop.strategies import STRATEGY_NAMES, build_strategies
from drop.strategies.base import DropNotFound, Outcome
from drop.strategies.redis_counter import stock_key

DROP_COLUMNS = """
    d.id, d.name, d.total_units, d.strategy, d.hold_seconds, d.price_cents, d.created_at,
    c.available
"""


async def get_conn(request: Request):
    """Borrow one pooled connection for the length of one request."""
    async with request.app.state.pool.connection() as conn:
        yield conn


Conn = Annotated[AsyncConnection, Depends(get_conn)]


def create_app(database_url: str | None = None, redis_url: str | None = None) -> FastAPI:
    url = database_url or config.database_url()
    cache_url = redis_url or config.redis_url()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = make_pool(url, max_size=config.pool_max_size())
        await pool.open()
        redis = Redis.from_url(cache_url, decode_responses=True)
        app.state.pool = pool
        app.state.redis = redis
        app.state.strategies = build_strategies(redis)
        # A drop's strategy never changes, so remember it instead of asking Postgres
        # on every single reservation.
        app.state.strategy_of_drop = {}
        yield
        await redis.aclose()
        await pool.close()

    app = FastAPI(title="drop", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz(conn: Conn):
        await conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.post("/drops", status_code=201, response_model=DropOut)
    async def create_drop(body: DropCreate, conn: Conn, request: Request):
        if body.strategy not in STRATEGY_NAMES:
            raise HTTPException(422, f"unknown strategy: {body.strategy}")
        async with conn.transaction():
            cur = await conn.execute(
                """
                INSERT INTO drops (name, total_units, strategy, hold_seconds, price_cents)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    body.name,
                    body.total_units,
                    body.strategy,
                    body.hold_seconds,
                    body.price_cents,
                ),
            )
            drop_id = (await cur.fetchone())["id"]
            await conn.execute(
                "INSERT INTO inventory_counters (drop_id, available) VALUES (%s, %s)",
                (drop_id, body.total_units),
            )
        strategy = request.app.state.strategies[body.strategy]
        await strategy.on_drop_created(drop_id, body.total_units)
        return await _load_drop(conn, request.app.state.redis, drop_id)

    @app.get("/drops/{drop_id}", response_model=DropOut)
    async def get_drop(drop_id: UUID, conn: Conn, request: Request):
        return await _load_drop(conn, request.app.state.redis, drop_id)

    @app.post("/drops/{drop_id}/reservations", status_code=201, response_model=HoldOut)
    async def reserve(
        drop_id: UUID,
        conn: Conn,
        request: Request,
        user_id: Annotated[UUID, Header(alias="X-User-Id")],
    ):
        known = request.app.state.strategy_of_drop
        if drop_id not in known:
            cur = await conn.execute("SELECT strategy FROM drops WHERE id = %s", (drop_id,))
            row = await cur.fetchone()
            if row is None:
                raise HTTPException(404, "drop not found")
            known[drop_id] = row["strategy"]
        strategy = request.app.state.strategies[known[drop_id]]
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

    @app.post("/reservations/{hold_id}/checkout", status_code=201, response_model=OrderOut)
    async def checkout_hold(
        hold_id: UUID,
        conn: Conn,
        user_id: Annotated[UUID, Header(alias="X-User-Id")],
    ):
        try:
            return await checkout(conn, hold_id, user_id)
        except CheckoutError as error:
            raise HTTPException(error.status_code, error.reason) from None

    @app.get("/orders/{order_id}", response_model=OrderOut)
    async def get_order(order_id: UUID, conn: Conn):
        cur = await conn.execute(
            """
            SELECT id, hold_id, drop_id, user_id, status, amount_cents, created_at
            FROM orders WHERE id = %s
            """,
            (order_id,),
        )
        order = await cur.fetchone()
        if order is None:
            raise HTTPException(404, "order not found")
        return order

    return app


async def _load_drop(conn: AsyncConnection, redis: Redis, drop_id: UUID) -> dict:
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
    if row["strategy"] == "redis_counter":
        stock = await redis.get(stock_key(drop_id))
        if stock is not None:
            row["available"] = int(stock)
    return row


app = create_app()
