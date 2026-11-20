from drop.migrate import migrate


def test_migrations_are_applied_once(database_url):
    assert migrate(database_url) == []


def test_schema_migrations_records_each_file(database_url, db):
    versions = [r[0] for r in db.execute("SELECT version FROM schema_migrations ORDER BY 1")]
    assert versions[:2] == ["001_init", "002_orders_and_one_per_customer"]
