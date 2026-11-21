"""What happens when buyers arrive at the same instant."""

import asyncio

import pytest

from conftest import user_headers
from drop import failpoints
from drop.strategies import SAFE_STRATEGIES, STRATEGY_NAMES
from drop.verify import verify_drop


async def test_naive_sells_the_last_unit_twice(client, make_drop, stampede, db):
    """The lost-update race, forced to happen every time.

    One unit, two buyers. The barrier holds each buyer after it has READ "1 left"
    and only releases them once both have read. Both then write "0 left" and both
    get a hold: two units sold where one existed. The CHECK (available >= 0)
    constraint does not help, because 0 is a perfectly legal number to write.
    """
    drop = await make_drop(total_units=1, strategy="naive")
    both_have_read = asyncio.Barrier(2)
    failpoints.set_hook("reserve.after_read", both_have_read.wait)

    statuses = await stampede(drop["id"], buyers=2)

    assert statuses == {201: 2}
    report = verify_drop(db, drop["id"])
    assert report.oversold == 1
    assert report.available == 0
    assert any(v.startswith("I1 oversold") for v in report.violations)


async def test_row_lock_survives_the_same_slow_gap(client, make_drop, stampede, db):
    """Same pause between read and write, but the read took a lock first.

    The second buyer cannot even read until the first has committed, so the gap
    is no longer dangerous.
    """
    drop = await make_drop(total_units=1, strategy="row_lock")

    async def slow_gap():
        await asyncio.sleep(0.05)

    failpoints.set_hook("reserve.after_read", slow_gap)

    statuses = await stampede(drop["id"], buyers=5)

    assert statuses == {201: 1, 409: 4}
    assert verify_drop(db, drop["id"]).ok


@pytest.mark.parametrize("strategy", SAFE_STRATEGIES)
async def test_safe_strategies_sell_exactly_the_stock(client, make_drop, stampede, db, strategy):
    drop = await make_drop(total_units=10, strategy=strategy)

    statuses = await stampede(drop["id"], buyers=100)

    assert statuses == {201: 10, 409: 90}
    report = verify_drop(db, drop["id"])
    assert report.ok, report.violations
    assert report.live_holds == 10


@pytest.mark.parametrize("strategy", sorted(STRATEGY_NAMES))
async def test_same_buyer_clicking_many_times_gets_one_unit(client, make_drop, db, strategy):
    drop = await make_drop(total_units=10, strategy=strategy)
    buyer = user_headers()

    responses = await asyncio.gather(
        *(client.post(f"/drops/{drop['id']}/reservations", headers=buyer) for _ in range(20))
    )

    assert sorted(r.status_code for r in responses) == [201] + [409] * 19
    report = verify_drop(db, drop["id"])
    assert report.ok, report.violations
    assert report.live_holds == 1
