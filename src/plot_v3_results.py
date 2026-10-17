"""Final paper figures: fixed Q2 choice and dual Q3 management recommendations.

Read saved metrics only. Added Q2 curve points never enter selection, and old
figure files are preserved in their versioned directories.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from plot_optimization_results import (plt, ROOT, BLUE, ORANGE, TEAL, PURPLE,
                                       annotate_points, clean_axis, save)
from plot_v2_results import time_label, policy_label
from matplotlib.ticker import FuncFormatter

GOLD = "#D5AA35"
STRICT_COLOR = "#1D756A"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def question2(root: Path) -> None:
    directory = root / "outputs/question2"
    candidates = pd.read_csv(directory / "comparison_v2.csv").set_index("scenario")
    extra = pd.read_csv(directory / "v3_curve_points/comparison.csv")
    selection = read_json(directory / "selection_v2.json")
    chosen = candidates.loc[selection["selected_scenario"]]
    linear = candidates[candidates.policy == "aging"].sort_values("alpha")
    original_log = candidates[candidates.policy == "log_aging"]
    logarithmic = pd.concat([original_log, extra], ignore_index=True).sort_values("tau_seconds")
    bound = selection["c2_mean_bound_hours"]
    fig, axes = plt.subplots(1, 2, figsize=(12.2, 5.3), layout="constrained")
    for ax, field, title in [(axes[0], "wait_hours_max", "(a) Maximum wait"),
                             (axes[1], "wait_hours_p99", "(b) P99 wait")]:
        ax.axvspan(0, bound, color="#DCF1E9", alpha=.7, zorder=0)
        ax.axvline(bound, color=TEAL, lw=.9, ls=":")
        ax.plot(linear.wait_hours_mean, linear[field], "o-", color=TEAL, ms=4, lw=1.4, label="Linear aging")
        ax.plot(logarithmic.wait_hours_mean, logarithmic[field], "-", color=PURPLE, lw=1.4, label="Log aging")
        ax.scatter(original_log.wait_hours_mean, original_log[field], color=PURPLE, s=20, zorder=3)
        ax.scatter(extra.wait_hours_mean, extra[field], facecolors="white", edgecolors=PURPLE, s=36, lw=1.2,
                   label="Log curve-only additions", zorder=4)
        for name, label, marker, color in [("fcfs", "FCFS", "s", BLUE), ("sjf", "Strict SJF", "D", ORANGE),
                                          ("backfill", "Backfill 128/512", "^", "#555555")]:
            row = candidates.loc[name]
            ax.scatter(row.wait_hours_mean, row[field], marker=marker, color=color, s=65,
                       edgecolors="white", linewidths=.6, label=label, zorder=5)
        row = candidates.loc["backfill_l512"]
        ax.scatter(row.wait_hours_mean, row[field], marker="v", color="#888888", s=45, zorder=5)
        row = candidates.loc["alpha_100"]
        ax.scatter(row.wait_hours_mean, row[field], marker="o", s=145, facecolors="none", edgecolors=ORANGE,
                   linewidths=1.5, label=r"Strict-cap alternative $\alpha=100$", zorder=6)
        ax.scatter(chosen.wait_hours_mean, chosen[field], marker="*", s=220, color=GOLD, edgecolors="#333333",
                   linewidths=.6, label="Fixed Q2*: " + policy_label(chosen), zorder=7)
        ax.set_xscale("symlog", linthresh=20)
        ax.set_yscale("log")
        ax.set_xlim(0, candidates.wait_hours_mean.max()*1.24)
        ax.margins(y=.2)
        ax.set_xlabel("Mean wait (hours; symlog scale)")
        ax.set_ylabel("Waiting time (hours; log scale)")
        ax.set_title(title, loc="left", fontsize=11)
        ax.text(.025, .025, f"C2: mean ≤ {bound:.3f} h\nC1 group checks also required", transform=ax.transAxes,
                fontsize=8, va="bottom", bbox={"facecolor": "white", "alpha": .85, "edgecolor": "none", "pad": 3})
        clean_axis(ax)
    axes[0].legend(loc="upper right", fontsize=7.5, framealpha=.94)
    sjf = candidates.loc["sjf"]
    axes[0].annotate("α ≤ .3: near SJF here", (sjf.wait_hours_mean, sjf.wait_hours_max), xytext=(-30, 32),
                     textcoords="offset points", ha="center", fontsize=8,
                     arrowprops={"arrowstyle": "-", "color": "#999999", "lw": .7})
    fig.suptitle("Queue-policy trade-offs with a fixed Q2* decision", fontsize=13)
    fig.supxlabel("Linear labels below α=1 are omitted; α=1 remains separate. Open log points (T=10/14/21 d) refine the curve only, without reselection.", fontsize=8.2)
    visible_linear = linear[linear.alpha >= 1]
    for ax, field in [(axes[0], "wait_hours_max"), (axes[1], "wait_hours_p99")]:
        points = pd.concat([visible_linear, logarithmic])
        labels = [f"α={a:g}" for a in visible_linear.alpha] + ["T=" + time_label(t) for t in logarithmic.tau_seconds]
        annotate_points(ax, points.wait_hours_mean, points[field], labels, fontsize=7.3)
    save(fig, directory / "figures_v3", "04_policy_tradeoff")


def frontier(root: Path) -> None:
    directory = root / "outputs/question3/v3"
    table = pd.read_csv(directory / "comparison.csv").set_index("scenario")
    selection = read_json(directory / "selection.json")
    strict = selection["strict_selected_scenario"]
    compensated = selection["compensation_selected_scenario"]
    # Historical sensitivity cases (including the SJF bridge) have a different
    # selection scope and are excluded from this main-grid frontier.
    main = table[table.phase.isin(["baseline", "v2_main", "main"])]
    xfield, yfield = "a10_mean_wait_gain_hours", "max_donor_mean_wait_delta_hours"
    fig, ax = plt.subplots(figsize=(10.7, 6), layout="constrained")
    ax.scatter(main[xfield], main[yfield], color="#BBBBBB", s=22, zorder=1)
    families = [
        (["v2_ah_d1", "v2_ah_d7", "v3_ah_d14", "v3_ah_d30", "v3_ah_d60", "v2_ah"],
         ["1 d", "7 d", "14 d", "30 d", "60 d", "No limit"], TEAL, "o", "Runtime limit; minimum wait 0"),
        (["v3_ah_d7_t1h", "v3_ah_d14_t1h", "v3_ah_d30_t1h", "v3_ah_d60_t1h", "v3_ah_t1h"],
         ["7 d / 1 h", "14 d / 1 h", "30 d / 1 h", "60 d / 1 h", "No limit / 1 h"],
         BLUE, "s", "Runtime limit; minimum wait 1 h"),
        (["v3_ah_cap10", "v3_ah_cap15", "v2_ah"], ["cap .10", "cap .15", "cap .25"],
         PURPLE, "D", "Borrowing cap; unlimited runtime"),
    ]
    label_x, label_y, labels = [], [], []
    for names, point_labels, color, marker, legend_label in families:
        rows = main.loc[names]
        ax.plot(rows[xfield], rows[yfield], marker=marker, ms=5, lw=1.5, color=color, label=legend_label, zorder=3)
        for name, point_label in zip(names, point_labels):
            # The unrestricted AH endpoint belongs to two families; label once.
            if name == "v2_ah" and point_label.startswith("cap"):
                continue
            label_x.append(main.loc[name, xfield]); label_y.append(main.loc[name, yfield]); labels.append(point_label)
    hfirst = main.loc[["v3_ha_d30", "v3_ha"]]
    ax.scatter(hfirst[xfield], hfirst[yfield], marker="^", color=ORANGE, s=65, label="H800 first", zorder=4)
    for name, label in [("v3_ha_d30", "H-first: 30 d"), ("v3_ha", "H-first: no limit")]:
        label_x.append(main.loc[name, xfield]); label_y.append(main.loc[name, yfield]); labels.append(label)
    for name, color, label, size in [(strict, STRICT_COLOR, "Strict recommendation", 250),
                                     (compensated, GOLD, "Compensation option", 260)]:
        row = table.loc[name]
        if name == compensated == strict and color == GOLD:
            ax.scatter(row[xfield], row[yfield], marker="*", s=410, facecolors="none", edgecolors=GOLD,
                       linewidths=1.7, label=label + " (same configuration)", zorder=8)
            continue
        ax.scatter(row[xfield], row[yfield], marker="*", s=size, color=color, edgecolors="#222222",
                   linewidths=.7, label=label, zorder=7)
    ax.axhline(.5, color=STRICT_COLOR, ls="--", lw=1)
    ax.axhline(1., color="#9D7918", ls=":", lw=1.3)
    ax.set_yscale("symlog", linthresh=.05, linscale=.8)
    ax.set_yticks([0, .05, .1, .5, 1, 10, 100])
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
    ax.set_ylim(-.035, max(2., float(main[yfield].max())*2))
    ax.margins(x=.14)
    ax.set_xlabel("A10 mean-wait reduction (hours; Q2* baseline)")
    ax.set_ylabel("Largest donor native mean-wait increase (hours; symlog)")
    ax.set_title("Routing frontier under two declared management rules", loc="left", fontsize=13)
    clean_axis(ax)
    # Threshold annotations are not legend entries: keep the family/choice
    # legend at six items, while making clear that these lines are not sufficient.
    ax.text(.015, .5, "Strict mean bound: 0.5 h", transform=ax.get_yaxis_transform(), fontsize=8,
            va="bottom", color=STRICT_COLOR, bbox={"facecolor": "white", "alpha": .85, "edgecolor": "none", "pad": 2})
    ax.text(.015, 1., "Compensation mean bound: 1 h", transform=ax.get_yaxis_transform(), fontsize=8,
            va="bottom", color="#836414", bbox={"facecolor": "white", "alpha": .85, "edgecolor": "none", "pad": 2})
    ax.legend(loc="upper left", fontsize=8.2, framealpha=.94)
    fig.supxlabel("Strict also checks each donor's P95 ≤ +2 h. Compensation also checks exchange ≥100 and each donor's harmed share ≤20%.\nGray points: other main-grid configurations. Sensitivity cases do not select either star; both recommendations use named donors.", fontsize=8.2)
    annotate_points(ax, label_x, label_y, labels, fontsize=7.7)
    save(fig, directory / "figures", "05_routing_frontier")


def compensation(root: Path) -> None:
    directory = root / "outputs/question3/v3"
    table = pd.read_csv(directory / "compensation.csv").set_index("scenario")
    selection = read_json(directory / "selection.json")
    names = [selection["strict_selected_scenario"], selection["compensation_selected_scenario"]]
    selected = table.loc[names]
    fig, axes = plt.subplots(1, 3, figsize=(11.5, 4.5), layout="constrained")
    colors = [STRICT_COLOR, GOLD]
    for ax, field, multiplier, title, ylabel in [
        (axes[0], "a10_job_hours_saved", 1e-6, "(a) Net A10 waiting saved", "Million job-hours"),
        (axes[1], "donor_native_job_hours_added", 1e-3, "(b) Donor positive waiting added", "Thousand job-hours"),
        (axes[2], "compensation_share", 100, "(c) GPU-weighted credit share", "C / S (%)"),
    ]:
        values = selected[field].to_numpy(dtype=float)*multiplier
        finite = np.isfinite(values)
        bars = ax.bar(np.arange(2)[finite], values[finite], width=.6, color=np.array(colors)[finite])
        ax.bar_label(bars, labels=[f"{value:.3f}" for value in values[finite]], padding=4, fontsize=10)
        for index in np.flatnonzero(~finite):
            ax.text(index, 0, "Undefined\n(S ≤ 0)", ha="center", va="bottom", fontsize=9)
        ax.set_xticks([0, 1], ["Strict", "Compensation"])
        ax.set_xlim(-.6, 1.6)
        upper = max(0., float(np.max(values[finite]))) if finite.any() else 0.
        ax.set_ylim(0, upper*1.22 if upper > 0 else 1)
        ax.set_ylabel(ylabel)
        ax.set_title(title, loc="left", fontsize=10.8)
        clean_axis(ax)
    title = "Delay accounts for the strict and compensation recommendations"
    if names[0] == names[1]:
        title += " (same configuration)"
    fig.suptitle(title, fontsize=13)
    fig.supxlabel("Job-hour exchange is unweighted; C/S uses GPU-weighted waiting. Credits are a proposed accounting rule, not measured money or user utility.", fontsize=8.2)
    save(fig, directory / "figures", "07_compensation")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--suite", choices=("all", "question2", "question3"), default="all")
    args = parser.parse_args()
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False, "svg.fonttype": "none"})
    if args.suite in {"all", "question2"}:
        question2(args.root.resolve())
    if args.suite in {"all", "question3"}:
        frontier(args.root.resolve())
        compensation(args.root.resolve())


if __name__ == "__main__":
    main()
