from uuid import UUID

from psycopg import AsyncConnection

from drop import failpoints
from drop.strategies.base import DropNotFound, ReservationStrategy


class RowLockStrategy(ReservationStrategy):
    """The naive strategy plus two words: FOR UPDATE.

    FOR UPDATE locks the counter row until this transaction ends. A second buyer's
    SELECT ... FOR UPDATE waits in line, and when it finally runs it reads the
    number the first buyer wrote. Pessimistic locking: assume a conflict will
    happen and make everyone queue. Correct, but every buyer waits on one row.
    """

    name = "row_lock"

    async def _claim_unit(self, conn: AsyncConnection, drop_id: UUID) -> bool:
        cur = await conn.execute(
            "SELECT available FROM inventory_counters WHERE drop_id = %s FOR UPDATE",
            (drop_id,),
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
