from uuid import UUID

from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from redis.asyncio import Redis

from drop import failpoints
from drop.strategies.base import (
    ALREADY_HOLDING,
    ONE_PER_CUSTOMER_INDEX,
    SOLD_OUT,
    ReservationStrategy,
    ReserveResult,
    create_hold,
)

# Runs inside Redis as one indivisible step. Redis executes commands one at a
# time, so no other buyer can slip in between the check and the decrement.
# Returns 1 = took a unit, 0 = sold out, -1 = Redis has no counter for this drop.
CLAIM_SCRIPT = """
local stock = redis.call('GET', KEYS[1])
if not stock then return -1 end
if tonumber(stock) <= 0 then return 0 end
redis.call('DECR', KEYS[1])
return 1
"""


def stock_key(drop_id: UUID | str) -> str:
    return f"drop:{drop_id}:stock"


class RedisCounterStrategy(ReservationStrategy):
    """Keep the stock number in Redis (memory) and the holds in Postgres (disk).

    Fast: a sold-out buyer is turned away without touching Postgres at all.
    The price: one purchase now writes to TWO systems, and they cannot commit
    together. If the process dies after Redis said "yes" but before Postgres saved
    the hold, that unit is gone from the counter and nobody owns it. This is the
    dual-write problem; `reconcile` repairs it after the fact.
    """

    name = "redis_counter"

    def __init__(self, redis: Redis):
        self.redis = redis
        self._claim = redis.register_script(CLAIM_SCRIPT)

    async def reserve(self, conn: AsyncConnection, drop_id: UUID, user_id: UUID) -> ReserveResult:
        key = stock_key(drop_id)
        answer = await self._claim(keys=[key])
        if answer == -1:
            await self._rebuild_counter(conn, drop_id)
            answer = await self._claim(keys=[key])
        if answer != 1:
            return SOLD_OUT
        try:
            await failpoints.hit("redis.after_decrement")
            async with conn.transaction():
                return await create_hold(conn, drop_id, user_id)
        except failpoints.Crash:
            raise
        except UniqueViolation as error:
            await self.redis.incr(key)
            if error.diag.constraint_name != ONE_PER_CUSTOMER_INDEX:
                raise
            return ALREADY_HOLDING
        except BaseException:
            # Postgres refused or the request was cancelled: hand the unit back.
            await self.redis.incr(key)
            raise

    async def on_drop_created(self, drop_id: UUID, total_units: int) -> None:
        await self.redis.set(stock_key(drop_id), total_units)

    async def _rebuild_counter(self, conn: AsyncConnection, drop_id: UUID) -> None:
        """Redis lost the counter (restart, eviction). Postgres is the source of truth."""
        cur = await conn.execute(
            """
            SELECT d.total_units - (
                SELECT count(*) FROM holds h
                WHERE h.drop_id = d.id AND h.status IN ('active', 'confirmed')
            ) AS free
            FROM drops d WHERE d.id = %s
            """,
            (drop_id,),
        )
        row = await cur.fetchone()
        if row is not None:
            await self.redis.set(stock_key(drop_id), max(row["free"], 0), nx=True)
