import os

DEFAULT_DATABASE_URL = "postgresql://drop:drop@localhost:5432/drop"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def redis_url() -> str:
    return os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)


def pool_max_size() -> int:
    return int(os.environ.get("DB_POOL_MAX_SIZE", "10"))
