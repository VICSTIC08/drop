"""What is special about keeping the stock number in a second system."""

import pytest

from conftest import user_headers
from drop import failpoints
from drop.reconcile import reconcile
from drop.strategies.redis_counter import stock_key
from drop.verify import verify_drop


async def test_stock_lives_in_redis(client, make_drop, cache):
    drop = await make_drop(total_units=3, strategy="redis_counter")
    assert cache.get(stock_key(drop["id"])) == "3"

    await client.post(f"/drops/{drop['id']}/reservations", headers=user_headers())

    assert cache.get(stock_key(drop["id"])) == "2"
    assert (await client.get(f"/drops/{drop['id']}")).json()["available"] == 2


async def test_crash_between_the_two_writes_leaks_a_unit(client, make_drop, db, cache):
    """The dual-write problem, forced to happen.

    Redis hands out a unit, then the process "dies" before Postgres records the
    hold. Nobody owns that unit, but the counter says it is gone: one unit of
    stock has silently disappeared. Reconciling against Postgres finds it again.
    """
    drop = await make_drop(total_units=5, strategy="redis_counter")

    async def die():
        raise failpoints.Crash

    failpoints.set_hook("redis.after_decrement", die)
    with pytest.raises(failpoints.Crash):
        await client.post(f"/drops/{drop['id']}/reservations", headers=user_headers())
    failpoints.clear()

    report = verify_drop(db, drop["id"])
    assert (report.live_holds, report.available) == (0, 4)
    assert [v[:2] for v in report.violations] == ["I3"]

    drift = reconcile(db, cache, drop["id"])

    assert drift == 1
    assert verify_drop(db, drop["id"]).ok


async def test_a_database_error_gives_the_unit_back(client, make_drop, db, cache):
    drop = await make_drop(total_units=5, strategy="redis_counter")

    async def database_trouble():
        raise RuntimeError("postgres is unhappy")

    failpoints.set_hook("redis.after_decrement", database_trouble)
    with pytest.raises(RuntimeError):
        await client.post(f"/drops/{drop['id']}/reservations", headers=user_headers())

    assert cache.get(stock_key(drop["id"])) == "5"
    assert verify_drop(db, drop["id"]).ok


async def test_counter_is_rebuilt_if_redis_forgets_it(client, make_drop, db, cache, stampede):
    drop = await make_drop(total_units=3, strategy="redis_counter")
    await client.post(f"/drops/{drop['id']}/reservations", headers=user_headers())
    cache.flushdb()

    statuses = await stampede(drop["id"], buyers=10)

    assert statuses == {201: 2, 409: 8}
    report = verify_drop(db, drop["id"])
    assert report.ok, report.violations
    assert report.live_holds == 3


async def test_reconcile_reports_zero_drift_when_in_sync(client, make_drop, db, cache):
    drop = await make_drop(total_units=3, strategy="redis_counter")
    await client.post(f"/drops/{drop['id']}/reservations", headers=user_headers())

    assert reconcile(db, cache, drop["id"]) == 0
