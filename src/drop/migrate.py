"""Apply the numbered .sql files in migrations/ exactly once each, in order.

Run with: python -m drop.migrate
"""

import os
from pathlib import Path

import psycopg

from drop.config import database_url

MIGRATIONS_DIR = Path(
    os.environ.get("MIGRATIONS_DIR", Path(__file__).resolve().parents[2] / "migrations")
)

# Any fixed number works: it names the lock that stops two migrators running at once.
MIGRATION_LOCK_ID = 727_001


def migrate(url: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every migration that has not been applied yet. Returns the versions applied."""
    applied_now: list[str] = []
    with psycopg.connect(url) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    text PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        conn.commit()
        for path in sorted(migrations_dir.glob("*.sql")):
            version = path.stem
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID,))
                already = conn.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = %s", (version,)
                ).fetchone()
                if already:
                    continue
                conn.execute(path.read_text())
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
                applied_now.append(version)
    return applied_now


def main() -> None:
    applied = migrate(database_url())
    if applied:
        print("applied: " + ", ".join(applied))
    else:
        print("nothing to apply")


if __name__ == "__main__":
    main()
