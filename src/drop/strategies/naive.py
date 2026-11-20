from uuid import UUID

from psycopg import AsyncConnection

from drop.strategies.base import DropNotFound, Outcome, ReserveResult, create_hold


class NaiveStrategy:
    """Read the stock, decide in Python, write the new number back.

    Correct when buyers arrive one at a time. Under concurrency two buyers can both
    read "1 left" before either writes, and both get a unit: the lost-update race.
    This is the baseline every other strategy is measured against.
    """

    name = "naive"

    async def reserve(self, conn: AsyncConnection, drop_id: UUID, user_id: UUID) -> ReserveResult:
        async with conn.transaction():
            cur = await conn.execute(
                "SELECT available FROM inventory_counters WHERE drop_id = %s", (drop_id,)
            )
            row = await cur.fetchone()
            if row is None:
                raise DropNotFound(str(drop_id))
            available = row["available"]
            if available <= 0:
                return ReserveResult(Outcome.SOLD_OUT)
            await conn.execute(
                "UPDATE inventory_counters SET available = %s WHERE drop_id = %s",
                (available - 1, drop_id),
            )
            return await create_hold(conn, drop_id, user_id)
