from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from psycopg import AsyncConnection


class Outcome(StrEnum):
    HELD = "held"
    SOLD_OUT = "sold_out"
    ALREADY_HOLDING = "already_holding"


@dataclass(frozen=True)
class ReserveResult:
    outcome: Outcome
    hold_id: UUID | None = None
    expires_at: datetime | None = None


class DropNotFound(Exception):
    pass


class ReservationStrategy(Protocol):
    """Every strategy answers the same question: can this user have one unit?"""

    name: str

    async def reserve(
        self, conn: AsyncConnection, drop_id: UUID, user_id: UUID
    ) -> ReserveResult: ...


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
