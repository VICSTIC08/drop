from uuid import UUID

from psycopg import AsyncConnection

from drop import failpoints
from drop.strategies.base import DropNotFound, ReservationStrategy


class NaiveStrategy(ReservationStrategy):
    """Read the stock, decide in Python, write the new number back.

    Correct when buyers arrive one at a time. Under concurrency two buyers can both
    read "1 left" before either writes, and both get a unit: the lost-update race.
    This is the baseline every other strategy is measured against.
    """

    name = "naive"

    async def _claim_unit(self, conn: AsyncConnection, drop_id: UUID) -> bool:
        cur = await conn.execute(
            "SELECT available FROM inventory_counters WHERE drop_id = %s", (drop_id,)
        )
        row = await cur.fetchone()
        if row is None:
            raise DropNotFound(str(drop_id))
        available = row["available"]
        if available <= 0:
            return False
        await failpoints.hit("reserve.after_read")
        await conn.execute(
            "UPDATE inventory_counters SET available = %s WHERE drop_id = %s",
            (available - 1, drop_id),
        )
        return True
