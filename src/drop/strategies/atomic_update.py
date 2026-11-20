from uuid import UUID

from psycopg import AsyncConnection

from drop.strategies.base import ReservationStrategy


class AtomicUpdateStrategy(ReservationStrategy):
    """Check and decrement in ONE statement, so there is no gap to race through.

    The database takes the row lock, re-checks `available > 0` against the latest
    committed value, and subtracts, all inside the UPDATE. If no row comes back,
    the stock was already zero. Same single hot row as row_lock, one round trip
    fewer.
    """

    name = "atomic_update"

    async def _claim_unit(self, conn: AsyncConnection, drop_id: UUID) -> bool:
        cur = await conn.execute(
            """
            UPDATE inventory_counters
            SET available = available - 1
            WHERE drop_id = %s AND available > 0
            RETURNING available
            """,
            (drop_id,),
        )
        return await cur.fetchone() is not None
