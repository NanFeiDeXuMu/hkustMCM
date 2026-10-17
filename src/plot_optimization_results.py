"""Four paper figures from saved aging and pull-routing experiment metrics.

The figures do not run simulations or reinterpret the selection rules. Every
selection marker comes from the corresponding saved selection.json file.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "tmp/matplotlib"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import ScalarFormatter

BLUE, ORANGE, TEAL, PURPLE = "#567086", "#D08C39", "#218B82", "#8C70AD"
FAIL = "#B75D42"
MODELS = ["A10", "A100-SXM4-80GB", "A800-SXM4-80GB", "H800", "GPU-series-2", "GPU-series-1"]
MODEL_LABELS = ["A10", "A100", "A800", "H800", "Series-2", "Series-1"]
MAIN = ["q3_base", "q3_arrival_old", "q3_pull_aa", "q3_pull_ahs", "q3_pull_all"]
MAIN_LABELS = ["No routing", "Arrival: AA", "Pull: AA", "Pull: AHS (center)", "Pull: all"]
SCENARIO_LABELS = {
    "q3_base": "No routing",
    "q3_arrival_old": "Arrival: A100 / A800",
    "q3_pull_aa": "Pull: A100 / A800",
    "q3_pull_ahs": "Pull: A100 / H800 / Series-2 (center)",
    "q3_pull_all": "Pull: all four donors",
    "q3_p25": "AHS: participation p = .25",
    "q3_p100": "AHS: participation p = 1",
    "q3_cap10": "AHS: borrowing cap = .10",
    "q3_cap50": "AHS: borrowing cap = .50",
    "q3_tau1h": "AHS: minimum wait = 1 h",
    "q3_tau6h": "AHS: minimum wait = 6 h",
    "q3_speed2": "AHS: assumed speedup = 2 [sensitivity]",
    "q3_seed18": "AHS: eligibility seed 18 [sensitivity]",
    "q3_seed19": "AHS: eligibility seed 19 [sensitivity]",
    "q3_noanon": "Pull: A100 / H800 only",
    "q3_sjf_bridge": "AHS with SJF baseline [sensitivity]",
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save(fig, directory: Path, name: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for extension in ("png", "svg"):
        target = directory / f"{name}.{extension}"
        fig.savefig(target, dpi=190, bbox_inches="tight")
        print(target)
    plt.close(fig)


def clean_axis(ax) -> None:
    ax.grid(alpha=.2)
    ax.set_axisbelow(True)


def annotate_points(ax, xs, ys, labels, *, fontsize=7.4) -> None:
    """Place short labels in display coordinates without moving data points.

    This small greedy placement only affects labels. It is deterministic and
    checks collisions against earlier labels, every marker, and the axes edge.
    """
    ax.figure.canvas.draw()
    points = ax.transData.transform(np.column_stack([xs, ys]))
    dpi_scale = ax.figure.dpi / 72
    bounds = ax.get_window_extent()
    placed = []
    offsets = [(8, 7), (8, -13), (-8, 7), (-8, -13), (0, 19), (0, -24),
               (20, 14), (20, -20), (-20, 14), (-20, -20), (0, 32), (0, -36),
               (34, 6), (-34, 6), (34, -14), (-34, -14)]
    for (x, y), (px, py), label in zip(zip(xs, ys), points, labels):
        width, height = max(11, len(label) * fontsize * .57) * dpi_scale, (fontsize + 4) * dpi_scale
        best = None
        for rank, (dx, dy) in enumerate(offsets):
            cx, cy = px + dx*dpi_scale, py + dy*dpi_scale
            align = "left" if dx > 0 else "right" if dx < 0 else "center"
            left = cx if align == "left" else cx-width if align == "right" else cx-width/2
            rectangle = (left-2, cy-height/2, left+width+2, cy+height/2)
            l, b, r, t = rectangle
            overlap = sum(max(0, min(r, pr)-max(l, pl)) * max(0, min(t, pt)-max(b, pb))
                          for pl, pb, pr, pt in placed)
            marker_hits = sum(l-3 <= qx <= r+3 and b-3 <= qy <= t+3 for qx, qy in points)
            outside = max(0, bounds.x0-l) + max(0, r-bounds.x1) + max(0, bounds.y0-b) + max(0, t-bounds.y1)
            score = overlap + 200*marker_hits + 1000*outside + rank*.05
            if best is None or score < best[0]:
                best = (score, dx, dy, align, rectangle)
        _, dx, dy, align, rectangle = best
        placed.append(rectangle)
        ax.annotate(label, (x, y), xytext=(dx, dy), textcoords="offset points",
                    ha=align, va="center", fontsize=fontsize, color="#333333",
                    arrowprops={"arrowstyle": "-", "color": "#AAAAAA", "lw": .45},
                    bbox={"facecolor": "white", "edgecolor": "none", "alpha": .8, "pad": .3})


def question2(root: Path) -> None:
    directory = root / "outputs/question2"
    sweep = pd.read_csv(directory / "aging_sweep/sweep.csv").sort_values("alpha")
    selection = read_json(directory / "aging_selection.json")
    alpha = selection["selected_alpha"]
    chosen = sweep.loc[sweep.alpha == alpha].iloc[0]
    legacy = {name: read_json(directory / "legacy_diagnostics" / name / "metrics.json")
              for name in ("fcfs", "sjf", "backfill")}
    fig, axes = plt.subplots(2, 2, figsize=(11.7, 8), layout="constrained")
    baseline_specs = [("fcfs", "FCFS", BLUE, "s"), ("sjf", "Strict SJF", ORANGE, "D"),
                      ("backfill", "Backfill 128", PURPLE, "^")]
    for ax, field, title, scale in [
        (axes[0, 0], "wait_hours_max", "(a) Mean versus maximum wait", "log"),
        (axes[0, 1], "wait_hours_p99", "(b) Mean versus P99 wait", "log"),
    ]:
        ax.plot(sweep.wait_hours_mean, sweep[field], "o-", color=TEAL, lw=1.4, ms=4,
                label="Aging sweep", zorder=2)
        for name, label, color, marker in baseline_specs:
            metric = legacy[name]
            ax.scatter(metric["wait_hours_mean"], metric[field], marker=marker, color=color,
                       s=60, label=label, edgecolors="white", linewidths=.7, zorder=4)
        ax.scatter(chosen.wait_hours_mean, chosen[field], marker="*", color="#E7BA43",
                   edgecolors="#333333", linewidths=.6, s=180, zorder=5,
                   label=rf"Selected $\alpha^*={alpha:g}$")
        ax.set_yscale(scale)
        ax.set_xscale("symlog", linthresh=20)
        right = max(float(sweep.wait_hours_mean.max()), *(m["wait_hours_mean"] for m in legacy.values()))
        ax.set_xlim(0, right*1.22)
        ax.margins(y=.18)
        ax.set_xlabel("Mean wait (hours; symlog scale)")
        ax.set_ylabel("Maximum wait (hours; log scale)" if field.endswith("max") else "P99 wait (hours; log scale)")
        ax.set_title(title, loc="left", fontsize=11)
        clean_axis(ax)
    axes[0, 0].legend(loc="upper right", fontsize=8, framealpha=.92)
    for ax, field, title, ylabel in [
        (axes[1, 0], "wait_hours_mean", "(c) Average-wait response", "Mean wait (hours)"),
        (axes[1, 1], "last_completion_days", "(d) Time to finish all submitted jobs", "Last completion (days from time zero)"),
    ]:
        ax.plot(sweep.alpha, sweep[field], "o-", color=TEAL, ms=4)
        ax.axhline(legacy["fcfs"][field], color=BLUE, ls="--", lw=1, label="FCFS")
        ax.axvline(alpha, color="#777777", ls=":", lw=1)
        ax.scatter(alpha, chosen[field], marker="*", color="#E7BA43", edgecolors="#333333",
                   linewidths=.6, s=180, zorder=5)
        ax.set_xscale("symlog", linthresh=.001, linscale=.7)
        ax.set_xticks([0, .001, .01, .1, 1, 10, 100], ["0", ".001", ".01", ".1", "1", "10", "100"])
        ax.set_xlabel(r"Aging coefficient $\alpha$ (symlog scale)")
        ax.set_ylabel(ylabel)
        ax.set_title(title, loc="left", fontsize=11)
        clean_axis(ax)
    axes[1, 1].legend(fontsize=8)
    fig.suptitle("Aging trades short-job preference against waiting-time fairness", fontsize=13)
    for ax, field in [(axes[0, 0], "wait_hours_max"), (axes[0, 1], "wait_hours_p99")]:
        annotate_points(ax, sweep.wait_hours_mean, sweep[field], [f"{a:g}" for a in sweep.alpha])
    save(fig, directory / "figures", "04_aging_tradeoff")

    groups = ["<=1h", "1h-1d", "1d-7d", ">7d"]
    cases = [("FCFS", directory / "legacy_diagnostics/fcfs", BLUE),
             ("Strict SJF", directory / "legacy_diagnostics/sjf", ORANGE),
             (rf"Aging $\alpha^*={alpha:g}$", directory / "aging_best", TEAL)]
    fig, axes = plt.subplots(1, 2, figsize=(10.8, 4.5), layout="constrained")
    x = np.arange(len(groups))
    for i, (label, path, color) in enumerate(cases):
        table = pd.read_csv(path / "metrics_by_job_group.csv").set_index("group").loc[groups]
        for ax, field, title in [(axes[0], "wait_hours_mean", "(a) Mean wait"),
                                 (axes[1], "wait_hours_max", "(b) Maximum wait")]:
            ax.bar(x + (i-1)*.25, table[field], width=.24, label=label, color=color)
            ax.set_xticks(x, ["≤1 h", "1 h–1 d", "1–7 d", ">7 d"])
            ax.set_xlabel("Original requested runtime")
            ax.set_ylabel("Waiting time (hours)")
            ax.set_title(title, loc="left", fontsize=11)
            clean_axis(ax)
    axes[0].legend(fontsize=9)
    fig.suptitle("Waiting times by original job duration", fontsize=13)
    save(fig, directory / "figures", "05_aging_waiting_by_duration")


def truth(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def question3(root: Path) -> None:
    directory = root / "outputs/question3/improved"
    comparison = pd.read_csv(directory / "comparison.csv").set_index("scenario")
    selection = read_json(directory / "selection.json")
    imbalance_names = MAIN.copy()
    imbalance_labels = MAIN_LABELS.copy()
    selected_name = selection["selected_scenario"]
    if selected_name not in imbalance_names:
        imbalance_names.append(selected_name)
        imbalance_labels.append("Selected: " + SCENARIO_LABELS.get(selected_name, selected_name).replace("AHS: borrowing cap", "AHS cap"))
    main = comparison.loc[imbalance_names]
    fig, axes = plt.subplots(1, 2, figsize=(12.6, 4.8), layout="constrained",
                             gridspec_kw={"width_ratios": [1, 1.6]})
    values = main.gpu_stranded_upper_bound_hours_90d.to_numpy()/1e6
    bars = axes[0].barh(imbalance_labels, values, color=[BLUE, ORANGE, PURPLE, TEAL, "#789B5B", "#B39849"][:len(main)])
    axes[0].bar_label(bars, fmt="%.3f", padding=4, fontsize=9)
    axes[0].set_xlim(0, max(values)*1.22)
    axes[0].invert_yaxis()
    axes[0].set_xlabel("Stranded matching bound (million GPU-hours)")
    axes[0].set_title("(a) Fixed-donor stranding during days 0–90", loc="left", fontsize=11)
    axes[0].grid(axis="x", alpha=.2)
    axes[0].set_axisbelow(True)
    allocation = np.stack([
        pd.read_csv(directory / name / "metrics_by_assigned_model.csv").set_index("assigned_model")
        .loc[MODELS, "gpu_allocation_rate_90d"].to_numpy()*100 for name in imbalance_names
    ])
    heatmap = axes[1].imshow(allocation, cmap="YlGnBu", vmin=0, vmax=100, aspect="auto")
    axes[1].set_xticks(np.arange(6), MODEL_LABELS)
    axes[1].set_yticks(np.arange(len(main)), imbalance_labels)
    axes[1].set_title("(b) Simulated GPU allocation by pool (%)", loc="left", fontsize=11)
    for row in range(len(main)):
        for column in range(6):
            value = allocation[row, column]
            axes[1].text(column, row, f"{value:.1f}", ha="center", va="center", fontsize=9,
                         color="white" if value >= 65 else "#222222")
    fig.colorbar(heatmap, ax=axes[1], fraction=.035, pad=.025, label="Allocated capacity (%)")
    fig.suptitle("Pool imbalance under the same aging baseline", fontsize=13)
    fig.supxlabel("Fixed donor set: A100, A800, H800, Series-2. Stranding is a matching bound on the realized timeline, not an achievable gain.",
                  fontsize=8.2)
    save(fig, directory / "figures", "04_imbalance")

    fig = plt.figure(figsize=(12.8, 7.2), layout="constrained")
    grid = fig.add_gridspec(1, 2, width_ratios=[1.4, 1])
    ax, key = fig.add_subplot(grid[0]), fig.add_subplot(grid[1])
    key.axis("off")
    names = list(comparison.index)
    x = comparison.a10_mean_wait_gain_hours.to_numpy()
    y = comparison.max_donor_p95_delta_hours.to_numpy()
    selected = selection["selected_scenario"]
    named_selected = selection["named_only_selected_scenario"]
    p95_bound = selection.get("max_native_p95_increase_hours", 2)
    mean_bound = selection.get("max_native_mean_increase_hours", .5)
    ax.axhline(p95_bound, color="#555555", ls="--", lw=1.1, label=f"P95 bound: {p95_bound:g} h")
    ax.axhline(0, color="#AAAAAA", lw=.6)
    for i, name in enumerate(names):
        row = comparison.loc[name]
        feasible = truth(row.management_feasible)
        eligible = truth(row.selection_eligible)
        marker = "o" if eligible else "D" if name == "q3_sjf_bridge" else "^" if name == "q3_speed2" else "s"
        if name == "q3_base":
            marker = "P"
        color = TEAL if feasible else FAIL
        ax.scatter(x[i], y[i], s=60, marker=marker, color=color, edgecolors="white", linewidths=.7, zorder=4)
        mean_fail = row.max_donor_mean_wait_delta_hours > mean_bound
        if mean_fail:
            ax.scatter(x[i], y[i], s=100, marker="x", color="#222222", linewidths=1.1, zorder=5)
        if name == "q3_pull_ahs":
            ax.scatter(x[i], y[i], s=160, marker="o", facecolors="none", edgecolors=PURPLE, linewidths=1.6, zorder=3)
        if name in {selected, named_selected}:
            ax.scatter(x[i], y[i], s=215, marker="*", facecolors="none", edgecolors="#A07817", linewidths=1.3, zorder=5)
        tags = []
        if name == selected:
            tags.append("selected")
        if name == named_selected:
            tags.append("named-only selected")
        suffix = "  · " + "; ".join(tags) if tags else ""
        key.text(0, .98-i*.045, f"{i:02d}  {SCENARIO_LABELS.get(name, name)}{suffix}",
                 transform=key.transAxes, fontsize=8.8, va="top", color=color)
    ax.set_yscale("symlog", linthresh=2, linscale=.75)
    ax.yaxis.set_major_formatter(ScalarFormatter())
    ax.margins(x=.12, y=.14)
    ax.set_xlabel("A10 mean-wait reduction (hours; own-policy baseline)")
    ax.set_ylabel("Largest donor native P95-wait increase (hours; symlog)")
    ax.set_title("Benefit versus native-user cost", loc="left", fontsize=12)
    clean_axis(ax)
    legend = [Line2D([], [], marker="o", color="none", markerfacecolor=TEAL, markeredgecolor="none", label="Meets both bounds"),
              Line2D([], [], marker="o", color="none", markerfacecolor=FAIL, markeredgecolor="none", label="Fails a management bound"),
              Line2D([], [], marker="x", color="none", markeredgecolor="#222222", label=f"Native mean increase > {mean_bound:g} h"),
              Line2D([], [], marker="o", color="none", markerfacecolor="none", markeredgecolor=PURPLE, label="AHS center"),
              Line2D([], [], marker="*", color="none", markerfacecolor="none", markeredgecolor="#A07817", markersize=12, label="Saved selection"),
              Line2D([], [], color="#555555", ls="--", label=f"Native P95 bound: {p95_bound:g} h")]
    key.legend(handles=legend, loc="lower left", bbox_to_anchor=(-.02, -.01), fontsize=8.6, frameon=False)
    fig.suptitle("Routing scenarios: gains do not guarantee zero native-job harm", fontsize=13)
    fig.supxlabel("Circles: selection candidates. Squares: seeds; triangle: speed; diamond: SJF bridge (sensitivity only). P95 cost is a difference of distribution quantiles.",
                  fontsize=8.3)
    annotate_points(ax, x, y, [f"{i:02d}" for i in range(len(names))], fontsize=8)
    save(fig, directory / "figures", "05_benefit_vs_native_cost")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="Repository root containing outputs/")
    parser.add_argument("--suite", choices=("all", "question2", "question3"), default="all")
    args = parser.parse_args()
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                         "svg.fonttype": "none"})
    if args.suite in {"all", "question2"}:
        question2(args.root.resolve())
    if args.suite in {"all", "question3"}:
        question3(args.root.resolve())


if __name__ == "__main__":
    main()
