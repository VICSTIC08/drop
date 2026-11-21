"""Give back units whose holds ran out of time.

A hold is a lease: the buyer owns the unit only until `expires_at`. If they never
check out, the sweeper marks the hold expired and returns the unit to stock.
"""

import asyncio
import logging
from collections import Counter

from psycopg_pool import AsyncConnectionPool

from drop.strategies.base import ReservationStrategy

log = logging.getLogger("drop.sweeper")


async def expire_holds(
    pool: AsyncConnectionPool, strategies: dict[str, ReservationStrategy], batch: int = 200
) -> int:
    """Expire up to `batch` overdue holds. Returns how many were expired.

    SKIP LOCKED makes it safe to run several sweepers at once: each one grabs
    different holds, so no unit is ever returned twice. The time check uses the
    database clock, the same clock checkout uses, so a hold is either still
    checkable or sweepable, never both.
    """
    released: Counter = Counter()
    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            """
            SELECT h.id, h.drop_id, d.strategy
            FROM holds h
            JOIN drops d ON d.id = h.drop_id
            WHERE h.status = 'active' AND h.expires_at <= now()
            ORDER BY h.expires_at
            LIMIT %s
            FOR UPDATE OF h SKIP LOCKED
            """,
            (batch,),
        )
        overdue = await cur.fetchall()
        if not overdue:
            return 0
        await conn.execute(
            "UPDATE holds SET status = 'expired' WHERE id = ANY(%s)",
            ([hold["id"] for hold in overdue],),
        )
        for hold in overdue:
            await strategies[hold["strategy"]].release(conn, hold["drop_id"], hold["id"])
            released[(hold["strategy"], hold["drop_id"])] += 1
    for (strategy, drop_id), units in released.items():
        await strategies[strategy].after_release_committed(drop_id, units)
    return len(overdue)


async def sweep_forever(
    pool: AsyncConnectionPool, strategies: dict[str, ReservationStrategy], interval: float
) -> None:
    while True:
        try:
            while await expire_holds(pool, strategies):
                pass
        except Exception:
            log.exception("sweep failed; will retry")
        await asyncio.sleep(interval)
