"""Holds are leases: units come back when the buyer runs out of time."""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from conftest import TEST_REDIS_URL, user_headers
from drop.main import create_app
from drop.strategies import STRATEGY_NAMES
from drop.sweeper import expire_holds
from drop.verify import verify_drop

ALL = sorted(STRATEGY_NAMES)


def _make_overdue(db, drop_id: str) -> None:
    db.execute(
        "UPDATE holds SET expires_at = now() - interval '1 second' WHERE drop_id = %s",
        (drop_id,),
    )


@pytest.mark.parametrize("strategy", ALL)
async def test_expired_holds_return_their_units(client, make_drop, db, app_state, strategy):
    drop = await make_drop(total_units=3, strategy=strategy)
    for _ in range(3):
        await client.post(f"/drops/{drop['id']}/reservations", headers=user_headers())
    assert (await client.get(f"/drops/{drop['id']}")).json()["available"] == 0
    _make_overdue(db, drop["id"])

    expired = await expire_holds(app_state.pool, app_state.strategies)

    assert expired == 3
    assert (await client.get(f"/drops/{drop['id']}")).json()["available"] == 3
    report = verify_drop(db, drop["id"])
    assert report.ok, report.violations
    assert report.live_holds == 0


@pytest.mark.parametrize("strategy", ALL)
async def test_returned_units_can_be_bought_again(
    client, make_drop, db, app_state, stampede, strategy
):
    drop = await make_drop(total_units=2, strategy=strategy)
    early_buyer = user_headers()
    await client.post(f"/drops/{drop['id']}/reservations", headers=early_buyer)
    await client.post(f"/drops/{drop['id']}/reservations", headers=user_headers())
    _make_overdue(db, drop["id"])
    await expire_holds(app_state.pool, app_state.strategies)

    again = await client.post(f"/drops/{drop['id']}/reservations", headers=early_buyer)
    assert again.status_code == 201, "a buyer whose hold expired may try again"
    if strategy != "naive":
        assert await stampede(drop["id"], buyers=10) == {201: 1, 409: 9}
        assert verify_drop(db, drop["id"]).ok


@pytest.mark.parametrize("strategy", ALL)
async def test_five_sweepers_at_once_return_each_unit_once(
    client, make_drop, db, app_state, strategy
):
    drop = await make_drop(total_units=20, strategy=strategy)
    for _ in range(20):
        await client.post(f"/drops/{drop['id']}/reservations", headers=user_headers())
    _make_overdue(db, drop["id"])

    counts = await asyncio.gather(
        *(expire_holds(app_state.pool, app_state.strategies, batch=7) for _ in range(5))
    )
    while await expire_holds(app_state.pool, app_state.strategies):
        pass

    assert sum(counts) <= 20
    report = verify_drop(db, drop["id"])
    assert report.ok, report.violations
    assert report.available == 20


async def test_holds_still_in_time_are_left_alone(client, make_drop, db, app_state):
    drop = await make_drop(total_units=2, strategy="row_lock")
    await client.post(f"/drops/{drop['id']}/reservations", headers=user_headers())

    assert await expire_holds(app_state.pool, app_state.strategies) == 0
    assert verify_drop(db, drop["id"]).live_holds == 1


async def test_checked_out_holds_never_expire(client, make_drop, db, app_state):
    drop = await make_drop(total_units=1, strategy="unit_rows")
    buyer = user_headers()
    hold = (await client.post(f"/drops/{drop['id']}/reservations", headers=buyer)).json()
    await client.post(f"/reservations/{hold['hold_id']}/checkout", headers=buyer)
    _make_overdue(db, drop["id"])

    assert await expire_holds(app_state.pool, app_state.strategies) == 0
    report = verify_drop(db, drop["id"])
    assert report.ok, report.violations
    assert (report.live_holds, report.orders) == (1, 1)


async def test_a_hold_is_either_checked_out_or_expired_never_both(client, make_drop, db, app_state):
    """Checkout and the sweeper race for the same overdue hold. Exactly one wins."""
    drop = await make_drop(total_units=1, strategy="row_lock")
    buyer = user_headers()
    hold = (await client.post(f"/drops/{drop['id']}/reservations", headers=buyer)).json()
    _make_overdue(db, drop["id"])

    checkout, expired = await asyncio.gather(
        client.post(f"/reservations/{hold['hold_id']}/checkout", headers=buyer),
        expire_holds(app_state.pool, app_state.strategies),
    )

    assert checkout.status_code in (409, 410)
    assert expired == 1
    report = verify_drop(db, drop["id"])
    assert report.ok, report.violations
    assert (report.live_holds, report.orders, report.available) == (0, 0, 1)


async def test_background_sweeper_runs_on_its_own(database_url, db, cache):
    app = create_app(database_url, TEST_REDIS_URL, sweep_interval=0.05)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            body = {"name": "x", "total_units": 1, "strategy": "unit_rows", "hold_seconds": 1}
            drop = (await c.post("/drops", json=body)).json()
            await c.post(f"/drops/{drop['id']}/reservations", headers=user_headers())
            assert (await c.get(f"/drops/{drop['id']}")).json()["available"] == 0

            for _ in range(60):
                await asyncio.sleep(0.05)
                if (await c.get(f"/drops/{drop['id']}")).json()["available"] == 1:
                    break

            assert (await c.get(f"/drops/{drop['id']}")).json()["available"] == 1
    assert verify_drop(db, drop["id"]).ok
