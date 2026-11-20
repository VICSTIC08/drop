async def test_healthz_reports_ok_when_database_is_reachable(client):
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
