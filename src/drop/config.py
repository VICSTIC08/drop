import os

DEFAULT_DATABASE_URL = "postgresql://drop:drop@localhost:5432/drop"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def pool_max_size() -> int:
    return int(os.environ.get("DB_POOL_MAX_SIZE", "10"))
