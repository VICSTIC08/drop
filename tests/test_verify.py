from uuid import uuid4

import pytest

from conftest import user_headers
from drop.verify import verify_drop


async def test_clean_drop_passes(client, make_drop, db):
    drop = await make_drop(total_units=3, strategy="row_lock")
    await client.post(f"/drops/{drop['id']}/reservations", headers=user_headers())

    report = verify_drop(db, drop["id"])

    assert report.ok
    assert (report.total_units, report.live_holds, report.available) == (3, 1, 2)


async def test_detects_more_holds_than_units(client, make_drop, db):
    drop = await make_drop(total_units=1, strategy="row_lock")
    for _ in range(2):
        db.execute(
            "INSERT INTO holds (drop_id, user_id, expires_at) VALUES (%s, %s, now())",
            (drop["id"], uuid4()),
        )

    report = verify_drop(db, drop["id"])

    assert report.oversold == 1
    assert any(v.startswith("I1") for v in report.violations)


async def test_detects_a_leaked_unit(client, make_drop, db):
    drop = await make_drop(total_units=5, strategy="row_lock")
    db.execute("UPDATE inventory_counters SET available = 4 WHERE drop_id = %s", (drop["id"],))

    report = verify_drop(db, drop["id"])

    assert [v[:2] for v in report.violations] == ["I3"]


async def test_expired_and_released_holds_do_not_count_as_sold(client, make_drop, db):
    drop = await make_drop(total_units=1, strategy="row_lock")
    for status in ("expired", "released"):
        db.execute(
            "INSERT INTO holds (drop_id, user_id, status, expires_at) VALUES (%s, %s, %s, now())",
            (drop["id"], uuid4(), status),
        )

    assert verify_drop(db, drop["id"]).ok


def test_unknown_drop_raises(db):
    with pytest.raises(LookupError):
        verify_drop(db, uuid4())
