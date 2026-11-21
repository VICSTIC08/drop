"""Run a benchmark experiment and save the raw results as JSON.

For each strategy, each variant and each repeat it creates a fresh drop, lets k6
send the buyers over real HTTP, then asks `verify` to judge the final database
state. Nothing here is rounded or summarised: bench/table.py does that from the file.

Usage:
    uv run python bench/run.py burst
    uv run python bench/run.py throughput
    uv run python bench/run.py sweep
    uv run python bench/run.py pool
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg

from drop import config
from drop.strategies import SAFE_STRATEGIES, STRATEGY_NAMES
from drop.verify import verify_drop

ROOT = Path(__file__).resolve().parents[1]
PORT = 8010
BASE_URL = f"http://127.0.0.1:{PORT}"
DEFAULT_POOL = 10

# burst:      far more buyers than units, the flash-sale moment. "Does it oversell?"
# throughput: a unit for every buyer, so every request does the full work of reserving.
#             "How fast is the reserve path, and how bad is the tail?"
# sweep:      throughput again at rising concurrency. "How does each strategy degrade
#             as more buyers arrive at once?"
# pool:       the burst again with different database connection pool sizes.
#             "Is the bottleneck the query, or waiting for a connection?"
EXPERIMENTS = {
    "burst": {
        "repeats": 5,
        "strategies": STRATEGY_NAMES,
        "variants": [{"units": 100, "buyers": 1000, "vus": 200, "pool": DEFAULT_POOL}],
    },
    "throughput": {
        "repeats": 5,
        "strategies": STRATEGY_NAMES,
        "variants": [{"units": 2000, "buyers": 2000, "vus": 50, "pool": DEFAULT_POOL}],
    },
    "sweep": {
        "repeats": 3,
        "strategies": STRATEGY_NAMES,
        "variants": [
            {"units": 2000, "buyers": 2000, "vus": vus, "pool": DEFAULT_POOL}
            for vus in (10, 25, 50, 100, 200)
        ],
    },
    "pool": {
        "repeats": 3,
        "strategies": SAFE_STRATEGIES,
        "variants": [
            {"units": 100, "buyers": 1000, "vus": 200, "pool": pool} for pool in (5, 10, 20, 40)
        ],
    },
}


def sh(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, cwd=ROOT).stdout.strip()


def environment() -> dict:
    with psycopg.connect(config.database_url()) as conn:
        postgres = conn.execute("SHOW server_version").fetchone()[0]
    return {
        "machine": platform.machine(),
        "system": f"{platform.system()} {platform.release()}",
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "postgres": postgres,
        "k6": sh("k6", "version"),
        "git_commit": sh("git", "rev-parse", "--short", "HEAD") or "unknown",
        "git_dirty": bool(sh("git", "status", "--porcelain")),
        "note": "load generator, API and database all share this one machine",
    }


def start_server(pool: int) -> subprocess.Popen:
    # The background sweeper is off so it cannot disturb the timings.
    env = {**os.environ, "DB_POOL_MAX_SIZE": str(pool), "SWEEP_INTERVAL_SECONDS": "0"}
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "drop.main:app", "--port", str(PORT)]
        + ["--log-level", "warning"],
        cwd=ROOT,
        env=env,
    )
    for _ in range(100):
        try:
            if httpx.get(f"{BASE_URL}/healthz", timeout=1).status_code == 200:
                return server
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    server.terminate()
    raise SystemExit("the API did not start; is `docker compose up -d` running?")


def run_once(strategy: str, units: int, buyers: int, vus: int) -> dict:
    drop = httpx.post(
        f"{BASE_URL}/drops",
        json={"name": f"bench-{strategy}", "total_units": units, "strategy": strategy},
        timeout=60,
    )
    drop.raise_for_status()
    drop_id = drop.json()["id"]

    with tempfile.TemporaryDirectory() as tmp:
        summary_path = Path(tmp) / "summary.json"
        k6 = subprocess.run(
            ["k6", "run", "--quiet", "--no-usage-report"]
            + ["-e", f"BASE_URL={BASE_URL}", "-e", f"DROP_ID={drop_id}"]
            + ["-e", f"VUS={vus}", "-e", f"BUYERS={buyers}", "-e", f"SUMMARY={summary_path}"]
            + [str(ROOT / "bench" / "reserve.js")],
            capture_output=True,
            text=True,
        )
        if k6.returncode != 0 or not summary_path.exists():
            raise SystemExit(f"k6 failed:\n{k6.stdout}\n{k6.stderr}")
        metrics = json.loads(summary_path.read_text())["metrics"]

    with psycopg.connect(config.database_url()) as conn:
        report = verify_drop(conn, drop_id)

    def count(name: str) -> int:
        return int(metrics[name]["values"]["count"]) if name in metrics else 0

    latency = metrics["http_req_duration"]["values"]
    return {
        "drop_id": drop_id,
        "sold": report.live_holds,
        "oversold": report.oversold,
        "available_after": report.available,
        "violations": report.violations,
        "responses_held": count("held"),
        "responses_sold_out": count("sold_out"),
        "responses_unexpected": count("unexpected"),
        "requests_per_second": metrics["http_reqs"]["values"]["rate"],
        "latency_ms": {
            "p50": latency["med"],
            "p95": latency["p(95)"],
            "p99": latency["p(99)"],
            "max": latency["max"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment", choices=sorted(EXPERIMENTS))
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--strategies", nargs="*")
    args = parser.parse_args()
    plan = EXPERIMENTS[args.experiment]
    repeats = args.repeats or plan["repeats"]
    strategies = args.strategies or plan["strategies"]

    env = environment()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "bench" / "results" / f"{stamp}-{env['git_commit']}"
    out_dir.mkdir(parents=True, exist_ok=True)

    runs = []
    for pool in sorted({variant["pool"] for variant in plan["variants"]}):
        server = start_server(pool)
        try:
            for variant in (v for v in plan["variants"] if v["pool"] == pool):
                for strategy in strategies:
                    for repeat in range(1, repeats + 1):
                        result = run_once(
                            strategy, variant["units"], variant["buyers"], variant["vus"]
                        )
                        runs.append({"strategy": strategy, "repeat": repeat, **variant, **result})
                        print(
                            f"{strategy:<14} {variant['vus']:>3} at a time, pool {pool:>2}, "
                            f"run {repeat}: sold {result['sold']:>5} "
                            f"oversold {result['oversold']:>4} "
                            f"{result['requests_per_second']:>7.0f} req/s  "
                            f"p99 {result['latency_ms']['p99']:>7.1f} ms"
                        )
        finally:
            server.terminate()
            server.wait(timeout=10)

    out_file = out_dir / f"{args.experiment}.json"
    out_file.write_text(
        json.dumps(
            {
                "experiment": args.experiment,
                "variants": plan["variants"],
                "repeats": repeats,
                "started_at_utc": stamp,
                "environment": env,
                "runs": runs,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"\nsaved {out_file.relative_to(ROOT)}")
    if env["git_dirty"]:
        print("note: uncommitted changes were present, so this run is not tied to one commit")


if __name__ == "__main__":
    main()
