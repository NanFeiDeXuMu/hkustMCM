"""Reproduce Problem B / Question 1 without modifying the supplied data.

All workload measures use per-worker requests times worker count. The offered
load diagnostic assumes immediate starts and is NOT a scheduling simulation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "2026_MCM_Senior_GPU_Scheduling/2026_MCM_Senior_GPU_Scheduling"
DAY = 86400.0
MODELS = ["A10", "A100-SXM4-80GB", "A800-SXM4-80GB", "GPU-series-1", "GPU-series-2", "H800"]


def dump_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def validate_inputs(jobs: pd.DataFrame, nodes: pd.DataFrame) -> dict:
    job_cols = {"job_name", "gpu_model", "cpu_request", "gpu_request", "worker_num", "submit_time", "duration"}
    node_cols = {"node_name", "gpu_model", "gpu_capacity_num", "cpu_num"}
    if set(jobs.columns) != job_cols or set(nodes.columns) != node_cols:
        raise ValueError("Unexpected input schema")
    if jobs.empty or nodes.empty:
        raise ValueError("Empty input")
    checks = {
        "missing_job_cells": int(jobs.isna().sum().sum()),
        "missing_node_cells": int(nodes.isna().sum().sum()),
        "duplicate_job_ids": int(jobs.job_name.duplicated().sum()),
        "duplicate_node_ids": int(nodes.node_name.duplicated().sum()),
        "duplicate_job_rows": int(jobs.duplicated().sum()),
        "duplicate_node_rows": int(nodes.duplicated().sum()),
        "unknown_gpu_model_jobs": int((~jobs.gpu_model.isin(nodes.gpu_model)).sum()),
    }
    numeric_jobs = ["cpu_request", "gpu_request", "worker_num", "submit_time", "duration"]
    numeric_nodes = ["gpu_capacity_num", "cpu_num"]
    checks["nonfinite_job_rows"] = int((~np.isfinite(jobs[numeric_jobs]).all(axis=1)).sum())
    checks["nonfinite_node_rows"] = int((~np.isfinite(nodes[numeric_nodes]).all(axis=1)).sum())
    checks["invalid_job_domains"] = int((
        (jobs.cpu_request < 0) | (jobs.submit_time < 0) | (jobs.duration <= 0)
        | (jobs.gpu_request <= 0) | (jobs.worker_num <= 0)
        | (jobs.gpu_request % 1 != 0) | (jobs.worker_num % 1 != 0)
    ).sum())
    checks["invalid_node_domains"] = int((
        (nodes.gpu_capacity_num <= 0) | (nodes.cpu_num <= 0)
        | (nodes.gpu_capacity_num % 1 != 0)
    ).sum())
    if any(checks.values()):
        raise ValueError(f"Input checks failed: {checks}")
    checks.update({
        "zero_cpu_request_jobs": int((jobs.cpu_request == 0).sum()),
        "fractional_cpu_request_jobs": int((jobs.cpu_request % 1 != 0).sum()),
        "submit_times_are_sorted": bool(jobs.submit_time.is_monotonic_increasing),
    })
    return checks


def empty_cluster_slots(jobs: pd.DataFrame, nodes: pd.DataFrame) -> np.ndarray:
    """Exact empty-cluster feasibility under the statement's identical-worker model."""
    slots = np.zeros(len(jobs), dtype=np.int64)
    for model, group in jobs.groupby("gpu_model", sort=False):
        positions = np.flatnonzero(jobs.gpu_model.to_numpy() == model)
        gpu = group.gpu_request.to_numpy(dtype=float)
        cpu = group.cpu_request.to_numpy(dtype=float)
        pool = nodes[nodes.gpu_model == model]
        for (gcap, ccap), count in pool.groupby(["gpu_capacity_num", "cpu_num"]).size().items():
            cpu_slots = np.full(len(group), np.inf)
            np.divide(ccap, cpu, out=cpu_slots, where=cpu > 0)
            capacity = np.minimum(np.floor(gcap / gpu + 1e-10), np.floor(cpu_slots + 1e-10))
            slots[positions] += count * capacity.astype(np.int64)
    return slots


def gini(values: np.ndarray) -> float:
    values = np.sort(np.asarray(values, dtype=float))
    if not len(values) or values.sum() <= 0:
        return 0.0
    ranks = np.arange(1, len(values) + 1, dtype=float)
    return float(2 * np.dot(ranks, values) / (len(values) * values.sum()) - (len(values) + 1) / len(values))


def concentration(jobs: pd.DataFrame) -> pd.DataFrame:
    records = []
    for model, frame in [("ALL", jobs), *list(jobs.groupby("gpu_model"))]:
        work = np.sort(frame.gpu_work_seconds.to_numpy())[::-1]
        for fraction in [0.001, 0.01, 0.05, 0.10]:
            count = math.ceil(fraction * len(work))
            records.append({"gpu_model": model, "top_fraction": fraction, "jobs_selected": count,
                            "actual_job_fraction": count / len(work),
                            "gpu_work_share": float(work[:count].sum() / work.sum())})
    return pd.DataFrame(records)


def offered_load(group: pd.DataFrame, gpu_capacity: float, cpu_capacity: float, days: int):
    """Integrate immediate-start resource requests exactly using event intervals.

    Jobs occupy [submit_time, submit_time + duration). No capacity is enforced.
    Includes all completions, but overload statistics refer only to [0, days*DAY).
    """
    horizon = days * DAY
    starts = group.submit_time.to_numpy(float)
    ends = starts + group.duration.to_numpy(float)
    times, inverse = np.unique(np.r_[starts, ends, 0.0, horizon], return_inverse=True)
    daily = {"day_index": np.arange(days)}
    summary = {}
    for resource, capacity in [("gpu", gpu_capacity), ("cpu", cpu_capacity)]:
        requests = group[f"job_{resource}"].to_numpy(float)
        changes = np.bincount(inverse, weights=np.r_[requests, -requests, 0.0, 0.0], minlength=len(times))
        loads = np.cumsum(changes)
        intervals = np.diff(times)
        area = np.r_[0.0, np.cumsum(loads[:-1] * intervals)]
        boundaries = np.arange(days + 1) * DAY
        ix = np.searchsorted(times, boundaries, side="right") - 1
        integrals = area[ix] + loads[ix] * (boundaries - times[ix])
        daily[f"{resource}_hours"] = np.diff(integrals) / 3600
        daily[f"mean_{resource}_demand_to_capacity"] = np.diff(integrals) / DAY / capacity
        mask = times[:-1] < horizon
        durations = np.maximum(0, np.minimum(times[1:], horizon) - times[:-1])
        expected_work = float(np.sum(requests * group.duration.to_numpy(float)))
        if not np.isclose(area[-1], expected_work, rtol=1e-10, atol=1e-4):
            raise AssertionError("Offered-load integral does not reconcile to full work")
        if not np.isclose(loads[-1], 0, atol=1e-6) or np.min(loads) < -1e-6:
            raise AssertionError("Invalid event accounting")
        summary[f"{resource}_work_in_window_hours"] = float(integrals[-1] / 3600)
        summary[f"{resource}_work_after_window_hours"] = float((expected_work - integrals[-1]) / 3600)
        summary[f"peak_{resource}_demand_to_capacity"] = float(loads[:-1][mask].max() / capacity)
        summary[f"fraction_window_{resource}_over_capacity"] = float(durations[loads[:-1] > capacity + 1e-7].sum() / horizon)
    summary["jobs_finishing_after_window_if_immediate"] = int((ends > horizon).sum())
    summary["latest_immediate_completion_day"] = float(ends.max() / DAY)
    return pd.DataFrame(daily), summary


def build_tables(jobs: pd.DataFrame, nodes: pd.DataFrame, days: int):
    jobs = jobs.copy()
    jobs["job_gpu"] = jobs.gpu_request * jobs.worker_num
    jobs["job_cpu"] = jobs.cpu_request * jobs.worker_num
    jobs["gpu_work_seconds"] = jobs.job_gpu * jobs.duration
    jobs["cpu_work_seconds"] = jobs.job_cpu * jobs.duration
    jobs["gpu_hours"] = jobs.gpu_work_seconds / 3600
    jobs["cpu_hours"] = jobs.cpu_work_seconds / 3600
    jobs["day_index"] = np.floor(jobs.submit_time / DAY).astype(int)
    jobs["empty_cluster_worker_slots"] = empty_cluster_slots(jobs, nodes)
    summary = nodes.groupby("gpu_model").agg(nodes=("node_name", "size"), gpus=("gpu_capacity_num", "sum"), vcpus=("cpu_num", "sum"))
    aggregate = jobs.groupby("gpu_model").agg(
        jobs=("job_name", "size"), gpu_hours=("gpu_hours", "sum"), cpu_hours=("cpu_hours", "sum"),
        duration_median_seconds=("duration", "median"), duration_mean_seconds=("duration", "mean"),
        duration_p95_seconds=("duration", lambda x: x.quantile(.95)),
        job_gpu_median=("job_gpu", "median"), job_gpu_max=("job_gpu", "max"),
        multiworker_share=("worker_num", lambda x: (x > 1).mean()),
        infeasible_jobs=("empty_cluster_worker_slots", lambda x: int((x < jobs.loc[x.index, "worker_num"]).sum())),
    )
    order = [m for m in MODELS if m in summary.index] + sorted(set(summary.index) - set(MODELS))
    summary = summary.join(aggregate).reindex(order)
    if summary.isna().any().any():
        raise ValueError("A node pool has no jobs; handle explicitly before interpreting its statistics")
    summary["job_share"] = summary.jobs / len(jobs)
    summary["gpu_capacity_share"] = summary.gpus / summary.gpus.sum()
    summary["gpu_work_share"] = summary.gpu_hours / jobs.gpu_hours.sum()
    summary["gpu_work_to_capacity_window"] = summary.gpu_hours / (summary.gpus * days * 24)
    summary["cpu_work_to_capacity_window"] = summary.cpu_hours / (summary.vcpus * days * 24)
    summary["gpu_capacity_days_required"] = summary.gpu_hours / summary.gpus / 24
    summary["cpu_to_gpu_requested_work_ratio"] = summary.cpu_hours / summary.gpu_hours
    summary["cpu_to_gpu_capacity_ratio"] = summary.vcpus / summary.gpus
    summary["gpu_work_to_capacity_exact_span"] = summary.gpu_hours / (summary.gpus * (jobs.submit_time.max() - jobs.submit_time.min()) / 3600)

    daily = jobs.groupby("day_index").agg(jobs=("job_name", "size"), gpu_hours=("gpu_hours", "sum"), cpu_hours=("cpu_hours", "sum"))
    daily = daily.reindex(range(days), fill_value=0).reset_index()
    daily_model = jobs.groupby(["day_index", "gpu_model"]).agg(jobs=("job_name", "size"), gpu_hours=("gpu_hours", "sum"), cpu_hours=("cpu_hours", "sum"))
    daily_model = daily_model.reindex(pd.MultiIndex.from_product([range(days), summary.index], names=["day_index", "gpu_model"]), fill_value=0).reset_index()
    daily["week_index"] = daily.day_index // 7
    weekly = daily.groupby("week_index").agg(days=("day_index", "size"), jobs=("jobs", "sum"), gpu_hours=("gpu_hours", "sum"), cpu_hours=("cpu_hours", "sum"))
    weekly["jobs_per_day"] = weekly.jobs / weekly.days
    weekly["gpu_hours_per_day"] = weekly.gpu_hours / weekly.days

    offered, load_rows = [], []
    for model, row in summary.iterrows():
        series, diagnostics = offered_load(jobs[jobs.gpu_model == model], row.gpus, row.vcpus, days)
        series.insert(0, "gpu_model", model)
        offered.append(series)
        load_rows.append({"gpu_model": model, **diagnostics})
    load_summary = pd.DataFrame(load_rows).set_index("gpu_model")
    summary = summary.join(load_summary)
    for resource, capacity in [("gpu", "gpus"), ("cpu", "vcpus")]:
        summary[f"{resource}_immediate_window_mean_to_capacity"] = summary[f"{resource}_work_in_window_hours"] / (summary[capacity] * days * 24)

    dist_cols = ["duration", "gpu_request", "cpu_request", "worker_num", "job_gpu", "job_cpu", "gpu_hours", "cpu_hours"]
    distribution = jobs[dist_cols].describe(percentiles=[.25, .5, .75, .9, .95, .99, .999]).T
    distribution.index.name = "metric"
    jobs["duration_band"] = pd.cut(jobs.duration, bins=[0, 60, 600, 3600, DAY, 7 * DAY, np.inf], labels=["(0,1 min]", "(1,10 min]", "(10 min,1 h]", "(1 h,1 d]", "(1,7 d]", ">7 d"])
    jobs["gpu_size_band"] = pd.cut(jobs.job_gpu, bins=[0, 1, 4, 8, 32, np.inf], labels=["1", "2-4", "5-8", "9-32", ">32"])
    bands = {}
    for column in ["duration_band", "gpu_size_band"]:
        frame = jobs.groupby(column, observed=False).agg(jobs=("job_name", "size"), gpu_hours=("gpu_hours", "sum"), cpu_hours=("cpu_hours", "sum"))
        frame["job_share"] = frame.jobs / len(jobs)
        frame["gpu_work_share"] = frame.gpu_hours / jobs.gpu_hours.sum()
        bands[column] = frame
    profiles = jobs.groupby(["gpu_model", "gpu_request", "cpu_request", "worker_num"]).agg(jobs=("job_name", "size"), gpu_hours=("gpu_hours", "sum")).reset_index().sort_values("jobs", ascending=False).reset_index(drop=True)
    tables = {"model_summary": summary, "distribution_summary": distribution,
              "daily_submissions": daily, "daily_by_model": daily_model, "weekly_submissions": weekly,
              "offered_load_daily": pd.concat(offered, ignore_index=True),
              "concentration": concentration(jobs), "request_profiles": profiles,
              "node_configurations": nodes.groupby(["gpu_model", "gpu_capacity_num", "cpu_num"]).size().rename("nodes").reset_index(),
              **bands}
    return jobs, tables


def global_metrics(jobs: pd.DataFrame, nodes: pd.DataFrame, tables: dict, days: int) -> dict:
    daily = tables["daily_submissions"]
    model = tables["model_summary"]
    noninitial = daily[daily.day_index > 0]
    peak_jobs = daily.loc[daily.jobs.idxmax()]
    peak_work = daily.loc[daily.gpu_hours.idxmax()]
    initial = jobs[jobs.submit_time == 0]
    c = tables["concentration"]
    return {
        "jobs": len(jobs), "nodes": len(nodes), "gpu_models": len(model),
        "physical_gpus": int(nodes.gpu_capacity_num.sum()), "vcpus": float(nodes.cpu_num.sum()),
        "window_days": days, "window_seconds": days * DAY,
        "first_submit_seconds": float(jobs.submit_time.min()), "last_submit_seconds": float(jobs.submit_time.max()),
        "duration_mean_days": float(jobs.duration.mean() / DAY), "duration_median_minutes": float(jobs.duration.median() / 60),
        "duration_p95_days": float(jobs.duration.quantile(.95) / DAY), "duration_p99_days": float(jobs.duration.quantile(.99) / DAY),
        "duration_max_days": float(jobs.duration.max() / DAY),
        "total_gpu_hours": float(jobs.gpu_hours.sum()), "total_cpu_hours": float(jobs.cpu_hours.sum()),
        "single_worker_share": float((jobs.worker_num == 1).mean()), "single_gpu_job_share": float((jobs.job_gpu == 1).mean()),
        "multiworker_gpu_work_share": float(jobs.loc[jobs.worker_num > 1, "gpu_hours"].sum() / jobs.gpu_hours.sum()),
        "gpu_work_gini": gini(jobs.gpu_work_seconds.to_numpy()),
        "top_1pct_gpu_work_share": float(c[(c.gpu_model == "ALL") & (c.top_fraction == .01)].gpu_work_share.iloc[0]),
        "top_5pct_gpu_work_share": float(c[(c.gpu_model == "ALL") & (c.top_fraction == .05)].gpu_work_share.iloc[0]),
        "jobs_longer_than_window": int((jobs.duration > days * DAY).sum()),
        "immediate_completion_after_window_jobs": int(model.jobs_finishing_after_window_if_immediate.sum()),
        "gpu_work_after_window_share_if_immediate": float(model.gpu_work_after_window_hours.sum() / jobs.gpu_hours.sum()),
        "latest_immediate_completion_day": float(((jobs.submit_time + jobs.duration) / DAY).max()),
        "completion_capacity_lower_bound_days": float(max((model.gpu_hours / model.gpus / 24).max(), (model.cpu_hours / model.vcpus / 24).max())),
        "completion_combined_lower_bound_days": float(max(((jobs.submit_time + jobs.duration) / DAY).max(), (model.gpu_hours / model.gpus / 24).max(), (model.cpu_hours / model.vcpus / 24).max())),
        "initial_time_jobs": len(initial), "initial_time_gpu_work_share": float(initial.gpu_hours.sum() / jobs.gpu_hours.sum()),
        "day0_gpu_work_share": float(daily.loc[daily.day_index == 0, "gpu_hours"].sum() / jobs.gpu_hours.sum()),
        "peak_arrivals_day_index": int(peak_jobs.day_index), "peak_daily_arrivals": int(peak_jobs.jobs),
        "peak_work_day_index": int(peak_work.day_index), "peak_daily_gpu_hours": float(peak_work.gpu_hours),
        "daily_arrivals_cv": float(daily.jobs.std(ddof=0) / daily.jobs.mean()),
        "daily_gpu_work_cv": float(daily.gpu_hours.std(ddof=0) / daily.gpu_hours.mean()),
        "noninitial_daily_arrivals_cv": float(noninitial.jobs.std(ddof=0) / noninitial.jobs.mean()),
        "noninitial_daily_gpu_work_cv": float(noninitial.gpu_hours.std(ddof=0) / noninitial.gpu_hours.mean()),
        "daily_arrivals_gpu_work_pearson": float(daily.jobs.corr(daily.gpu_hours)),
        "noninitial_daily_arrivals_gpu_work_pearson": float(noninitial.jobs.corr(noninitial.gpu_hours)),
    }


def make_figures(jobs: pd.DataFrame, tables: dict, metrics: dict, output: Path) -> None:
    cache = ROOT / "tmp/matplotlib"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(cache))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter, ScalarFormatter
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.titlesize": 12,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.grid": True, "grid.alpha": .18, "savefig.dpi": 180,
                         "svg.fonttype": "none"})
    colors = ["#2274A5", "#E69F00", "#009E73", "#CC79A7", "#665191", "#D55E00"]
    summary = tables["model_summary"]
    labels = [m.replace("-SXM4-80GB", "") for m in summary.index]
    def save(fig, name):
        fig.savefig(output / f"{name}.png", bbox_inches="tight", facecolor="white")
        fig.savefig(output / f"{name}.svg", bbox_inches="tight", facecolor="white")
        plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.7), layout="constrained")
    y = np.arange(len(summary))
    for i, (col, label, color) in enumerate(zip(["job_share", "gpu_work_share", "gpu_capacity_share"], ["Jobs", "Requested GPU work", "GPU count"], colors)):
        axes[0].barh(y + (i - 1) * .23, summary[col] * 100, height=.21, label=label, color=color)
    axes[0].set(yticks=y, yticklabels=labels, xlabel="Share of cluster total (%)", title="(a) Demand and physical capacity")
    axes[0].invert_yaxis()
    axes[0].legend(fontsize=8, loc="lower right")
    for offset, col, label, color in [(-.16, "gpu_work_to_capacity_window", "GPU work / GPU capacity-time", colors[0]), (.16, "cpu_work_to_capacity_window", "CPU work / CPU capacity-time", colors[2])]:
        bars = axes[1].barh(y + offset, summary[col], height=.29, label=label, color=color)
        axes[1].bar_label(bars, fmt="%.2f", padding=3, fontsize=8)
    axes[1].set(yticks=y, yticklabels=labels, xlabel=f"Full requested work / {metrics['window_days']}-day supply", title="(b) Workload pressure, not utilization", xlim=(0, max(2.4, summary.gpu_work_to_capacity_window.max()*1.15)))
    axes[1].invert_yaxis()
    axes[1].axvline(1, color="#444444", linestyle="--", linewidth=1)
    axes[1].legend(fontsize=8, loc="lower right")
    save(fig, "01_model_demand_capacity")

    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.1), layout="constrained")
    for ax, col, factor, xlabel, title in [(axes[0], "duration", 1/3600, "Runtime (hours, log scale)", "(a) Runtime distribution"), (axes[1], "job_gpu", 1, "GPUs requested by the whole job (log scale)", "(b) Job size distribution")]:
        grouped = jobs.groupby(col).agg(jobs=("job_name", "size"), work=("gpu_hours", "sum")).sort_index()
        ax.step(grouped.index.to_numpy()*factor, grouped.jobs.cumsum()/len(jobs), where="post", label="Fraction of jobs", color=colors[0], linewidth=2)
        ax.step(grouped.index.to_numpy()*factor, grouped.work.cumsum()/jobs.gpu_hours.sum(), where="post", label="Fraction of GPU work", color=colors[1], linewidth=2)
        ax.set(xscale="log", xlabel=xlabel, ylabel="Cumulative fraction", ylim=(0, 1.02), title=title)
        ax.yaxis.set_major_formatter(PercentFormatter(1))
        ax.legend(loc="lower right" if col == "job_gpu" else "upper left", fontsize=9)
    save(fig, "02_job_distributions")

    fig, ax = plt.subplots(figsize=(7.5, 4.2), layout="constrained")
    work = np.sort(jobs.gpu_hours.to_numpy())[::-1]
    x = np.arange(1, len(work)+1) / len(work) * 100
    cumulative = work.cumsum()/work.sum()*100
    ax.plot(x, cumulative, color=colors[0], linewidth=2)
    reference = np.geomspace(x[0], 100, 500)
    ax.plot(reference, reference, linestyle="--", color="#999999", label="Equal-work reference")
    for fraction in [.01, .05, .1]:
        i = math.ceil(fraction*len(work))-1
        ax.scatter(x[i], cumulative[i], color=colors[1], zorder=3)
        ax.annotate(f"Top {fraction:.0%}: {cumulative[i]:.1f}%", (x[i], cumulative[i]), xytext=(8, -20 if fraction == .1 else -15), textcoords="offset points", fontsize=9)
    ax.set(xscale="log", xlim=(.01, 100), ylim=(0, 105), xticks=[.01, .1, 1, 10, 100], xlabel="Largest jobs ranked by GPU work (% of all jobs, log scale)", ylabel="Cumulative GPU work (%)", title="A small fraction of jobs dominates requested GPU work")
    ax.xaxis.set_major_formatter(ScalarFormatter())
    ax.legend(loc="upper left", fontsize=8)
    save(fig, "03_work_concentration")

    daily = tables["daily_submissions"]
    fig, axes = plt.subplots(2, 1, figsize=(11, 6), sharex=True, layout="constrained")
    for ax, col, label, title in [(axes[0], "jobs", "Submitted jobs / day", "(a) Job arrivals"), (axes[1], "gpu_hours", "Requested GPU-hours / day (log scale)", "(b) Full work attributed to each submission day")]:
        ax.plot(daily.day_index, daily[col], color=colors[0], alpha=.65, linewidth=1, label="Daily total")
        ax.plot(daily.day_index, daily[col].rolling(7, min_periods=7).mean(), color=colors[1], linewidth=2, label="7-day trailing mean")
        ax.set(ylabel=label, title=title)
        ax.legend(fontsize=8, loc="upper right")
    axes[1].set_yscale("log")
    axes[1].set_xlabel("Day index since trace start (day 0 is retained)")
    save(fig, "04_daily_submissions")

    fig, axes = plt.subplots(2, 3, figsize=(12, 6), sharex=True, sharey=True, layout="constrained")
    offered = tables["offered_load_daily"]
    for ax, (model, row), label, color in zip(axes.flat, summary.iterrows(), labels, colors):
        d = offered[offered.gpu_model == model]
        ax.plot(d.day_index + .5, d.mean_gpu_demand_to_capacity, color=color, linewidth=1.8)
        ax.axhline(1, color="#555555", linewidth=1, linestyle="--")
        ax.set(title=label, ylim=(0, max(2.4, offered.mean_gpu_demand_to_capacity.max()*1.08)))
    fig.supxlabel("Day index since trace start")
    fig.supylabel("Daily mean immediate-start GPU demand / capacity")
    fig.suptitle("Unconstrained offered load: every job hypothetically starts on arrival", fontsize=12)
    save(fig, "05_immediate_start_demand")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/question1")
    parser.add_argument("--skip-figures", action="store_true")
    args = parser.parse_args()
    files = {"jobs": args.data_dir / "job_info_clean.csv", "nodes": args.data_dir / "node_info_df.csv"}
    jobs = pd.read_csv(files["jobs"], dtype={"job_name": str, "gpu_model": str})
    nodes = pd.read_csv(files["nodes"], dtype={"node_name": str, "gpu_model": str})
    checks = validate_inputs(jobs, nodes)
    days = int(jobs.submit_time.max() // DAY) + 1
    jobs, tables = build_tables(jobs, nodes, days)
    checks["empty_cluster_infeasible_jobs"] = int((jobs.empty_cluster_worker_slots < jobs.worker_num).sum())
    if checks["empty_cluster_infeasible_jobs"]:
        raise ValueError("Some jobs cannot fit even when their compatible pool is empty")
    for resource in ["gpu", "cpu"]:
        total = jobs[f"{resource}_hours"].sum()
        for name in ["model_summary", "daily_submissions", "weekly_submissions", "daily_by_model", "duration_band", "gpu_size_band"]:
            if not np.isclose(tables[name][f"{resource}_hours"].sum(), total, rtol=1e-12, atol=1e-5):
                raise AssertionError(f"Work accounting mismatch: {name}")
    checks["aggregation_reconciliations_passed"] = True
    checks["offered_load_full_integral_reconciliations_passed"] = True
    args.output_dir.mkdir(parents=True, exist_ok=True)
    table_dir, fig_dir = args.output_dir / "tables", args.output_dir / "figures"
    table_dir.mkdir(exist_ok=True)
    fig_dir.mkdir(exist_ok=True)
    for name, frame in tables.items():
        frame.to_csv(table_dir / f"{name}.csv", index=not isinstance(frame.index, pd.RangeIndex), encoding="utf-8", float_format="%.12g")
    metrics = global_metrics(jobs, nodes, tables, days)
    dump_json(args.output_dir / "summary.json", metrics)
    dump_json(args.output_dir / "validation.json", checks)
    dump_json(args.output_dir / "provenance.json", {
        "sources": {key: {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size} for key, path in files.items()},
        "python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__,
        "window_definition": "[0, (floor(last_submit_seconds/86400)+1)*86400); bins are zero-based days",
        "window_days": days, "quantiles": "linear interpolation (pandas default)",
        "daily_cv": "population standard deviation (ddof=0) / mean; descriptive of the full observed daily series",
        "top_fraction": "ceil(fraction * number_of_jobs), ranked descending by full GPU work",
        "cpu_unit": "vCPU", "gpu_work_unit": "GPU-hours", "cpu_work_unit": "vCPU-hours",
        "offered_load": "Each job occupies [submit, submit+duration), with no capacity or placement restrictions; not actual utilization",
        "daily_work": "Full lifetime work assigned to the job's submission day; not work executed that day",
        "time_zero": "All time-zero submissions retained; no assertion of a natural day-zero arrival process",
        "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    })
    if not args.skip_figures:
        make_figures(jobs, tables, metrics, fig_dir)
    print(json.dumps(metrics, indent=2))
    print(f"Outputs: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
