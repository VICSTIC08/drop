from uuid import uuid4

from conftest import user_headers


async def test_reserve_creates_an_active_hold_and_takes_one_unit(client, make_drop, db):
    drop = await make_drop(total_units=3)
    headers = user_headers()

    response = await client.post(f"/drops/{drop['id']}/reservations", headers=headers)

    assert response.status_code == 201
    hold = response.json()
    assert hold["user_id"] == headers["X-User-Id"]
    row = db.execute(
        "SELECT status, expires_at > now() FROM holds WHERE id = %s", (hold["hold_id"],)
    ).fetchone()
    assert row == ("active", True)
    assert (await client.get(f"/drops/{drop['id']}")).json()["available"] == 2


async def test_reserve_on_unknown_drop_is_404(client):
    response = await client.post(f"/drops/{uuid4()}/reservations", headers=user_headers())
    assert response.status_code == 404


async def test_reserve_requires_a_user_id(client, make_drop):
    drop = await make_drop()
    response = await client.post(f"/drops/{drop['id']}/reservations")
    assert response.status_code == 422


async def test_hold_expiry_follows_the_drop_setting(client, make_drop, db):
    drop = await make_drop(total_units=1, hold_seconds=30)
    response = await client.post(f"/drops/{drop['id']}/reservations", headers=user_headers())
    seconds = db.execute(
        "SELECT extract(epoch FROM expires_at - created_at) FROM holds WHERE id = %s",
        (response.json()["hold_id"],),
    ).fetchone()[0]
    assert round(seconds) == 30
