"""Publication figures for the final paper draft (reports/paper_draft_v2_en.md).

Reads frozen experiment outputs only; writes PNG (300 dpi) and SVG files to
outputs/paper_figures/. Run from the repository root:

    python src/plot_paper_figures.py
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "2026_MCM_Senior_GPU_Scheduling/2026_MCM_Senior_GPU_Scheduling"
OUT = ROOT / "outputs"
DAY, HOUR = 86400.0, 3600.0

# Validated categorical slots (blue, orange, aqua) plus neutral ink.
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
BLUE_LIGHT, ORANGE_LIGHT, AQUA_LIGHT = "#cde2fb", "#fbd9c9", "#c9eedf"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, AXIS, SURFACE = "#e1e0d9", "#c3c2b7", "#fcfcfb"
NEUTRAL = "#a8a69f"
DONORS = ["A100-SXM4-80GB", "A800-SXM4-80GB", "H800", "GPU-series-2"]


def style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 8.5, "axes.titlesize": 9.5,
        "axes.titleweight": "bold", "axes.titlelocation": "left", "axes.titlepad": 8,
        "axes.labelsize": 8.5, "axes.labelcolor": INK2, "axes.edgecolor": AXIS,
        "axes.linewidth": 0.8, "axes.facecolor": SURFACE, "figure.facecolor": "white",
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
        "axes.axisbelow": True, "axes.spines.top": False, "axes.spines.right": False,
        "xtick.color": INK2, "ytick.color": INK2, "xtick.labelsize": 8, "ytick.labelsize": 8,
        "xtick.major.size": 0, "ytick.major.size": 0, "legend.frameon": False,
        "legend.fontsize": 8, "lines.linewidth": 2, "savefig.dpi": 300,
    })


def save(fig, name: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "svg"):
        fig.savefig(out / f"{name}.{ext}", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def panel_tag(ax, text: str) -> None:
    ax.set_title(text, color=INK)


# ---------------------------------------------------------------- Figure 1
def figure1(out: Path) -> None:
    band = pd.read_csv(OUT / "question1/tables/duration_band.csv")
    labels = ["≤ 1 min", "1–10 min", "10 min–1 h", "1 h–1 d", "1–7 d", "> 7 d"]
    jobs = band.job_share.to_numpy() * 100
    work = band.gpu_work_share.to_numpy() * 100

    raw = pd.read_csv(DATA / "job_info_clean.csv", dtype={"job_name": str})
    w = np.sort((raw.gpu_request * raw.worker_num * raw.duration).to_numpy())[::-1]
    x = np.arange(1, len(w) + 1) / len(w) * 100
    y = np.cumsum(w) / w.sum() * 100

    fig, (a, b) = plt.subplots(1, 2, figsize=(7.2, 2.9), gridspec_kw={"width_ratios": [1.15, 1]})
    pos = np.arange(len(labels))
    h = 0.38
    a.barh(pos + h / 2, jobs, h, color=BLUE, label="Share of jobs", edgecolor="white", linewidth=1)
    a.barh(pos - h / 2, work, h, color=ORANGE, label="Share of GPU work", edgecolor="white", linewidth=1)
    for i in range(len(labels)):
        if jobs[i] >= 5:
            a.text(jobs[i] + 1, pos[i] + h / 2, f"{jobs[i]:.0f}%", va="center", fontsize=7.5, color=INK2)
        if work[i] >= 5:
            a.text(work[i] + 1, pos[i] - h / 2, f"{work[i]:.0f}%", va="center", fontsize=7.5,
                   color=INK, fontweight="bold")
    a.set_yticks(pos, labels)
    a.set_xlim(0, 88)
    a.set_xlabel("Percent of total")
    a.grid(axis="y", visible=False)
    a.legend(loc="lower right")
    panel_tag(a, "(a) Jobs vs GPU work by runtime")

    b.plot(x, y, color=ORANGE, lw=2.2)
    b.fill_between(x, y, color=ORANGE_LIGHT, alpha=0.6, lw=0)
    offsets = {1: (-8, -4), 5: (-10, 2), 10: (-8, 9)}
    for top, share in [(1, 43.3), (5, 87.7), (10, 97.1)]:
        b.plot(top, share, "o", ms=6, mfc="white", mec=INK, mew=1.4, zorder=5)
        b.annotate(f"top {top}% → {share:.0f}% of work", (top, share), xytext=offsets[top],
                   textcoords="offset points", ha="right", va="center", fontsize=7.5, color=INK)
    b.text(0.012, 55, "Gini = 0.95", fontsize=8.5, color=INK, fontweight="bold")
    b.set_xscale("log")
    b.set_xlim(0.01, 100)
    b.set_ylim(0, 102)
    b.set_xticks([0.01, 0.1, 1, 10, 100], ["0.01%", "0.1%", "1%", "10%", "100%"])
    b.set_xlabel("Largest jobs by GPU work (cumulative share of jobs)")
    b.set_ylabel("Cumulative share of GPU work (%)")
    panel_tag(b, "(b) Concentration of GPU work")
    fig.tight_layout(w_pad=2.5)
    save(fig, "fig1_workload", out)


# ---------------------------------------------------------------- Figure 2
def figure2(out: Path) -> None:
    models = pd.read_csv(OUT / "question1/tables/model_summary.csv").sort_values("gpu_work_to_capacity_window")
    short = {"A100-SXM4-80GB": "A100", "A800-SXM4-80GB": "A800"}
    names = [short.get(m, m) for m in models.gpu_model]
    rho = models.gpu_work_to_capacity_window.to_numpy()
    daily = pd.read_csv(OUT / "question1/tables/offered_load_daily.csv")

    fig, (a, b) = plt.subplots(1, 2, figsize=(7.2, 2.8), gridspec_kw={"width_ratios": [1, 1.35]})
    colors = [ORANGE if r > 1 else BLUE for r in rho]
    a.barh(names, rho, color=colors, height=0.62, edgecolor="white")
    a.axvline(1, color=INK, lw=1)
    a.text(1.02, -0.75, "90-day capacity", fontsize=7.5, color=INK2, va="center")
    for i, r in enumerate(rho):
        if r > 1:
            a.text(r + 0.04, i, f"{r:.2f}", va="center", fontsize=7.5, color=INK, fontweight="bold")
        else:  # inside the bar end, clear of the capacity line
            a.text(r - 0.04, i, f"{r:.2f}", va="center", ha="right", fontsize=7.5, color="white",
                   fontweight="bold")
    a.set_xlim(0, 2.45)
    a.set_ylim(-1.1, len(names) - 0.5)
    a.set_xlabel("Requested GPU work ÷ 90-day GPU supply")
    a.grid(axis="y", visible=False)
    panel_tag(a, "(a) Workload-to-capacity ratio")

    highlight = {"A10": ORANGE, "A100-SXM4-80GB": BLUE, "GPU-series-1": AQUA}
    for model, group in daily.groupby("gpu_model"):
        g = group[group.day_index < 90]
        if model in highlight:
            continue
        b.plot(g.day_index, g.mean_gpu_demand_to_capacity, color=NEUTRAL, lw=0.9, alpha=0.8)
    for model, color in highlight.items():
        g = daily[(daily.gpu_model == model) & (daily.day_index < 90)]
        b.plot(g.day_index, g.mean_gpu_demand_to_capacity, color=color, lw=2)
        if model == "A10":
            b.fill_between(g.day_index, 1, g.mean_gpu_demand_to_capacity,
                           where=g.mean_gpu_demand_to_capacity > 1, color=ORANGE_LIGHT, lw=0)
        end = g.iloc[-1]
        b.text(91, end.mean_gpu_demand_to_capacity, short.get(model, model), color=INK,
               fontsize=7.5, va="center", fontweight="bold")
    b.text(91, 0.42, "other\nmodels", color=MUTED, fontsize=7, va="center")
    b.axhline(1, color=INK, lw=1)
    b.set_xlim(0, 104)
    b.set_ylim(0, 1.75)
    b.set_xlabel("Day")
    b.set_ylabel("Demand ÷ capacity")
    panel_tag(b, "(b) Daily GPU demand if every job started on arrival")
    fig.tight_layout(w_pad=2.5)
    save(fig, "fig2_supply_demand", out)


# ---------------------------------------------------------------- Figure 3
def figure3(out: Path) -> None:
    q = pd.read_csv(OUT / "question2/comparison_v2.csv")
    extra = pd.read_csv(OUT / "question2/v3_curve_points/comparison.csv")
    lin = q[q.scenario.str.startswith("alpha_")].copy()
    lin["alpha"] = lin.scenario.str.slice(6).astype(float)
    lin = lin.sort_values("alpha")
    log = pd.concat([q[q.scenario.str.startswith("tau_")], extra])
    log["T"] = log.scenario.str.slice(4).astype(float) / DAY
    log = log.sort_values("T")
    row = q.set_index("scenario")
    bound = 2 * row.loc["sjf", "wait_hours_mean"]

    fig, (a, b) = plt.subplots(1, 2, figsize=(7.2, 3.1), gridspec_kw={"width_ratios": [1.25, 1]})
    a.axvspan(5, bound, color=AQUA_LIGHT, alpha=0.55, lw=0)
    a.text(bound * 0.96, 1330, "mean ≤ 2 × SJF", ha="right", fontsize=7.5, color="#127a55")
    a.plot(lin.wait_hours_mean, lin.wait_hours_max, "-o", color=BLUE, ms=4, label="Linear aging  $d_j+\\alpha a_j$")
    a.plot(log.wait_hours_mean, log.wait_hours_max, "-o", color=ORANGE, ms=4, label="Log aging  $\\ln d_j+a_j/T$")
    for alpha, off in ((1, (5, 4)), (3, (5, 4)), (10, (-6, 6))):
        r = lin[lin.alpha == alpha].iloc[0]
        a.annotate(f"α={alpha:g}", (r.wait_hours_mean, r.wait_hours_max), xytext=off,
                   ha="left" if alpha < 10 else "right",
                   textcoords="offset points", fontsize=7, color=BLUE)
    for T, off in ((30, (6, 2)), (3, (-8, -14))):
        r = log[np.isclose(log["T"], T)].iloc[0]
        a.annotate(f"T={T:g} d", (r.wait_hours_mean, r.wait_hours_max), xytext=off,
                   textcoords="offset points", fontsize=7, color="#b8461b")
    for name, label, off in (("fcfs", "FCFS / backfill", (6, -14)), ("sjf", "SJF", (0, 8))):
        r = row.loc[name]
        a.plot(r.wait_hours_mean, r.wait_hours_max, "s" if name != "sjf" else "D", ms=6,
               color=AQUA if name == "sjf" else MUTED, mec="white", mew=1, zorder=5)
        a.annotate(label, (r.wait_hours_mean, r.wait_hours_max), xytext=off, textcoords="offset points",
                   fontsize=7.5, color=INK, ha="right" if name != "sjf" else "center")
    star = row.loc["tau_604800"]
    a.plot(star.wait_hours_mean, star.wait_hours_max, "*", ms=17, color=ORANGE, mec=INK, mew=0.9, zorder=6)
    a.annotate("Q2*  (T = 7 d)\nmean 31 h, max 1,515 h", (star.wait_hours_mean, star.wait_hours_max),
               xytext=(26, 34), textcoords="offset points", fontsize=7.5, color=INK, fontweight="bold",
               arrowprops={"arrowstyle": "-", "color": INK2, "lw": 0.8})
    a.set_xscale("log")
    a.set_xlim(12, 700)
    a.set_ylim(1240, 3300)
    a.set_xticks([20, 50, 100, 200, 500], ["20", "50", "100", "200", "500"])
    a.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    a.set_xlabel("Mean wait (h, log scale)")
    a.set_ylabel("Maximum wait (h)")
    a.legend(loc="upper right", handlelength=1.6)
    panel_tag(a, "(a) Average vs worst-case waiting")

    groups = ["<=1h", "1h-1d", "1d-7d", ">7d"]
    glabels = ["≤ 1 h", "1 h–1 d", "1–7 d", "> 7 d"]
    series = [("FCFS", "question2/fcfs", MUTED), ("SJF", "question2/sjf", AQUA), ("Q2*", "question2/q2_star", ORANGE)]
    width = 0.26
    for k, (label, path, color) in enumerate(series):
        g = pd.read_csv(OUT / path / "metrics_by_job_group.csv").set_index("group").loc[groups]
        xs = np.arange(len(groups)) + (k - 1) * width
        b.bar(xs, g.wait_hours_mean.clip(lower=0.01), width, color=color, label=label, edgecolor="white", lw=1)
    b.set_yscale("log")
    b.set_ylim(0.01, 2000)
    b.set_xticks(range(len(groups)), glabels)
    b.set_xlabel("Job runtime class")
    b.set_ylabel("Mean wait (h, log scale)")
    b.grid(axis="x", visible=False)
    b.legend(loc="upper left", ncol=3, columnspacing=1)
    panel_tag(b, "(b) Who waits: mean wait by runtime class")
    fig.tight_layout(w_pad=2)
    save(fig, "fig3_queue_policies", out)


# ---------------------------------------------------------------- Figure 4
def figure4(out: Path) -> None:
    dev = pd.read_csv(OUT / "v4_validation/development.csv")
    order = ["d30", "d45", "d60", "d75", "d90", "unlimited_cap25"]
    labels = ["30 d", "45 d", "60 d", "75 d", "90 d", "none"]

    def stats(prefix_tau: str, col: str):
        names = [f"{o}_{prefix_tau}" if not o.startswith("unlimited") else o for o in order]
        g = dev.groupby("configuration")[col]
        return g.mean().reindex(names).to_numpy(), g.min().reindex(names).to_numpy(), g.max().reindex(names).to_numpy()

    x = np.arange(len(order))
    fig, (a, b) = plt.subplots(1, 2, figsize=(7.2, 2.9))
    for ax in (a, b):
        ax.axvspan(2.6, 3.4, color=BLUE_LIGHT, alpha=0.7, lw=0)
    m, lo, hi = stats("t0h", "wait_hours_mean")
    a.plot(x, m, "-o", color=BLUE, ms=5)
    a.fill_between(x, lo, hi, color=BLUE, alpha=0.15, lw=0)
    a.axhline(31.19, color=MUTED, lw=1, ls=(0, (3, 2)))
    a.text(5.3, 31.19 - 2.6, "no routing: 31.2 h", ha="right", fontsize=7.5, color=INK2)
    for i in (0, 3, 5):
        a.annotate(f"{m[i]:.1f} h", (x[i], m[i]), xytext=(0, 7), textcoords="offset points",
                   ha="center", fontsize=7.5, color=INK, fontweight="bold" if i == 3 else "normal")
    a.text(3, 33.5, "chosen", ha="center", color=BLUE, fontsize=7.5, fontweight="bold")
    a.set_xticks(x, labels)
    a.set_ylim(0, 36)
    a.set_xlabel("Runtime limit for routed jobs")
    a.set_ylabel("Cluster mean wait (h)")
    panel_tag(a, "(a) Benefit: cluster mean wait")

    pm, plo, phi = stats("t0h", "max_donor_p95_delta_hours")
    mm, mlo, mhi = stats("t0h", "max_donor_mean_wait_delta_hours")
    b.plot(x, pm, "-o", color=ORANGE, ms=5, label="Worst donor ΔP95 wait")
    b.fill_between(x, plo, phi, color=ORANGE, alpha=0.15, lw=0)
    b.plot(x, mm, "-o", color=AQUA, ms=5, label="Worst donor Δmean wait")
    b.fill_between(x, mlo, mhi, color=AQUA, alpha=0.18, lw=0)
    b.axhline(2, color=ORANGE, lw=1, ls=(0, (3, 2)))
    b.axhline(0.5, color=AQUA, lw=1, ls=(0, (3, 2)))
    b.text(-0.3, 2.15, "P95 limit 2 h", fontsize=7.5, color="#b8461b")
    b.text(-0.3, 0.6, "mean limit 0.5 h", fontsize=7.5, color="#127a55")
    for i in (4, 5):
        b.plot(x[i], pm[i], "x", ms=9, mew=2, color=INK, zorder=6)
    b.text(5, pm[5] - 0.75, "violates", ha="center", fontsize=7, color=INK)
    b.text(3, 7.4, "chosen", ha="center", color=BLUE, fontsize=7.5, fontweight="bold")
    b.set_xticks(x, labels)
    b.set_ylim(-0.2, 8)
    b.set_xlabel("Runtime limit for routed jobs")
    b.set_ylabel("Increase in donor-native wait (h)")
    b.legend(loc="upper left", bbox_to_anchor=(0, 0.92))
    panel_tag(b, "(b) Cost: donor users' extra waiting")
    fig.tight_layout(w_pad=2.5)
    save(fig, "fig5_runtime_limit", out)


# ---------------------------------------------------------------- Figure 5
def figure5(out: Path) -> None:
    v = pd.read_csv(OUT / "v6_source_protection/validation.csv")
    cases = [("current", 1.0, "v=1", BLUE, True), ("current", 0.8, "v=0.8", BLUE, False),
             ("loans512", 1.0, "v=1", AQUA, True), ("loans512", 0.8, "v=0.8", AQUA, False)]
    panels = [("wait_hours_mean", "Cluster mean wait (h)", "(a) Efficiency", 1),
              ("max_donor_p95_delta_hours", "Worst donor ΔP95 wait (h)", "(b) Donor protection", 1),
              ("a10_harmed_fraction", "A10 jobs starting later (%)", "(c) Fairness inside A10", 100)]
    rng = np.random.default_rng(7)
    fig, axes = plt.subplots(1, 3, figsize=(7.4, 2.9))
    for ax, (col, ylabel, title, scale) in zip(axes, panels):
        for i, (conf, speed, label, color, filled) in enumerate(cases):
            vals = v[(v.configuration == conf) & np.isclose(v.speedup, speed)][col].to_numpy() * scale
            jitter = rng.uniform(-0.17, 0.17, len(vals))
            ax.scatter(i + jitter, vals, s=26, color=color if filled else "white", edgecolor=color,
                       linewidth=1.4, zorder=4)
            ax.hlines(vals.mean(), i - 0.3, i + 0.3, color=INK, lw=1.6, zorder=5)
        ax.set_xticks(range(len(cases)), [c[2] for c in cases], fontsize=7.5)
        for center, name, color in ((0.5, "Routing", BLUE), (2.5, "Protected", "#127a55")):
            ax.text(center, -0.13, name, transform=ax.get_xaxis_transform(), ha="center", va="top",
                    fontsize=8, color=color, fontweight="bold")
        ax.set_xlim(-0.6, 3.6)
        ax.grid(axis="x", visible=False)
        ax.set_ylabel(ylabel)
        panel_tag(ax, title)
    axes[0].axhline(31.19, color=MUTED, lw=1, ls=(0, (3, 2)))
    axes[0].text(3.55, 29.3, "no routing 31.2 h", ha="right", fontsize=7, color=INK2)
    axes[0].set_ylim(0, 34)
    axes[1].axhline(2, color=ORANGE, lw=1, ls=(0, (3, 2)))
    axes[1].text(3.55, 2.08, "limit 2 h", ha="right", fontsize=7, color="#b8461b")
    axes[1].set_ylim(-0.1, 2.4)
    axes[2].set_ylim(-1.5, 34)
    for i in (2, 3):
        axes[2].text(i, 2.0, "0%", ha="center", fontsize=8, color=INK, fontweight="bold")
    handles = [Line2D([], [], marker="o", ls="", color=BLUE, ms=6, label="Routing (75/30-day limits)"),
               Line2D([], [], marker="o", ls="", color=AQUA, ms=6, label="Protected mode"),
               Line2D([], [], marker="o", ls="", mfc="white", mec=INK2, ms=6, label="slower migration (v = 0.8)"),
               Line2D([], [], color=INK, lw=1.6, label="mean of 10 held-out samples")]
    fig.legend(handles=handles, loc="lower center", ncol=4, bbox_to_anchor=(0.5, -0.06), fontsize=7.5)
    fig.tight_layout(rect=(0, 0.07, 1, 1), w_pad=1.6)
    save(fig, "fig6_validation", out)


# ---------------------------------------------------------------- Figure 6
def timeline(schedule: Path, capacity: pd.Series, horizon_days: int = 150):
    s = pd.read_csv(schedule, dtype={"job_name": str},
                    usecols=["requested_model", "assigned_model", "submit_time", "start_time", "end_time",
                             "gpu_request", "worker_num"])
    s["g"] = s.gpu_request * s.worker_num
    grid = np.arange(0, horizon_days * DAY + 1, HOUR)

    def step(starts, ends, weights):
        ev = np.zeros(len(grid) + 1)
        np.add.at(ev, np.searchsorted(grid, starts, side="right") - 1, weights)
        np.add.at(ev, np.searchsorted(grid, ends, side="right") - 1, -weights)
        return np.cumsum(ev)[: len(grid)]

    busy = s[s.assigned_model.isin(DONORS)]
    idle = capacity[DONORS].sum() - step(busy.start_time.to_numpy(), busy.end_time.to_numpy(), busy.g.to_numpy())
    a10 = s[s.requested_model == "A10"]
    queued = step(a10.submit_time.to_numpy(), a10.start_time.to_numpy(), a10.g.to_numpy())
    days = grid / DAY
    frame = pd.DataFrame({"day": np.floor(days), "idle": idle, "queued": queued})
    return frame.groupby("day").mean().reset_index()


def figure6(out: Path) -> None:
    nodes = pd.read_csv(DATA / "node_info_df.csv", dtype={"node_name": str})
    cap = nodes.groupby("gpu_model").gpu_capacity_num.sum()
    base = timeline(OUT / "question2/q2_star/schedule.csv", cap)
    routed = timeline(OUT / "v6_source_protection/cases/validation_current_s20261033_v1/schedule.csv", cap)
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.8), sharey=True)
    top = 1.05 * max(base.queued.max(), base.idle.max(), routed.queued.max(), routed.idle.max())
    for ax, frame, title in ((axes[0], base, "(a) Log aging, no routing"),
                             (axes[1], routed, "(b) With pull routing (75/30-day limits)")):
        stranded = np.minimum(frame.idle, frame.queued)
        ax.fill_between(frame.day, 0, frame.idle, color=BLUE_LIGHT, lw=0, step="post")
        ax.fill_between(frame.day, 0, stranded, color=BLUE, alpha=0.55, lw=0, step="post")
        ax.plot(frame.day, frame.queued, color=ORANGE, lw=1.6, drawstyle="steps-post")
        ax.axvline(90, color=MUTED, lw=0.8, ls=(0, (3, 2)))
        ax.text(91, top * 0.93, "day 90", fontsize=7, color=INK2)
        ax.set_xlim(0, 145)
        ax.set_ylim(0, top)
        ax.set_xlabel("Day")
        panel_tag(ax, title)
    axes[0].set_ylabel("GPUs")
    axes[1].text(148, top * 0.55, "stranded capacity, 90 days\n3.68 → 0.74 M GPU·h\n(mean of 10 samples)", ha="right",
                 fontsize=7.5, color=INK, fontweight="bold")
    handles = [Patch(color=BLUE_LIGHT, label="Idle GPUs on A100, A800, H800, GPU-series-2"),
               Patch(color=BLUE, alpha=0.55, label="Stranded: idle while A10 jobs wait"),
               Line2D([], [], color=ORANGE, lw=1.6, label="GPUs requested by waiting A10 jobs")]
    fig.legend(handles=handles, loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.05), fontsize=7.5)
    fig.tight_layout(rect=(0, 0.07, 1, 1), w_pad=1.5)
    save(fig, "fig4_stranded_capacity", out)


# ---------------------------------------------------------------- Overview diagram
def figure_overview(out: Path) -> None:
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

    fig, ax = plt.subplots(figsize=(7.2, 2.9))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 40)
    ax.axis("off")

    def box(x, y, w, h, text, face, edge, weight="normal", size=7.4, ls="-"):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.3,rounding_size=1.0",
                                    facecolor=face, edgecolor=edge, linewidth=1.2, linestyle=ls))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=size,
                color=INK, fontweight=weight, linespacing=1.35)

    def arrow(start, end, color=INK2, lw=1.1, rad=0.0, ls="-"):
        ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=9, color=color,
                                     lw=lw, connectionstyle=f"arc3,rad={rad}", linestyle=ls))

    def title(x, text, sub=None):
        ax.text(x, 38.3, text, ha="center", fontsize=8.2, fontweight="bold", color=INK)
        if sub:
            ax.text(x, 35.4, sub, ha="center", fontsize=7.2, color=INK2)

    title(26, "Question 2: queue order", r"log aging Q2*, key $\ln d_j + a_j/T$")
    title(52, "Placement", "node-level best fit")
    title(84.5, "Question 3: rebalancing")
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    box(0.8, 14, 10.5, 10, "Job\narrivals\n224,112", "white", AXIS)
    rows = [(25, "A10 queue", ORANGE_LIGHT, ORANGE), (14.5, "A100/H800 queues", BLUE_LIGHT, BLUE),
            (4, "Other queues", "#ecebe6", NEUTRAL)]
    pools = [(25, "A10: 2,494 GPUs\nρ = 2.06", ORANGE_LIGHT, ORANGE),
             (14.5, "A100: 3,456 GPUs\nH800: 1,752 GPUs", BLUE_LIGHT, BLUE),
             (4, "A800, GPU-series-1/2", "#ecebe6", NEUTRAL)]
    for (y, label, face, edge), (_, plabel, pface, pedge) in zip(rows, pools):
        box(17, y, 18, 7, label, face, edge, weight="bold")
        box(42, y, 20, 7, plabel, pface, pedge)
        arrow((11.8, 19), (16.6, y + 3.5))
        arrow((35.6, y + 3.5), (41.4, y + 3.5))
    arrow((35.6, 26.0), (41.6, 20.0), color=BLUE, lw=2.2, rad=-0.45)
    ax.text(35.8, 20.6, "pull", fontsize=7.5, color=BLUE, fontweight="bold", ha="left")
    box(69, 14, 30, 17,
        "Pull routing\n" "only when donor queue is empty\n" "borrow ≤ 25% of GPUs and CPUs\n"
        "runtime ≤ 75 d on A100, ≤ 30 d on H800\n" "eligible share p = 0.5", "white", BLUE, size=7.0)
    arrow((68.4, 18.0), (62.6, 18.0), color=BLUE, ls="--")
    box(69, 2.5, 30, 8, "Protected mode (optional)\nno A10 job starts later than under Q2*",
        "white", AQUA, size=7.0, ls="--")
    arrow((68.4, 9.0), (62.6, 26.5), color="#127a55", rad=0.3, ls="--")
    save(fig, "fig_overview", out)


# ---------------------------------------------------------------- Waiting-time tails
def figure_tails(out: Path) -> None:
    runs = [("FCFS", "question2/fcfs", MUTED, "-"),
            ("SJF", "question2/sjf", AQUA, "-"),
            ("Q2* (log aging)", "question2/q2_star", ORANGE, "-"),
            ("Q2* + routing", "v6_source_protection/cases/validation_current_s20261033_v1", BLUE, "-"),
            ("Q2* + protected routing", "v6_source_protection/cases/validation_loans512_s20261033_v1", BLUE, (0, (4, 2)))]
    xs = np.logspace(-2, np.log10(4000), 400)
    fig, (a, b) = plt.subplots(1, 2, figsize=(7.2, 2.75), sharey=True)
    for ax, subset, title in ((a, None, "(a) All jobs"), (b, "A10", "(b) A10 jobs")):
        for label, path, color, ls in runs:
            s = pd.read_csv(OUT / path / "schedule.csv", usecols=["requested_model", "wait_time"])
            if subset:
                s = s[s.requested_model == subset]
            w = np.sort(s.wait_time.to_numpy() / HOUR)
            frac = 1 - np.searchsorted(w, xs, side="right") / len(w)
            ax.plot(xs, np.maximum(frac, 1e-6) * 100, color=color, ls=ls, lw=1.8, label=label)
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlim(0.01, 4000)
        ax.set_ylim(0.004, 100)
        ax.set_xticks([0.01, 0.1, 1, 10, 100, 1000], ["0.01", "0.1", "1", "10", "100", "1000"])
        ax.set_yticks([0.01, 0.1, 1, 10, 100], ["0.01%", "0.1%", "1%", "10%", "100%"])
        ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
        ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
        ax.set_xlabel("Waiting time x (h, log scale)")
        panel_tag(ax, title)
    a.set_ylabel("Share of jobs waiting longer than x")
    a.legend(loc="lower left", fontsize=7.3, handlelength=2.2)
    fig.tight_layout(w_pad=1.5)
    save(fig, "fig_wait_tails", out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT / "paper_figures")
    parser.add_argument("--only", nargs="*", type=int)
    args = parser.parse_args()
    style()
    # Keys follow the paper's figure numbers.
    figures = {1: figure1, 2: figure2, 3: figure3, 4: figure6, 5: figure4, 6: figure5,
               7: figure_overview, 8: figure_tails}
    for number, func in figures.items():
        if not args.only or number in args.only:
            func(args.out)
            print(f"figure {number} written")


if __name__ == "__main__":
    main()
