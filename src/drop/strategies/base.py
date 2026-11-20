from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation

ONE_PER_CUSTOMER_INDEX = "holds_one_live_per_user_idx"


class Outcome(StrEnum):
    HELD = "held"
    SOLD_OUT = "sold_out"
    ALREADY_HOLDING = "already_holding"


@dataclass(frozen=True)
class ReserveResult:
    outcome: Outcome
    hold_id: UUID | None = None
    expires_at: datetime | None = None


SOLD_OUT = ReserveResult(Outcome.SOLD_OUT)
ALREADY_HOLDING = ReserveResult(Outcome.ALREADY_HOLDING)


class DropNotFound(Exception):
    pass


class ReservationStrategy:
    """Every strategy answers the same question: can this user have one unit?

    Subclasses implement `_claim_unit`, the only part that differs. Everything else
    (the transaction, creating the hold, the one-per-customer rule) is shared, so
    the benchmark compares the claiming step and nothing else.
    """

    name: str

    async def reserve(self, conn: AsyncConnection, drop_id: UUID, user_id: UUID) -> ReserveResult:
        try:
            async with conn.transaction():
                if not await self._claim_unit(conn, drop_id):
                    return SOLD_OUT
                return await create_hold(conn, drop_id, user_id)
        except UniqueViolation as error:
            # The buyer already has a live hold. Leaving the transaction block with an
            # error rolled everything back, including the unit we had just claimed.
            if error.diag.constraint_name != ONE_PER_CUSTOMER_INDEX:
                raise
            return ALREADY_HOLDING

    async def _claim_unit(self, conn: AsyncConnection, drop_id: UUID) -> bool:
        """Take one unit out of stock. Return False if there is none left."""
        raise NotImplementedError

    async def on_drop_created(self, drop_id: UUID, total_units: int) -> None:
        """Called once after a drop using this strategy is created. Most need nothing."""
        return None


async def create_hold(conn: AsyncConnection, drop_id: UUID, user_id: UUID) -> ReserveResult:
    """Insert the hold row. The expiry uses the database clock, never the app's clock."""
    cur = await conn.execute(
        """
        INSERT INTO holds (drop_id, user_id, expires_at)
        SELECT d.id, %(user_id)s, now() + make_interval(secs => d.hold_seconds)
        FROM drops d
        WHERE d.id = %(drop_id)s
        RETURNING id, expires_at
        """,
        {"drop_id": drop_id, "user_id": user_id},
    )
    row = await cur.fetchone()
    if row is None:
        raise DropNotFound(str(drop_id))
    return ReserveResult(Outcome.HELD, hold_id=row["id"], expires_at=row["expires_at"])
