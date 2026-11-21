from conftest import user_headers


async def test_drop_gets_one_row_per_unit(client, make_drop, db):
    drop = await make_drop(total_units=7, strategy="unit_rows")

    rows = db.execute(
        "SELECT count(*), count(hold_id) FROM inventory_units WHERE drop_id = %s", (drop["id"],)
    ).fetchone()

    assert rows == (7, 0)
    assert drop["available"] == 7


async def test_reserving_marks_exactly_one_unit_with_the_hold(client, make_drop, db):
    drop = await make_drop(total_units=3, strategy="unit_rows")

    hold = (await client.post(f"/drops/{drop['id']}/reservations", headers=user_headers())).json()

    taken = db.execute(
        "SELECT hold_id::text FROM inventory_units WHERE drop_id = %s AND hold_id IS NOT NULL",
        (drop["id"],),
    ).fetchall()
    assert taken == [(hold["hold_id"],)]
    assert (await client.get(f"/drops/{drop['id']}")).json()["available"] == 2
