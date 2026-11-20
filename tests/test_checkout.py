import asyncio
from uuid import uuid4

from conftest import user_headers
from drop.verify import verify_drop


async def _reserve(client, drop_id: str, buyer: dict) -> str:
    response = await client.post(f"/drops/{drop_id}/reservations", headers=buyer)
    assert response.status_code == 201, response.text
    return response.json()["hold_id"]


async def test_checkout_turns_a_hold_into_a_paid_order(client, make_drop, db):
    drop = await make_drop(total_units=2, strategy="row_lock", price_cents=4500)
    buyer = user_headers()
    hold_id = await _reserve(client, drop["id"], buyer)

    response = await client.post(f"/reservations/{hold_id}/checkout", headers=buyer)

    assert response.status_code == 201
    order = response.json()
    assert order["status"] == "paid"
    assert order["amount_cents"] == 4500
    assert order["hold_id"] == hold_id
    status = db.execute("SELECT status FROM holds WHERE id = %s", (hold_id,)).fetchone()[0]
    assert status == "confirmed"
    assert (await client.get(f"/orders/{order['id']}")).json() == order
    assert verify_drop(db, drop["id"]).ok


async def test_a_hold_can_only_be_checked_out_once(client, make_drop):
    drop = await make_drop(strategy="row_lock")
    buyer = user_headers()
    hold_id = await _reserve(client, drop["id"], buyer)
    await client.post(f"/reservations/{hold_id}/checkout", headers=buyer)

    again = await client.post(f"/reservations/{hold_id}/checkout", headers=buyer)

    assert again.status_code == 409
    assert again.json()["detail"] == "already_checked_out"


async def test_ten_simultaneous_checkouts_create_one_order(client, make_drop, db):
    drop = await make_drop(strategy="row_lock")
    buyer = user_headers()
    hold_id = await _reserve(client, drop["id"], buyer)

    responses = await asyncio.gather(
        *(client.post(f"/reservations/{hold_id}/checkout", headers=buyer) for _ in range(10))
    )

    assert sorted(r.status_code for r in responses) == [201] + [409] * 9
    orders = db.execute("SELECT count(*) FROM orders WHERE hold_id = %s", (hold_id,)).fetchone()
    assert orders[0] == 1
    assert verify_drop(db, drop["id"]).ok


async def test_nobody_else_can_check_out_your_hold(client, make_drop):
    drop = await make_drop(strategy="row_lock")
    hold_id = await _reserve(client, drop["id"], user_headers())

    response = await client.post(f"/reservations/{hold_id}/checkout", headers=user_headers())

    assert response.status_code == 404


async def test_expired_hold_cannot_be_checked_out(client, make_drop, db):
    drop = await make_drop(strategy="row_lock")
    buyer = user_headers()
    hold_id = await _reserve(client, drop["id"], buyer)
    db.execute(
        "UPDATE holds SET expires_at = now() - interval '1 second' WHERE id = %s", (hold_id,)
    )

    response = await client.post(f"/reservations/{hold_id}/checkout", headers=buyer)

    assert response.status_code == 410
    assert response.json()["detail"] == "hold_expired"
    assert db.execute("SELECT count(*) FROM orders").fetchone()[0] == 0


async def test_checkout_of_unknown_hold_is_404(client):
    response = await client.post(f"/reservations/{uuid4()}/checkout", headers=user_headers())
    assert response.status_code == 404


async def test_unknown_order_is_404(client):
    assert (await client.get(f"/orders/{uuid4()}")).status_code == 404
