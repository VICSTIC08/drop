"""Repair the Redis stock counter from the source of truth, Postgres.

The Redis strategy writes to two systems, so they can drift apart (for example a
crash after Redis gave out a unit but before Postgres recorded who got it).
Postgres holds the durable record, so the right number is always
total units minus live holds.

Run with: python -m drop.reconcile <drop_id>
"""

import sys
from uuid import UUID

import psycopg
import redis

from drop.config import database_url, redis_url
from drop.strategies.redis_counter import stock_key


def reconcile(conn: psycopg.Connection, client: redis.Redis, drop_id: UUID | str) -> int:
    """Set the Redis counter to what Postgres says it should be.

    Returns the drift that was corrected: positive means that many units had
    leaked (Redis thought they were gone, but nobody held them).
    """
    row = conn.execute(
        """
        SELECT d.total_units - (
            SELECT count(*) FROM holds h
            WHERE h.drop_id = d.id AND h.status IN ('active', 'confirmed')
        )
        FROM drops d WHERE d.id = %s
        """,
        (drop_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"drop not found: {drop_id}")
    correct = max(row[0], 0)
    key = stock_key(drop_id)
    current = client.get(key)
    client.set(key, correct)
    return correct - int(current) if current is not None else correct


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m drop.reconcile <drop_id>")
    with (
        psycopg.connect(database_url()) as conn,
        redis.Redis.from_url(redis_url(), decode_responses=True) as client,
    ):
        drift = reconcile(conn, client, sys.argv[1])
    print(f"corrected drift: {drift}")


if __name__ == "__main__":
    main()
