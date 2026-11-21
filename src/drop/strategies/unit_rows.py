from uuid import UUID

from psycopg import AsyncConnection

from drop.strategies.base import SOLD_OUT, ReservationStrategy, ReserveResult, create_hold


class UnitRowsStrategy(ReservationStrategy):
    """One database row per unit; each buyer locks a different free row.

    FOR UPDATE locks the row we pick. SKIP LOCKED tells Postgres: if another
    buyer has already locked a row, do not wait for it, move on to the next free
    one. So 200 buyers lock 200 different rows instead of queueing for one counter.
    Everything stays in Postgres, so the unit and the hold commit together (no
    dual write). Modelled on the pattern in Shopify Engineering's May 2026 post.

    Trade-off: a buyer can be told "sold out" while the last units are locked by
    requests that later fail and release them. The sale never oversells, but it
    can turn someone away a moment too early.
    """

    name = "unit_rows"

    async def _reserve_in_transaction(
        self, conn: AsyncConnection, drop_id: UUID, user_id: UUID
    ) -> ReserveResult:
        cur = await conn.execute(
            """
            SELECT unit_no FROM inventory_units
            WHERE drop_id = %s AND hold_id IS NULL
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            """,
            (drop_id,),
        )
        unit = await cur.fetchone()
        if unit is None:
            return SOLD_OUT
        result = await create_hold(conn, drop_id, user_id)
        await conn.execute(
            "UPDATE inventory_units SET hold_id = %s WHERE drop_id = %s AND unit_no = %s",
            (result.hold_id, drop_id, unit["unit_no"]),
        )
        return result

    async def release(self, conn: AsyncConnection, drop_id: UUID, hold_id: UUID) -> None:
        await conn.execute(
            "UPDATE inventory_units SET hold_id = NULL WHERE hold_id = %s", (hold_id,)
        )
