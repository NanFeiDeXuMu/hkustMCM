"""Evaluate complete schedules at a shared 90-day horizon and after drainage."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

DAY = 86400.0
HORIZON = 90 * DAY
DONORS = ("A100-SXM4-80GB", "A800-SXM4-80GB", "H800", "GPU-series-2")


def distribution(values: pd.Series, prefix: str) -> dict:
    values = values.astype(float)
    if len(values) == 0:
        return {f"{prefix}_{suffix}": None for suffix in ("mean", "median", "p95", "p99", "max")}
    return {f"{prefix}_mean": float(values.mean()),
            f"{prefix}_median": float(values.median()),
            f"{prefix}_p95": float(values.quantile(.95)),
            f"{prefix}_p99": float(values.quantile(.99)),
            f"{prefix}_max": float(values.max())}


def job_metrics(frame: pd.DataFrame, horizon: float = HORIZON) -> dict:
    wait = frame.start_time - frame.submit_time
    turnaround = frame.end_time - frame.submit_time
    result = {"jobs": len(frame), **distribution(wait / 3600, "wait_hours"),
              **distribution(turnaround / 3600, "turnaround_hours")}
    if frame.empty:
        return result
    result.update({
        "completed_at_90d": int((frame.end_time <= horizon).sum()),
        "waiting_at_90d": int(((frame.submit_time <= horizon) & (frame.start_time > horizon)).sum()),
        "running_at_90d": int(((frame.start_time <= horizon) & (frame.end_time > horizon)).sum()),
        "immediate_start_fraction": float((wait.abs() < 1e-7).mean()),
        "wait_over_1day_fraction": float((wait > DAY).mean()),
        "wait_over_7days_fraction": float((wait > 7 * DAY).mean()),
        "last_completion_days": float(frame.end_time.max() / DAY),
        "bounded_slowdown_60s_mean": float(np.maximum(1, turnaround / frame.run_duration.clip(lower=60)).mean()),
    })
    return result


def gpu_queue_idle_metrics(schedule: pd.DataFrame, nodes: pd.DataFrame,
                           horizons: dict[str, float] | None = None) -> tuple[dict, pd.DataFrame]:
    """Integrate queue/idle diagnostics exactly over constant event intervals.

    Running GPU requests belong to assigned_model on [start, end). Queued GPU
    requests belong to requested_model on [submit, start), including migrants.
    Equal-time changes are aggregated before the following interval. The fixed
    donor set is independent of which models a routing scenario actually uses.

    ``gpu_stranded_upper_bound_hours`` is a matching upper bound on this fixed
    realized timeline, ignoring compatibility, CPU and node packing. It is not
    a bound on the gain of a different schedule, which changes that timeline.
    """
    capacities = nodes.groupby("gpu_model").gpu_capacity_num.sum().astype(float)
    if capacities.empty or (capacities <= 0).any() or not np.isfinite(capacities).all():
        raise ValueError("Queue metrics require positive finite GPU capacities")
    if horizons is None:
        horizons = {"90d": HORIZON, "until_last_completion": float(schedule.end_time.max())}
    if not horizons or any(not np.isfinite(h) or h <= 0 for h in horizons.values()):
        raise ValueError("Queue-metric horizons must be positive and finite")
    requests = (schedule.gpu_request * schedule.worker_num).to_numpy(dtype=float)
    submits = schedule.submit_time.to_numpy(dtype=float)
    starts = schedule.start_time.to_numpy(dtype=float)
    ends = schedule.end_time.to_numpy(dtype=float)
    if (not np.isfinite(np.r_[requests, submits, starts, ends]).all()
            or np.any(requests <= 0) or np.any(submits < 0)
            or np.any(starts < submits) or np.any(ends <= starts)):
        raise ValueError("Invalid job GPU requests or scheduling intervals")
    requested = schedule.requested_model.to_numpy()
    assigned = schedule.assigned_model.to_numpy()
    if not (schedule.requested_model.isin(capacities.index).all()
            and schedule.assigned_model.isin(capacities.index).all()):
        raise ValueError("Queue-metric job model has no corresponding GPU pool")

    # One event grid for every pool and horizon; memory stays O(events), rather
    # than retaining separate busy/queue matrices for all six models.
    count = len(schedule)
    times, inverse = np.unique(np.r_[submits, starts, ends, 0.0, list(horizons.values())],
                               return_inverse=True)
    submit_ix, start_ix, end_ix = inverse[:count], inverse[count:2*count], inverse[2*count:3*count]
    interval_count = len(times) - 1
    weights = {name: np.maximum(0.0, np.minimum(times[1:], horizon) - times[:-1])
               for name, horizon in horizons.items()}

    def interval_load(mask: np.ndarray, begin: np.ndarray, finish: np.ndarray) -> np.ndarray:
        changes = np.bincount(begin[mask], weights=requests[mask], minlength=len(times))
        changes -= np.bincount(finish[mask], weights=requests[mask], minlength=len(times))
        return np.cumsum(changes)[:interval_count]

    a10_queue = interval_load(requested == "A10", submit_ix, start_ix)
    a10_waiting = a10_queue > 0
    a10_queue_seconds = {name: float(weight[a10_waiting].sum()) for name, weight in weights.items()}
    donor_idle = np.zeros(interval_count, dtype=float)
    donor_capacity = float(capacities.loc[capacities.index.isin(DONORS)].sum())
    allocation_rates = {name: {} for name in horizons}
    own_idle_totals = {name: 0.0 for name in horizons}
    rows = []
    for model, capacity in capacities.items():
        busy = interval_load(assigned == model, start_ix, end_ix)
        idle = capacity - busy
        queue = a10_queue if model == "A10" else interval_load(requested == model, submit_ix, start_ix)
        if np.any(idle < -1e-7) or np.any(busy < -1e-7) or np.any(queue < -1e-7):
            raise ValueError(f"Invalid GPU occupancy or queue accounting for {model}")
        idle = np.maximum(0.0, idle)
        is_donor = model in DONORS
        if is_donor:
            donor_idle += idle
        queued = queue > 0
        row = {"gpu_model": model, "gpus": float(capacity), "in_fixed_donor_set": is_donor}
        for name, weight in weights.items():
            horizon = horizons[name]
            own_queue_seconds = float(weight[queued].sum())
            own_idle_seconds = float(np.dot(idle * queued, weight))
            a10_idle_seconds = float(np.dot(idle * a10_waiting, weight))
            own_idle_totals[name] += own_idle_seconds
            allocation_rates[name][model] = float(np.dot(busy, weight) / (capacity * horizon))
            row.update({
                f"gpu_allocation_rate_{name}": allocation_rates[name][model],
                f"own_queue_nonempty_hours_{name}": own_queue_seconds / 3600,
                f"own_queue_nonempty_fraction_{name}": own_queue_seconds / horizon,
                f"own_queue_idle_gpu_hours_{name}": own_idle_seconds / 3600,
                f"own_queue_idle_fraction_{name}": (own_idle_seconds / (capacity * own_queue_seconds)
                                                     if own_queue_seconds else None),
                f"own_queue_idle_capacity_time_fraction_{name}": own_idle_seconds / (capacity * horizon),
                f"a10_queue_idle_gpu_hours_{name}": a10_idle_seconds / 3600,
                f"a10_queue_idle_fraction_{name}": (a10_idle_seconds / (capacity * a10_queue_seconds[name])
                                                    if a10_queue_seconds[name] else None),
            })
        rows.append(row)

    result = {}
    for name, weight in weights.items():
        rates = allocation_rates[name]
        donor_rate = (sum(rates[model] * capacity for model, capacity in capacities.items() if model in DONORS)
                      / donor_capacity if donor_capacity else None)
        result.update({
            f"gpu_stranded_upper_bound_hours_{name}": float(np.dot(np.minimum(donor_idle, a10_queue), weight) / 3600),
            f"donor_idle_while_a10_queued_gpu_hours_{name}": float(np.dot(donor_idle * a10_waiting, weight) / 3600),
            f"a10_queue_nonempty_hours_{name}": a10_queue_seconds[name] / 3600,
            f"gpu_idle_while_native_queued_hours_{name}": own_idle_totals[name] / 3600,
            f"gpu_allocation_rate_range_{name}": float(max(rates.values()) - min(rates.values())),
            f"donor_gpu_allocation_rate_{name}": donor_rate,
            f"a10_minus_donor_gpu_allocation_rate_{name}": (rates["A10"] - donor_rate
                                                           if "A10" in rates and donor_rate is not None else None),
        })
    return result, pd.DataFrame(rows)


def evaluate(schedule: pd.DataFrame, nodes: pd.DataFrame, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    frame = schedule.copy()
    frame["job_gpus"] = frame.gpu_request * frame.worker_num
    frame["job_cpus"] = frame.cpu_request * frame.worker_num
    frame["window_runtime"] = (np.minimum(frame.end_time, HORIZON) - np.maximum(frame.start_time, 0)).clip(lower=0)
    capacities = nodes.groupby("gpu_model").agg(gpus=("gpu_capacity_num", "sum"), cpus=("cpu_num", "sum"))
    result = job_metrics(frame)
    total_time = float(frame.end_time.max())
    for resource, units, capacity in (("gpu", "job_gpus", "gpus"), ("cpu", "job_cpus", "cpus")):
        work = float((frame[units] * frame.run_duration).sum())
        in_window = float((frame[units] * frame.window_runtime).sum())
        result[f"allocated_{resource}_hours"] = work / 3600
        result[f"{resource}_allocation_rate_90d"] = in_window / (float(capacities[capacity].sum()) * HORIZON)
        result[f"{resource}_allocation_rate_until_last_completion"] = work / (float(capacities[capacity].sum()) * total_time)
    result["migrated_jobs"] = int((frame.requested_model != frame.assigned_model).sum())
    a10 = frame[frame.requested_model == "A10"]
    migrated = a10[a10.assigned_model != "A10"]
    original_work = a10.job_gpus * a10.original_duration
    result["a10_migrated_job_fraction"] = float(len(migrated) / len(a10)) if len(a10) else 0
    result["a10_migrated_original_work_fraction"] = float((migrated.job_gpus * migrated.original_duration).sum() / original_work.sum()) if original_work.sum() else 0
    if "flexible" in frame.columns:
        flexible = a10[a10.flexible.astype(bool)]
        result["a10_flexible_jobs"] = len(flexible)
        result["a10_flexible_job_fraction"] = float(len(flexible) / len(a10)) if len(a10) else 0
        result["a10_flexible_original_work_fraction"] = float((flexible.job_gpus * flexible.original_duration).sum() / original_work.sum()) if original_work.sum() else 0
    by_original = [{"requested_model": model, **job_metrics(group)} for model, group in frame.groupby("requested_model")]
    pd.DataFrame(by_original).to_csv(output / "metrics_by_requested_model.csv", index=False)
    by_assigned = []
    for model, cap in capacities.iterrows():
        group = frame[frame.assigned_model == model]
        row = {"assigned_model": model, "jobs": len(group), "gpus": int(cap.gpus), "vcpus": float(cap.cpus)}
        for resource, units, capacity in (("gpu", "job_gpus", "gpus"), ("cpu", "job_cpus", "cpus")):
            row[f"{resource}_allocation_rate_90d"] = float((group[units] * group.window_runtime).sum() / (cap[capacity] * HORIZON))
            row[f"allocated_{resource}_hours"] = float((group[units] * group.run_duration).sum() / 3600)
        by_assigned.append(row)
    pd.DataFrame(by_assigned).to_csv(output / "metrics_by_assigned_model.csv", index=False)
    frame["duration_group"] = pd.cut(frame.original_duration, [0, 3600, DAY, 7*DAY, np.inf], labels=["<=1h", "1h-1d", "1d-7d", ">7d"])
    groups = [{"group": str(name), **job_metrics(group)} for name, group in frame.groupby("duration_group", observed=True)]
    groups += [{"group": label, **job_metrics(group)} for label, group in (
        ("single_worker", frame[frame.worker_num == 1]), ("multiworker", frame[frame.worker_num > 1]))]
    pd.DataFrame(groups).to_csv(output / "metrics_by_job_group.csv", index=False)
    idle_metrics, idle_by_model = gpu_queue_idle_metrics(frame, nodes)
    result.update(idle_metrics)
    idle_by_model.to_csv(output / "metrics_idle_while_queued_by_model.csv", index=False)
    result["metric_definitions"] = {
        "wait": "start - submit; all submitted jobs, including those completing after day 90",
        "allocation_rate": "allocated resource-seconds / physical capacity-seconds; simulated allocation, not measured hardware utilization",
        "fixed_horizon_seconds": HORIZON,
        "bounded_slowdown": "max(1,(end-submit)/max(actual simulated runtime,60 seconds))",
        "completion_convention": "completed end<=H; running start<=H<end; waiting start>H (after events at H)",
        "queue_gpu_requests": "requested_model; whole-job GPUs on [submit,start), including migrants before they start",
        "idle_queue_integration": "Exact constant-interval integration after aggregating all events at the same time; no sampling",
        "fixed_donor_models": list(DONORS),
        "stranded_upper_bound": "Integral min(fixed-donor idle GPUs,A10 queued GPUs); an upper bound on matching idle GPUs on this realized timeline, ignoring compatibility/CPU/packing, not on alternative-schedule gains",
        "own_queue_idle_fraction": "Conditional idle fraction: own-queue idle GPU-seconds / (pool GPUs * own-queue-nonempty seconds); null if the queue is never nonempty",
        "own_queue_idle_capacity_time_fraction": "Own-queue idle GPU-seconds / (pool GPUs * entire horizon seconds)",
        "a10_queue_idle_fraction": "Conditional idle fraction during A10 queue time; null if A10 never queues",
        "allocation_imbalance": "Range across all available pools; signed A10 minus GPU-capacity-weighted fixed-donor allocation rate",
    }
    (output / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return result


def paired_comparison(baseline: pd.DataFrame, changed: pd.DataFrame, output: Path) -> pd.DataFrame:
    """Compare the same original jobs, so recipient-pool harms are visible."""
    base = baseline.set_index("job_name")
    new = changed.set_index("job_name").reindex(base.index)
    assert new.start_time.notna().all() and len(new) == len(base)
    rows = []
    for model in ["ALL", *sorted(base.requested_model.unique())]:
        mask = np.ones(len(base), dtype=bool) if model == "ALL" else (base.requested_model == model).to_numpy()
        before, after = base.loc[mask], new.loc[mask]
        delta = ((after.start_time - after.submit_time) - (before.start_time - before.submit_time)) / 3600
        positive = delta[delta > 1e-7]
        wait_before = (before.start_time - before.submit_time) / 3600
        wait_after = (after.start_time - after.submit_time) / 3600
        rows.append({"requested_model": model, "jobs": len(before),
                     "wait_mean_before_hours": float((before.start_time-before.submit_time).mean()/3600),
                     "wait_mean_after_hours": float((after.start_time-after.submit_time).mean()/3600),
                     "wait_delta_mean_hours": float(delta.mean()),
                     "wait_increased_fraction": float((delta > 1e-7).mean()),
                     "wait_decreased_fraction": float((delta < -1e-7).mean()),
                     "positive_wait_delta_p95_hours": float(positive.quantile(.95)) if len(positive) else 0,
                     "max_wait_increase_hours": float(max(0, delta.max())),
                     "max_wait_decrease_hours": float(min(0, delta.min())),
                     "completed_90d_delta": int((after.end_time<=HORIZON).sum()-(before.end_time<=HORIZON).sum()),
                     "wait_p95_before_hours": float(wait_before.quantile(.95)),
                     "wait_p95_after_hours": float(wait_after.quantile(.95)),
                     "wait_max_before_hours": float(wait_before.max()),
                     "wait_max_after_hours": float(wait_after.max()),
                     "wait_p95_delta_hours": float(wait_after.quantile(.95) - wait_before.quantile(.95)),
                     "wait_max_delta_hours": float(wait_after.max() - wait_before.max())})
    result = pd.DataFrame(rows)
    result.to_csv(output / "paired_wait_changes.csv", index=False)
    return result
