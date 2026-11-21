"""Draw p99 latency against concurrency for each strategy, from the saved sweep.

Usage: uv run python bench/figure.py
"""

import statistics
from collections import defaultdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from table import ROOT, newest  # noqa: E402

# Fixed colour and marker per strategy, so a strategy looks the same in every figure.
STYLE = {
    "naive": ("#2a78d6", "o"),
    "row_lock": ("#eb6834", "s"),
    "atomic_update": ("#1baf7a", "^"),
    "redis_counter": ("#eda100", "D"),
    "unit_rows": ("#e87ba4", "v"),
}
INK, MUTED, GRID, SURFACE = "#1f1f1e", "#6b6a64", "#e6e5df", "#fcfcfb"


def main() -> None:
    data = newest("sweep")
    if data is None:
        raise SystemExit("no sweep results yet: run `bench/run.py sweep` first")
    cells = defaultdict(list)
    for run in data["runs"]:
        cells[(run["strategy"], run["vus"])].append(run["latency_ms"]["p99"])
    levels = sorted({vus for _, vus in cells})
    strategies = list(dict.fromkeys(run["strategy"] for run in data["runs"]))

    fig, ax = plt.subplots(figsize=(8, 4.8), dpi=160, facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    for strategy in strategies:
        color, marker = STYLE[strategy]
        values = [statistics.median(cells[(strategy, vus)]) for vus in levels]
        ax.plot(
            levels, values, color=color, marker=marker, linewidth=2, markersize=6, label=strategy
        )

    ax.set_yscale("log")
    ticks = (5, 10, 20, 50, 100, 200, 500, 1000, 2000)
    low, high = ax.get_ylim()
    shown = [t for t in ticks if low <= t <= high]
    ax.set_yticks(shown, [str(t) for t in shown])
    ax.set_xscale("log")
    ax.set_xticks(levels, [str(level) for level in levels])
    ax.set_xlim(levels[0] * 0.85, levels[-1] * 1.18)
    ax.minorticks_off()
    ax.set_xlabel("Buyers sending requests at the same time", color=MUTED)
    ax.set_ylabel("p99 latency in ms (log scale)", color=MUTED)
    variant = data["variants"][0]
    ax.set_title(
        "Slowest 1% of reservations as concurrency rises",
        loc="left",
        color=INK,
        fontsize=12,
        fontweight="bold",
    )
    env = data["environment"]
    fig.text(
        0.125,
        0.01,
        f"{variant['buyers']} buyers, {variant['units']} units, median of {data['repeats']} runs, "
        f"commit {env['git_commit']}, one {env['cpu_count']}-CPU machine running everything.",
        color=MUTED,
        fontsize=7.5,
    )
    ax.grid(True, which="major", color=GRID, linewidth=0.8)
    ax.tick_params(colors=MUTED, length=0)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.legend(frameon=False, fontsize=8, labelcolor=INK, loc="upper left")

    out = ROOT / "figures" / "p99_vs_concurrency.png"
    out.parent.mkdir(exist_ok=True)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out, facecolor=SURFACE)
    print(f"saved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
