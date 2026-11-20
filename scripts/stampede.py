"""Watch each strategy face a crowd.

Creates one drop per strategy, sends every buyer's request at the same instant,
then lets `verify` judge the result. The app runs inside this process, so the
timings here are NOT benchmark numbers; this script is only about correctness.

Usage: uv run python scripts/stampede.py --units 100 --buyers 1000
"""

import argparse
import asyncio
import time
from collections import Counter
from uuid import uuid4

import psycopg
from httpx import ASGITransport, AsyncClient

from drop import config
from drop.main import create_app
from drop.strategies import STRATEGY_NAMES
from drop.verify import verify_drop


async def run_one(client: AsyncClient, strategy: str, units: int, buyers: int) -> tuple:
    response = await client.post(
        "/drops", json={"name": f"stampede-{strategy}", "total_units": units, "strategy": strategy}
    )
    response.raise_for_status()
    drop_id = response.json()["id"]

    started = time.perf_counter()
    responses = await asyncio.gather(
        *(
            client.post(f"/drops/{drop_id}/reservations", headers={"X-User-Id": str(uuid4())})
            for _ in range(buyers)
        )
    )
    seconds = time.perf_counter() - started
    return drop_id, Counter(r.status_code for r in responses), seconds


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--units", type=int, default=100)
    parser.add_argument("--buyers", type=int, default=1000)
    parser.add_argument("strategies", nargs="*", default=sorted(STRATEGY_NAMES))
    args = parser.parse_args()

    url = config.database_url()
    app = create_app(url)
    print(f"{args.units} units, {args.buyers} buyers at once\n")
    print(f"{'strategy':<15}{'sold':>6}{'oversold':>10}{'sold out':>10}{'seconds':>9}  verdict")
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://app", timeout=120) as client:
            for strategy in args.strategies:
                drop_id, statuses, seconds = await run_one(
                    client, strategy, args.units, args.buyers
                )
                with psycopg.connect(url) as conn:
                    report = verify_drop(conn, drop_id)
                verdict = "OK" if report.ok else "; ".join(report.violations)
                print(
                    f"{strategy:<15}{report.live_holds:>6}{report.oversold:>10}"
                    f"{statuses[409]:>10}{seconds:>9.2f}  {verdict}"
                )


if __name__ == "__main__":
    asyncio.run(main())
