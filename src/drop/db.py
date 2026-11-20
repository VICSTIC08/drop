from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


def make_pool(url: str, max_size: int = 10) -> AsyncConnectionPool:
    """A pool keeps a few database connections open and lends them to requests.

    Opening a connection costs a network handshake and a new Postgres process,
    so reusing them is far cheaper than connecting on every request.

    autocommit=True means nothing is left half-open by accident: a single statement
    commits on its own, and anything that must be all-or-nothing is wrapped in an
    explicit `async with conn.transaction()` block, which commits when the block ends.
    So by the time a response is sent, its transaction is already committed.
    """
    return AsyncConnectionPool(
        url,
        min_size=1,
        max_size=max_size,
        open=False,
        kwargs={"row_factory": dict_row, "autocommit": True},
    )
