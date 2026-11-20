"""Rules every strategy must obey when buyers arrive one at a time."""

import pytest

from conftest import user_headers
from drop.strategies import STRATEGIES
from drop.verify import verify_drop

ALL = sorted(STRATEGIES)


@pytest.mark.parametrize("strategy", ALL)
async def test_sells_every_unit_then_says_sold_out(client, make_drop, db, strategy):
    drop = await make_drop(total_units=3, strategy=strategy)

    statuses = []
    for _ in range(5):
        response = await client.post(f"/drops/{drop['id']}/reservations", headers=user_headers())
        statuses.append(response.status_code)

    assert statuses == [201, 201, 201, 409, 409]
    assert verify_drop(db, drop["id"]).ok


@pytest.mark.parametrize("strategy", ALL)
async def test_one_unit_per_customer(client, make_drop, db, strategy):
    drop = await make_drop(total_units=3, strategy=strategy)
    buyer = user_headers()

    first = await client.post(f"/drops/{drop['id']}/reservations", headers=buyer)
    second = await client.post(f"/drops/{drop['id']}/reservations", headers=buyer)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"] == "already_holding"
    report = verify_drop(db, drop["id"])
    assert report.ok, report.violations
    assert report.available == 2, "the rejected second attempt must give its unit back"


@pytest.mark.parametrize("strategy", ALL)
async def test_sold_out_response_says_why(client, make_drop, strategy):
    drop = await make_drop(total_units=1, strategy=strategy)
    await client.post(f"/drops/{drop['id']}/reservations", headers=user_headers())

    response = await client.post(f"/drops/{drop['id']}/reservations", headers=user_headers())

    assert response.status_code == 409
    assert response.json()["detail"] == "sold_out"
