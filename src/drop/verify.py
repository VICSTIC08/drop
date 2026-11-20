"""The judge: look only at the final rows in the database and check the invariants.

It knows nothing about which strategy ran, so every strategy is held to the same rules.

Run with: python -m drop.verify <drop_id>
"""

import sys
from dataclasses import dataclass, field
from uuid import UUID

import psycopg
import redis

from drop.config import database_url, redis_url
from drop.strategies.redis_counter import stock_key

LIVE = ("active", "confirmed")


@dataclass
class VerifyReport:
    drop_id: str
    total_units: int
    live_holds: int
    available: int
    duplicate_buyers: int
    confirmed_holds: int
    orders: int
    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    @property
    def oversold(self) -> int:
        return max(0, self.live_holds - self.total_units)


def verify_drop(conn: psycopg.Connection, drop_id: UUID | str) -> VerifyReport:
    row = conn.execute(
        """
        SELECT
            d.strategy,
            d.total_units,
            c.available,
            (SELECT count(*) FROM holds h
              WHERE h.drop_id = d.id AND h.status = ANY(%(live)s)) AS live_holds,
            (SELECT count(*) FROM holds h
              WHERE h.drop_id = d.id AND h.status = 'confirmed') AS confirmed_holds,
            (SELECT count(*) FROM orders o WHERE o.drop_id = d.id) AS orders,
            (SELECT count(*) FROM (
                SELECT user_id FROM holds h
                WHERE h.drop_id = d.id AND h.status = ANY(%(live)s)
                GROUP BY user_id HAVING count(*) > 1
            ) AS repeat_buyers) AS duplicate_buyers
        FROM drops d
        JOIN inventory_counters c ON c.drop_id = d.id
        WHERE d.id = %(drop_id)s
        """,
        {"drop_id": drop_id, "live": list(LIVE)},
    ).fetchone()
    if row is None:
        raise LookupError(f"drop not found: {drop_id}")
    strategy, total_units, available, live_holds, confirmed_holds, orders, duplicate_buyers = row
    if strategy == "redis_counter":
        available = _redis_stock(drop_id)

    report = VerifyReport(
        drop_id=str(drop_id),
        total_units=total_units,
        live_holds=live_holds,
        available=available,
        duplicate_buyers=duplicate_buyers,
        confirmed_holds=confirmed_holds,
        orders=orders,
    )
    if live_holds > total_units:
        report.violations.append(
            f"I1 oversold: {live_holds} live holds for {total_units} units "
            f"(+{live_holds - total_units})"
        )
    if duplicate_buyers:
        report.violations.append(f"I2 one per customer: {duplicate_buyers} buyers hold 2+ units")
    if live_holds + available != total_units:
        report.violations.append(
            f"I3 conservation: {live_holds} live holds + {available} available "
            f"!= {total_units} units"
        )
    if confirmed_holds != orders:
        report.violations.append(
            f"I4 orders: {confirmed_holds} confirmed holds but {orders} orders"
        )
    return report


def _redis_stock(drop_id: UUID | str) -> int:
    """For the Redis strategy the stock number lives in Redis, not in Postgres."""
    with redis.Redis.from_url(redis_url(), decode_responses=True) as client:
        value = client.get(stock_key(drop_id))
    return int(value) if value is not None else 0


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m drop.verify <drop_id>")
    with psycopg.connect(database_url()) as conn:
        report = verify_drop(conn, sys.argv[1])
    print(report)
    raise SystemExit(0 if report.ok else 1)


if __name__ == "__main__":
    main()
