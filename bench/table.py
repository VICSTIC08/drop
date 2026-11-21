"""Turn saved benchmark results into a Markdown table. Every number comes from the JSON.

Usage:
    uv run python bench/table.py                 (newest result of each experiment)
    uv run python bench/table.py bench/results/<folder>/burst.json
"""

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"


def newest(experiment: str) -> Path | None:
    files = sorted(RESULTS.glob(f"*/{experiment}.json"))
    return files[-1] if files else None


def render(path: Path) -> str:
    data = json.loads(path.read_text())
    by_strategy = defaultdict(list)
    for run in data["runs"]:
        by_strategy[run["strategy"]].append(run)
    settings, env = data["settings"], data["environment"]

    lines = [
        f"**{data['experiment']}**: {settings['units']} units, {settings['buyers']} buyers, "
        f"{settings['vus']} at a time, {data['repeats']} runs per strategy "
        f"(commit `{env['git_commit']}`, {env['system']} {env['machine']}, "
        f"{env['cpu_count']} CPUs, PostgreSQL {env['postgres']}, pool {env['db_pool_max_size']}; "
        "load generator on the same machine)",
        "",
        "| Strategy | Sold (min–max) | Oversold (min–max) | Runs with a violation "
        "| Requests/s (median) | p50 ms | p99 ms (median) |",
        "|---|---|---|---|---|---|---|",
    ]
    for strategy, runs in by_strategy.items():
        sold = [r["sold"] for r in runs]
        over = [r["oversold"] for r in runs]
        bad = sum(1 for r in runs if r["violations"])
        rps = statistics.median(r["requests_per_second"] for r in runs)
        p50 = statistics.median(r["latency_ms"]["p50"] for r in runs)
        p99 = statistics.median(r["latency_ms"]["p99"] for r in runs)
        lines.append(
            f"| `{strategy}` | {min(sold)}–{max(sold)} | {min(over)}–{max(over)} "
            f"| {bad} of {len(runs)} | {rps:.0f} | {p50:.1f} | {p99:.1f} |"
        )
    return "\n".join(lines)


def main() -> None:
    if len(sys.argv) > 1:
        paths = [Path(arg) for arg in sys.argv[1:]]
    else:
        paths = [p for p in (newest("burst"), newest("throughput")) if p]
    if not paths:
        raise SystemExit("no results yet: run bench/run.py first")
    print("\n\n".join(render(path) for path in paths))


if __name__ == "__main__":
    main()
