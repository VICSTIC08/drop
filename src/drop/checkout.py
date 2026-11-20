from uuid import UUID

from psycopg import AsyncConnection


class CheckoutError(Exception):
    def __init__(self, status_code: int, reason: str):
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason


async def checkout(conn: AsyncConnection, hold_id: UUID, user_id: UUID) -> dict:
    """Turn an active hold into an order, all-or-nothing.

    The UPDATE is "guarded": it only matches while the hold is still active, belongs
    to this buyer and has not expired. If two checkouts for the same hold race, the
    second one waits for the first, then finds the status already changed and
    matches zero rows. Payment is instant for now; Part 2 moves it to a job queue.
    """
    async with conn.transaction():
        cur = await conn.execute(
            """
            UPDATE holds
            SET status = 'confirmed'
            WHERE id = %s AND user_id = %s AND status = 'active' AND expires_at > now()
            RETURNING drop_id
            """,
            (hold_id, user_id),
        )
        confirmed = await cur.fetchone()
        if confirmed is None:
            raise await _why_not(conn, hold_id, user_id)
        cur = await conn.execute(
            """
            INSERT INTO orders (hold_id, drop_id, user_id, status, amount_cents)
            SELECT %(hold_id)s, d.id, %(user_id)s, 'paid', d.price_cents
            FROM drops d
            WHERE d.id = %(drop_id)s
            RETURNING id, hold_id, drop_id, user_id, status, amount_cents, created_at
            """,
            {"hold_id": hold_id, "user_id": user_id, "drop_id": confirmed["drop_id"]},
        )
        return await cur.fetchone()


async def _why_not(conn: AsyncConnection, hold_id: UUID, user_id: UUID) -> CheckoutError:
    cur = await conn.execute(
        "SELECT status, expires_at <= now() AS expired FROM holds WHERE id = %s AND user_id = %s",
        (hold_id, user_id),
    )
    hold = await cur.fetchone()
    if hold is None:
        return CheckoutError(404, "hold not found")
    if hold["status"] == "confirmed":
        return CheckoutError(409, "already_checked_out")
    if hold["status"] == "active" and hold["expired"]:
        return CheckoutError(410, "hold_expired")
    return CheckoutError(409, f"hold_{hold['status']}")
