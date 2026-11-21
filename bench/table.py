"""Turn saved benchmark results into Markdown tables. Every number comes from the JSON.

Usage:
    uv run python bench/table.py            (print the newest result of each experiment)
    uv run python bench/table.py --readme   (also write them into README.md)
"""

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "bench" / "results"
README = ROOT / "README.md"
START, END = "<!-- results:start -->", "<!-- results:end -->"
ORDER = ["burst", "throughput", "sweep", "pool"]


def newest(experiment: str) -> dict | None:
    """The most recent saved run of an experiment that used the current file format."""
    for path in sorted(RESULTS.glob(f"*/{experiment}.json"), reverse=True):
        data = json.loads(path.read_text())
        if "variants" in data:
            data["path"] = str(path.relative_to(ROOT))
            return data
    return None


def med(runs: list[dict], *keys: str) -> float:
    values = []
    for run in runs:
        value = run
        for key in keys:
            value = value[key]
        values.append(value)
    return statistics.median(values)


def setup_line(data: dict) -> str:
    env = data["environment"]
    dirty = ", uncommitted changes present" if env["git_dirty"] else ""
    return (
        f"{data['repeats']} runs per row, median shown. Commit `{env['git_commit']}`{dirty}, "
        f"{env['system']} {env['machine']}, {env['cpu_count']} CPUs, "
        f"PostgreSQL {env['postgres']}. Raw data: `{data['path']}`."
    )


def render_single(data: dict, title: str) -> str:
    variant = data["variants"][0]
    by_strategy = defaultdict(list)
    for run in data["runs"]:
        by_strategy[run["strategy"]].append(run)
    lines = [
        f"**{title}**: {variant['units']} units, {variant['buyers']} buyers, "
        f"{variant['vus']} at a time, pool of {variant['pool']} connections.",
        "",
        "| Strategy | Sold (min–max) | Oversold (min–max) | Runs breaking a rule "
        "| Requests/s | p50 ms | p99 ms |",
        "|---|---|---|---|---|---|---|",
    ]
    for strategy, runs in by_strategy.items():
        sold = [r["sold"] for r in runs]
        over = [r["oversold"] for r in runs]
        bad = sum(1 for r in runs if r["violations"])
        lines.append(
            f"| `{strategy}` | {min(sold)}–{max(sold)} | {min(over)}–{max(over)} "
            f"| {bad} of {len(runs)} | {med(runs, 'requests_per_second'):.0f} "
            f"| {med(runs, 'latency_ms', 'p50'):.1f} | {med(runs, 'latency_ms', 'p99'):.1f} |"
        )
    return "\n".join([*lines, "", setup_line(data)])


def render_grid(data: dict, title: str, axis: str, axis_label: str) -> str:
    levels = sorted({run[axis] for run in data["runs"]})
    cells = defaultdict(list)
    for run in data["runs"]:
        cells[(run["strategy"], run[axis])].append(run)
    strategies = list(dict.fromkeys(run["strategy"] for run in data["runs"]))
    lines = [
        f"**{title}** Each cell: requests/s, then p99 in ms.",
        "",
        "| Strategy | " + " | ".join(f"{axis_label} {level}" for level in levels) + " |",
        "|---|" + "---|" * len(levels),
    ]
    for strategy in strategies:
        row = [
            f"{med(cells[(strategy, level)], 'requests_per_second'):.0f} / "
            f"{med(cells[(strategy, level)], 'latency_ms', 'p99'):.1f}"
            for level in levels
        ]
        lines.append(f"| `{strategy}` | " + " | ".join(row) + " |")
    return "\n".join([*lines, "", setup_line(data)])


def render(data: dict) -> str:
    name = data["experiment"]
    if name == "burst":
        return render_single(data, "Burst (the flash-sale moment)")
    if name == "throughput":
        return render_single(data, "Throughput (a unit for every buyer)")
    if name == "sweep":
        variant = data["variants"][0]
        title = (
            f"Rising concurrency: {variant['units']} units, {variant['buyers']} buyers, "
            f"pool of {variant['pool']}."
        )
        return render_grid(data, title, "vus", "at a time:")
    variant = data["variants"][0]
    title = (
        f"Connection pool size: {variant['units']} units, {variant['buyers']} buyers, "
        f"{variant['vus']} at a time."
    )
    return render_grid(data, title, "pool", "pool of")


def main() -> None:
    found = [data for data in (newest(name) for name in ORDER) if data]
    if not found:
        raise SystemExit("no results yet: run bench/run.py first")
    text = "\n\n".join(render(data) for data in found)
    print(text)
    if "--readme" in sys.argv:
        readme = README.read_text()
        if START not in readme or END not in readme:
            raise SystemExit("README.md is missing the results markers")
        before, rest = readme.split(START, 1)
        after = rest.split(END, 1)[1]
        README.write_text(f"{before}{START}\n{text}\n{END}{after}")
        print("\nwrote the tables into README.md")


if __name__ == "__main__":
    main()
