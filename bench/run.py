"""Run a benchmark experiment and save the raw results as JSON.

For each strategy and each repeat it creates a fresh drop, lets k6 send the
buyers over real HTTP, then asks `verify` to judge the final database state.
Nothing here is rounded or summarised: bench/table.py does that from the file.

Usage:
    uv run python bench/run.py burst
    uv run python bench/run.py throughput --repeats 5
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
from drop.strategies import STRATEGY_NAMES
from drop.verify import verify_drop

ROOT = Path(__file__).resolve().parents[1]
PORT = 8010
BASE_URL = f"http://127.0.0.1:{PORT}"

# burst: far more buyers than units, the flash-sale moment. Answers "does it oversell?"
# throughput: a unit for every buyer, so every request does the full work of
#             reserving. Answers "how fast is the reserve path, and how bad is the tail?"
EXPERIMENTS = {
    "burst": {"units": 100, "buyers": 1000, "vus": 200},
    "throughput": {"units": 2000, "buyers": 2000, "vus": 50},
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
        "db_pool_max_size": config.pool_max_size(),
        "note": "load generator, API and database all share this one machine",
    }


def start_server() -> subprocess.Popen:
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "drop.main:app", "--port", str(PORT)]
        + ["--log-level", "warning"],
        cwd=ROOT,
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
        timeout=30,
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
        "units": units,
        "buyers": buyers,
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
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--strategies", nargs="*", default=STRATEGY_NAMES)
    args = parser.parse_args()
    settings = EXPERIMENTS[args.experiment]

    env = environment()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "bench" / "results" / f"{stamp}-{env['git_commit']}"
    out_dir.mkdir(parents=True, exist_ok=True)

    server = start_server()
    runs = []
    try:
        for strategy in args.strategies:
            for repeat in range(1, args.repeats + 1):
                result = run_once(strategy, **settings)
                runs.append({"strategy": strategy, "repeat": repeat, **result})
                print(
                    f"{strategy:<14} run {repeat}: sold {result['sold']:>5} "
                    f"oversold {result['oversold']:>4}  "
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
                "settings": settings,
                "repeats": args.repeats,
                "started_at_utc": stamp,
                "environment": env,
                "runs": runs,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"\nsaved {out_file.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
