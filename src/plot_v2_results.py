"""Paper figures for the D023/D024 selection and native-harm experiments.

Reads saved metrics only. Historical figure outputs and selection rules remain
unchanged; stars identify the selections recorded by the experiment runner.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from plot_optimization_results import (plt, ROOT, BLUE, ORANGE, TEAL, PURPLE,
                                       MODELS, MODEL_LABELS, annotate_points,
                                       clean_axis, save, truth)
from matplotlib.lines import Line2D
from matplotlib.ticker import ScalarFormatter

GOLD = "#D5AA35"
GROUPS = ["<=1h", "1h-1d", "1d-7d", ">7d"]
SCENARIO_LABELS = {
    "v2_base": "Q2* without routing",
    "v2_ahs": "AHS: no duration / reserve controls",
    "v2_ah": "AH: no duration / reserve controls",
    "v2_ah_d1": "AH: duration ≤ 1 d",
    "v2_ah_d7": "AH: duration ≤ 7 d",
    "v2_ah_r10": "AH: reserve = .10",
    "v2_ah_r20": "AH: reserve = .20",
    "v2_ah_d7_r10": "AH: duration ≤ 7 d; reserve = .10",
    "v2_ah_d1_cap50": "AH: duration ≤ 1 d; borrowing cap = .50",
    "v2_ahs_d1": "AHS: duration ≤ 1 d",
    "v2_ahs_d7_r10": "AHS: duration ≤ 7 d; reserve = .10",
    "v2_aha_d7_r10": "AHA: duration ≤ 7 d; reserve = .10",
    "v2_p25": "Primary-config sensitivity: p = .25",
    "v2_p100": "Primary-config sensitivity: p = 1",
    "v2_seed18": "Primary-config sensitivity: seed 18",
    "v2_seed19": "Primary-config sensitivity: seed 19",
    "v2_speed2": "Primary-config sensitivity: speedup = 2",
    "v2_tau1h": "Primary-config sensitivity: minimum wait = 1 h",
    "v2_sjf_bridge": "Primary config with SJF baseline",
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def time_label(seconds: float) -> str:
    return f"{seconds/86400:g} d" if seconds >= 86400 else f"{seconds/3600:g} h"


def policy_label(row) -> str:
    if row.policy == "aging":
        return rf"Linear aging $\alpha={row.alpha:g}$"
    if row.policy == "log_aging":
        return "Log aging T=" + time_label(row.tau_seconds)
    return {"fcfs": "FCFS", "sjf": "Strict SJF", "backfill": "Backfill"}.get(row.policy, row.policy)


def question2(root: Path) -> None:
    directory = root / "outputs/question2"
    table = pd.read_csv(directory / "comparison_v2.csv").set_index("scenario")
    selection = read_json(directory / "selection_v2.json")
    selected = table.loc[selection["selected_scenario"]]
    linear = table[table.policy == "aging"].sort_values("alpha")
    logarithmic = table[table.policy == "log_aging"].sort_values("tau_seconds")
    bound = selection["c2_mean_bound_hours"]
    fig, axes = plt.subplots(1, 2, figsize=(12.2, 5.4), layout="constrained")
    for ax, field, title in [(axes[0], "wait_hours_max", "(a) Maximum wait"),
                             (axes[1], "wait_hours_p99", "(b) P99 wait")]:
        ax.axvspan(0, bound, color="#DCF1E9", alpha=.7, zorder=0)
        ax.axvline(bound, color=TEAL, lw=.9, ls=":")
        ax.plot(linear.wait_hours_mean, linear[field], "o-", ms=4, color=TEAL, lw=1.4, label="Linear aging")
        ax.plot(logarithmic.wait_hours_mean, logarithmic[field], "o-", ms=4, color=PURPLE, lw=1.4, label="Log aging")
        for name, label, marker, color in [("fcfs", "FCFS", "s", BLUE),
                                          ("sjf", "Strict SJF", "D", ORANGE),
                                          ("backfill", "Backfill 128", "^", "#555555"),
                                          ("backfill_l512", "Backfill 512", "v", "#888888")]:
            row = table.loc[name]
            ax.scatter(row.wait_hours_mean, row[field], marker=marker, color=color, s=65,
                       edgecolors="white", linewidths=.6, label=label, zorder=4)
        legacy = table.loc["alpha_100"]
        ax.scatter(legacy.wait_hours_mean, legacy[field], marker="o", s=145, facecolors="none",
                   edgecolors=ORANGE, linewidths=1.5, label=r"Old bound-constrained $\alpha=100$", zorder=5)
        ax.scatter(selected.wait_hours_mean, selected[field], marker="*", s=210, color=GOLD,
                   edgecolors="#333333", linewidths=.6, label="Q2*: " + policy_label(selected), zorder=6)
        ax.set_xscale("symlog", linthresh=20)
        ax.set_yscale("log")
        ax.set_xlim(0, table.wait_hours_mean.max()*1.24)
        ax.margins(y=.2)
        ax.set_xlabel("Mean wait (hours; symlog scale)")
        ax.set_ylabel("Waiting time (hours; log scale)")
        ax.set_title(title, loc="left", fontsize=11)
        ax.text(.025, .025, f"Shading: C2 mean ≤ {bound:.3f} h\nC1 group checks also required", transform=ax.transAxes,
                fontsize=8, va="bottom", bbox={"facecolor": "white", "alpha": .85, "edgecolor": "none", "pad": 3})
        clean_axis(ax)
    axes[0].legend(loc="upper right", fontsize=7.7, framealpha=.94)
    fig.suptitle("Selecting Q2* with group protection and an average-wait budget", fontsize=13)
    fig.supxlabel("Curve labels are linear α or log-aging T. Selections use the same trace; α=100 remains the earlier maximum-wait-cap alternative.", fontsize=8.4)
    for ax, field in [(axes[0], "wait_hours_max"), (axes[1], "wait_hours_p99")]:
        points = pd.concat([linear, logarithmic])
        labels = [f"α={a:g}" for a in linear.alpha] + ["T=" + time_label(t) for t in logarithmic.tau_seconds]
        annotate_points(ax, points.wait_hours_mean, points[field], labels, fontsize=7)
    save(fig, directory / "figures_v2", "04_policy_tradeoff")

    cases = [("FCFS", root / table.loc["fcfs", "output_path"], BLUE),
             ("Strict SJF", root / table.loc["sjf", "output_path"], ORANGE),
             ("Q2*: " + policy_label(selected), directory / "q2_star", TEAL)]
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.8), layout="constrained")
    x = np.arange(4)
    for i, (label, path, color) in enumerate(cases):
        groups = pd.read_csv(path / "tail_by_duration_group.csv").set_index("group").loc[GROUPS]
        for ax, field, title in [(axes[0], "wait_hours_mean", "(a) Mean wait: C1 protects every group"),
                                 (axes[1], "bounded_slowdown_60s_p99", "(b) P99 bounded slowdown")]:
            ax.bar(x+(i-1)*.25, groups[field], .24, label=label, color=color)
            ax.set_xticks(x, ["≤1 h", "1 h–1 d", "1–7 d", ">7 d"])
            ax.set_xlabel("Original requested runtime")
            ax.set_title(title, loc="left", fontsize=11)
            clean_axis(ax)
    axes[0].set_ylabel("Mean waiting time (hours)")
    axes[0].legend(fontsize=8.3)
    axes[1].set_yscale("log")
    axes[1].set_ylabel("Unitless ratio (log scale)")
    fig.suptitle("Average protection and tail slowdown by original duration", fontsize=13)
    fig.supxlabel("Bounded slowdown = max[1, (completion − submission) / max(actual runtime, 60 seconds)]. All submitted jobs included.", fontsize=8.4)
    save(fig, directory / "figures_v2", "06_group_fairness")


def question3(root: Path) -> None:
    directory = root / "outputs/question3/v2"
    table = pd.read_csv(directory / "comparison.csv").set_index("scenario")
    selection = read_json(directory / "selection.json")
    primary = selection["primary_selected_scenario"]
    conditional = selection["conditional_selected_scenario"]
    p95_bound = selection["native_p95_increase_bound_hours"]
    mean_bound = selection["native_mean_increase_bound_hours"]
    names = list(table.index)
    fig = plt.figure(figsize=(13.3, 8), layout="constrained")
    grid = fig.add_gridspec(1, 2, width_ratios=[1.35, 1.05])
    ax, key = fig.add_subplot(grid[0]), fig.add_subplot(grid[1])
    key.axis("off")
    x, y = table.a10_mean_wait_gain_hours.to_numpy(), table.max_donor_p95_delta_hours.to_numpy()
    ax.axhline(p95_bound, color="#555555", ls="--", lw=1.1)
    ax.axhline(0, color="#AAAAAA", lw=.6)
    for i, name in enumerate(names):
        row = table.loc[name]
        anonymous = truth(row.includes_anonymous_donor)
        feasible = truth(row.management_feasible)
        color = PURPLE if anonymous else TEAL
        marker = "o" if row.phase == "main" else "D" if name == "v2_sjf_bridge" else "^" if name == "v2_speed2" else "s"
        if name == "v2_base":
            marker, color = "P", BLUE
        ax.scatter(x[i], y[i], s=65, marker=marker, facecolors=color if feasible else "none",
                   edgecolors=color, linewidths=1.2, zorder=4)
        if row.max_donor_mean_wait_delta_hours > mean_bound:
            ax.scatter(x[i], y[i], s=95, marker="x", color="#333333", linewidths=1, zorder=5)
        if name == primary:
            ax.scatter(x[i], y[i], s=245, marker="*", color=GOLD, edgecolors="#333333", linewidths=.7, zorder=6)
        if name == conditional:
            ax.scatter(x[i], y[i], s=245, marker="*", facecolors="none", edgecolors=PURPLE, linewidths=1.5, zorder=6)
        tags = []
        if name == primary:
            tags.append("primary")
        if name == conditional:
            tags.append("conditional")
        label = SCENARIO_LABELS.get(name, name)
        if not selection.get("sensitivity_anchor_is_recommendation", True):
            label = label.replace("Primary-config", "Diagnostic-config").replace("Primary config", "Diagnostic config")
        suffix = "  [" + ", ".join(tags) + "]" if tags else ""
        key.text(0, .98-i*.039, f"{i:02d}  {label}{suffix}", transform=key.transAxes,
                 fontsize=8.5, va="top", color=color)
    ax.set_yscale("symlog", linthresh=2, linscale=.75)
    ax.yaxis.set_major_formatter(ScalarFormatter())
    ax.margins(x=.16, y=.13)
    ax.set_xlabel("A10 mean-wait reduction (hours; own-policy baseline)")
    ax.set_ylabel("Largest donor native P95-wait increase (hours; symlog)")
    ax.set_title("Gains versus native-user waiting costs", loc="left", fontsize=12)
    clean_axis(ax)
    handles = [Line2D([], [], marker="o", color="none", markerfacecolor=TEAL, markeredgecolor=TEAL, label="Named donors only"),
               Line2D([], [], marker="o", color="none", markerfacecolor=PURPLE, markeredgecolor=PURPLE, label="Includes anonymous Series-2"),
               Line2D([], [], marker="o", color="none", markerfacecolor="none", markeredgecolor="#555555", label="Open: fails a management bound"),
               Line2D([], [], marker="x", color="none", markeredgecolor="#333333", label=f"Mean increase > {mean_bound:g} h"),
               Line2D([], [], color="#555555", ls="--", label=f"P95 bound: {p95_bound:g} h"),
               Line2D([], [], marker="*", color="none", markerfacecolor=GOLD, markeredgecolor="#333333", markersize=12, label="Primary named-donor selection")]
    key.legend(handles=handles, loc="lower left", bbox_to_anchor=(-.02, -.015), fontsize=8.3, frameon=False)
    fig.suptitle("Duration limits and reserve capacity: v2 routing choices", fontsize=13)
    fig.supxlabel("AH=A100+H800; AHS=AH+Series-2; AHA=AH+A800. Squares/triangle/diamond: sensitivity only; the SJF bridge uses its own SJF baseline.", fontsize=8.4)
    annotate_points(ax, x, y, [f"{i:02d}" for i in range(len(names))], fontsize=8)
    save(fig, directory / "figures", "05_benefit_vs_native_cost")

    cases = [("Q2*: no routing", "v2_base", BLUE), ("Primary named-donor routing", primary, TEAL)]
    # A fallback to no routing is shown explicitly rather than inventing a gain.
    if primary == "v2_base":
        cases = [("Q2*: no routing (primary selection)", "v2_base", BLUE)]
    fig, axes = plt.subplots(1, 2, figsize=(11.8, 4.6), layout="constrained", gridspec_kw={"width_ratios": [1.6, 1]})
    x = np.arange(len(MODELS))
    for i, (label, name, color) in enumerate(cases):
        allocation = pd.read_csv(directory / name / "metrics_by_assigned_model.csv").set_index("assigned_model")
        values = allocation.loc[MODELS, "gpu_allocation_rate_90d"].to_numpy()*100
        bars = axes[0].bar(x+(i-(len(cases)-1)/2)*.36, values, .34, label=label, color=color)
        axes[0].bar_label(bars, fmt="%.1f", padding=3, fontsize=8)
    axes[0].set_xticks(x, MODEL_LABELS)
    axes[0].set_ylim(0, 112)
    axes[0].set_ylabel("Allocated physical capacity during days 0–90 (%)")
    axes[0].set_title("(a) Simulated GPU allocation by pool", loc="left", fontsize=11)
    axes[0].legend(fontsize=8.3, loc="lower left")
    clean_axis(axes[0])
    values = [table.loc[name, "gpu_stranded_upper_bound_hours_90d"]/1e6 for _, name, _ in cases]
    bars = axes[1].bar(range(len(cases)), values, color=[color for _, _, color in cases], width=.6)
    axes[1].bar_label(bars, fmt="%.3f", padding=4, fontsize=10)
    axes[1].set_xticks(range(len(cases)), ["No routing" if name == "v2_base" else "Primary routing" for _, name, _ in cases])
    axes[1].set_ylim(0, max(values)*1.2)
    axes[1].set_ylabel("Million GPU-hours during days 0–90")
    axes[1].set_title("(b) Fixed-donor stranded matching bound", loc="left", fontsize=11)
    clean_axis(axes[1])
    fig.suptitle("Primary recommendation: " + SCENARIO_LABELS.get(primary, primary), fontsize=12.5)
    fig.supxlabel("Stranding uses fixed donors A100/A800/H800/Series-2; it bounds matching on the realized timeline, not the gain of a different schedule.", fontsize=8.4)
    save(fig, directory / "figures", "06_allocation_imbalance")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--suite", choices=("all", "question2", "question3"), default="all")
    args = parser.parse_args()
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False, "svg.fonttype": "none"})
    if args.suite in {"all", "question2"}:
        question2(args.root.resolve())
    if args.suite in {"all", "question3"}:
        question3(args.root.resolve())


if __name__ == "__main__":
    main()
