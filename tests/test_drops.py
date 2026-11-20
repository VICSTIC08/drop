from uuid import uuid4

import pytest


async def test_create_drop_starts_with_all_units_available(client):
    response = await client.post("/drops", json={"name": "sneakers", "total_units": 100})
    assert response.status_code == 201
    drop = response.json()
    assert drop["total_units"] == 100
    assert drop["available"] == 100
    assert drop["strategy"] == "naive"


async def test_get_drop_returns_what_was_created(client, make_drop):
    created = await make_drop(total_units=5)
    response = await client.get(f"/drops/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


async def test_get_unknown_drop_is_404(client):
    response = await client.get(f"/drops/{uuid4()}")
    assert response.status_code == 404


@pytest.mark.parametrize("total_units", [0, -1])
async def test_drop_must_have_at_least_one_unit(client, total_units):
    response = await client.post("/drops", json={"name": "x", "total_units": total_units})
    assert response.status_code == 422


async def test_unknown_strategy_is_rejected(client):
    response = await client.post("/drops", json={"name": "x", "total_units": 1, "strategy": "hope"})
    assert response.status_code == 422


def test_database_itself_rejects_a_drop_with_no_units(db):
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("INSERT INTO drops (name, total_units, strategy) VALUES ('x', 0, 'naive')")
