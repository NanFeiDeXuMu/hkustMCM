"""Paired routing benefits, native harm, compensation, and actual borrowing.

Waiting differences are new minus baseline. A10 savings are net, donor cost
counts only positive differences, and GPU weights are requests times workers.
All jobs and their complete intervals are included; no 90-day censoring applies.
These metrics do not replace the independent per-node schedule audit.
"""
from __future__ import annotations

from decimal import Decimal
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

HOUR = 3600.0
TIME_TOLERANCE = 1e-8
ORIGINAL_COLUMNS = ("requested_model", "submit_time", "original_duration",
                    "gpu_request", "cpu_request", "worker_num")
NUMERIC_COLUMNS = ("submit_time", "start_time", "end_time", "original_duration",
                   "run_duration", "wait_time", "gpu_request", "cpu_request", "worker_num")
SCHEDULE_COLUMNS = ("job_name", "requested_model", "assigned_model", *NUMERIC_COLUMNS, "migrated")
HARM_COLUMNS = ("donor_model", "native_jobs", "native_wait_increased_jobs",
                "native_wait_increased_fraction", "native_wait_mean_delta_hours",
                "native_wait_p95_delta_hours", "donor_native_job_hours_added",
                "native_max_wait_increase_hours", "compensation_gpu_hours")
PEAK_COLUMNS = ("donor_model", "migrated_jobs", "peak_borrowed_gpus", "peak_borrowed_cpus",
                "donor_gpu_capacity", "donor_cpu_capacity", "peak_borrowed_gpu_fraction",
                "peak_borrowed_cpu_fraction")


def _boolean(value) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise ValueError("migrated must contain booleans or true/false text")


def _prepare_schedule(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    missing = set(SCHEDULE_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"{label}: missing schedule columns {sorted(missing)}")
    result = frame.loc[:, SCHEDULE_COLUMNS].copy()
    if result.isna().any().any():
        raise ValueError(f"{label}: missing schedule values")
    for column in ("job_name", "requested_model", "assigned_model"):
        result[column] = result[column].astype(str)
        if result[column].str.strip().eq("").any():
            raise ValueError(f"{label}: empty {column}")
    if result.job_name.duplicated().any():
        raise ValueError(f"{label}: duplicate job identifiers")
    for column in NUMERIC_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="raise")
        if not np.isfinite(result[column].to_numpy(dtype=float)).all():
            raise ValueError(f"{label}: nonfinite {column}")
    for column in ("gpu_request", "worker_num"):
        values = result[column].to_numpy(dtype=float)
        if ((values <= 0) | (values != np.floor(values))).any():
            raise ValueError(f"{label}: {column} must be positive integers")
    if ((result.cpu_request < 0) | (result.submit_time < 0)
            | (result.original_duration <= 0) | (result.run_duration <= 0)).any():
        raise ValueError(f"{label}: invalid resource or time domain")
    if ((result.start_time < result.submit_time) | (result.end_time <= result.start_time)).any():
        raise ValueError(f"{label}: invalid start/end time")
    if not np.allclose(result.wait_time, result.start_time - result.submit_time,
                       atol=TIME_TOLERANCE, rtol=0):
        raise ValueError(f"{label}: wait_time disagrees with start minus submission")
    if not np.allclose(result.run_duration, result.end_time - result.start_time,
                       atol=TIME_TOLERANCE, rtol=0):
        raise ValueError(f"{label}: run_duration disagrees with end minus start")
    result["migrated"] = result.migrated.map(_boolean)
    if not result.migrated.eq(result.assigned_model.ne(result.requested_model)).all():
        raise ValueError(f"{label}: migrated flag disagrees with assigned model")
    native = ~result.migrated
    if not result.loc[native, "run_duration"].eq(result.loc[native, "original_duration"]).all():
        raise ValueError(f"{label}: native runtime differs from original duration")
    return result.sort_values("job_name", kind="stable").set_index("job_name")


def _capacities(nodes: pd.DataFrame) -> pd.DataFrame:
    required = ("node_name", "gpu_model", "gpu_capacity_num", "cpu_num")
    if not set(required).issubset(nodes.columns):
        raise ValueError("Missing node capacity columns")
    table = nodes.loc[:, required].copy()
    if table.empty or table.isna().any().any():
        raise ValueError("Need nonempty nodes without missing values")
    table["node_name"] = table.node_name.astype(str)
    table["gpu_model"] = table.gpu_model.astype(str)
    if table.node_name.duplicated().any() or table.node_name.str.strip().eq("").any():
        raise ValueError("Need unique nonempty node identifiers")
    for column in ("gpu_capacity_num", "cpu_num"):
        table[column] = pd.to_numeric(table[column], errors="raise")
        values = table[column].to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values <= 0).any():
            raise ValueError("Node capacities must be finite and positive")
    if table.gpu_capacity_num.ne(np.floor(table.gpu_capacity_num)).any():
        raise ValueError("Node GPU capacities must be integers")
    return table.groupby("gpu_model", sort=True).agg(
        gpu_capacity=("gpu_capacity_num", "sum"), cpu_capacity=("cpu_num", "sum"))


def _harm(base_wait: np.ndarray, new_wait: np.ndarray, gpu: np.ndarray) -> dict:
    delta = new_wait - base_wait
    positive = np.maximum(delta, 0)
    count = len(delta)
    return {
        "native_jobs": count,
        "native_wait_increased_jobs": int(np.count_nonzero(delta > 0)),
        "native_wait_increased_fraction": float(np.mean(delta > 0)) if count else None,
        "native_wait_mean_delta_hours": math.fsum(delta) / count / HOUR if count else None,
        "native_wait_p95_delta_hours": float(
            np.quantile(new_wait, .95, method="linear") - np.quantile(base_wait, .95, method="linear")
        ) / HOUR if count else None,
        "donor_native_job_hours_added": math.fsum(positive) / HOUR,
        "native_max_wait_increase_hours": max(0.0, float(delta.max()) / HOUR) if count else 0.0,
        "compensation_gpu_hours": math.fsum(positive * gpu) / HOUR,
    }


def _borrowing_peak(model: str, migrated: pd.DataFrame, capacity: pd.Series) -> dict:
    """Entire-trace half-open occupancy; exact decimal CPU accumulation.

    Event phase 0 releases before phase 1 starts at the same timestamp. Peaks
    can belong to different times for GPU and CPU. No future/policy inference
    is used: the result describes only the supplied realized schedule.
    """
    events = []
    for row in migrated.itertuples():
        gpu = int(row.gpu_request) * int(row.worker_num)
        cpu = Decimal(str(row.cpu_request)) * int(row.worker_num)
        events.append((float(row.start_time), 1, gpu, cpu))
        events.append((float(row.end_time), 0, -gpu, -cpu))
    gpu_used = peak_gpu = 0
    cpu_used = peak_cpu = Decimal(0)
    for _, _, gpu_change, cpu_change in sorted(events, key=lambda event: (event[0], event[1])):
        gpu_used += gpu_change
        cpu_used += cpu_change
        if gpu_used < 0 or cpu_used < 0:
            raise AssertionError("Borrowing event accounting became negative")
        peak_gpu = max(peak_gpu, gpu_used)
        peak_cpu = max(peak_cpu, cpu_used)
    if gpu_used != 0 or cpu_used != 0:
        raise AssertionError("Borrowing events do not balance")
    gpu_capacity = int(capacity.gpu_capacity)
    cpu_capacity = float(capacity.cpu_capacity)
    if peak_gpu > gpu_capacity or float(peak_cpu) > cpu_capacity + TIME_TOLERANCE:
        raise ValueError(f"Migrants alone exceed physical capacity in {model}")
    return {"donor_model": model, "migrated_jobs": len(migrated),
            "peak_borrowed_gpus": peak_gpu, "peak_borrowed_cpus": float(peak_cpu),
            "donor_gpu_capacity": gpu_capacity, "donor_cpu_capacity": cpu_capacity,
            "peak_borrowed_gpu_fraction": peak_gpu / gpu_capacity,
            "peak_borrowed_cpu_fraction": float(peak_cpu) / cpu_capacity}


def analyze_routing_frontier(baseline: pd.DataFrame, schedule: pd.DataFrame,
                             nodes: pd.DataFrame, donors,
                             output_dir: str | Path | None = None) -> dict:
    """Return scalar metrics plus ``native_harm_by_donor``/``borrowing_peaks``.

    IDs must match exactly, and every original job attribute must be equal by
    value. The baseline must have no routing. Only original A10 jobs may move
    to the supplied donor models. Wait differences use start minus submission.

    ``exchange_ratio=None, exchange_ratio_infinite=True`` denotes zero positive
    donor cost, even when A10 net savings are nonpositive. Recommendation code
    must separately require positive A10 savings. ``compensation_share`` is
    None whenever net GPU-weighted savings S<=0, flagged by
    ``compensation_share_undefined``. Empty native groups have undefined (None)
    fractions/means/quantiles; their counts and costs are zero. The maximum
    per-donor values ignore empty native groups and default to zero.
    """
    if isinstance(donors, str):
        raise ValueError("donors must be a sequence of model names, not one string")
    donors = tuple(str(model) for model in donors)
    if len(set(donors)) != len(donors) or "A10" in donors:
        raise ValueError("Donors must be distinct alternatives to A10")
    base = _prepare_schedule(baseline, "baseline")
    new = _prepare_schedule(schedule, "schedule")
    if not base.index.equals(new.index):
        raise ValueError("Paired schedules must contain exactly the same job identifiers")
    for column in ORIGINAL_COLUMNS:
        if not base[column].eq(new[column]).all():
            raise ValueError(f"Original job attributes differ: {column}")
    if base.migrated.any():
        raise ValueError("The paired baseline must have no routing")
    capacity = _capacities(nodes)
    models = set(capacity.index)
    if not set(donors).issubset(models):
        raise ValueError("A donor model is absent from the node table")
    if not set(base.requested_model).issubset(models) or not set(new.assigned_model).issubset(models):
        raise ValueError("Schedule model is absent from the node table")
    migrants = new.loc[new.migrated]
    if (migrants.requested_model.ne("A10").any()
            or not migrants.assigned_model.isin(donors).all()):
        raise ValueError("Only A10 jobs may migrate, and only to configured donors")
    base_wait = (base.start_time - base.submit_time).to_numpy(dtype=float)
    new_wait = (new.start_time - new.submit_time).to_numpy(dtype=float)
    delta = new_wait - base_wait
    gpu = (base.gpu_request * base.worker_num).to_numpy(dtype=float)
    a10 = base.requested_model.eq("A10").to_numpy()
    native = base.requested_model.isin(donors).to_numpy()
    if not a10.any():
        raise ValueError("Routing frontier analysis requires at least one A10 job")
    saved = math.fsum(-delta[a10]) / HOUR
    weighted_saved = math.fsum(-delta[a10] * gpu[a10]) / HOUR
    aggregate = _harm(base_wait[native], new_wait[native], gpu[native])
    added = aggregate["donor_native_job_hours_added"]
    harm_rows = []
    peak_rows = []
    for model in donors:
        mask = base.requested_model.eq(model).to_numpy()
        harm_rows.append({"donor_model": model, **_harm(base_wait[mask], new_wait[mask], gpu[mask])})
        peak_rows.append(_borrowing_peak(model, migrants.loc[migrants.assigned_model.eq(model)], capacity.loc[model]))

    def maximum(field: str) -> float:
        return max((row[field] for row in harm_rows if row[field] is not None), default=0.0)

    result = {
        "a10_jobs": int(np.count_nonzero(a10)),
        "a10_job_hours_saved": saved,
        "a10_mean_wait_gain_hours": saved / int(np.count_nonzero(a10)),
        "donor_native_jobs": aggregate["native_jobs"],
        "donor_native_job_hours_added": added,
        "exchange_ratio": saved / added if added > 0 else None,
        "exchange_ratio_infinite": added == 0,
        "native_wait_increased_jobs": aggregate["native_wait_increased_jobs"],
        "native_wait_increased_fraction": aggregate["native_wait_increased_fraction"],
        "native_wait_mean_delta_hours": aggregate["native_wait_mean_delta_hours"],
        "native_wait_p95_delta_hours": aggregate["native_wait_p95_delta_hours"],
        "max_donor_native_wait_increased_fraction": maximum("native_wait_increased_fraction"),
        "max_donor_mean_wait_delta_hours": maximum("native_wait_mean_delta_hours"),
        "max_donor_p95_delta_hours": maximum("native_wait_p95_delta_hours"),
        "a10_wait_increased_jobs": int(np.count_nonzero(delta[a10] > 0)),
        "a10_wait_increased_fraction": float(np.mean(delta[a10] > 0)),
        "a10_max_wait_increase_hours": max(0.0, float(delta[a10].max()) / HOUR),
        "compensation_gpu_hours": aggregate["compensation_gpu_hours"],
        "a10_gpu_wait_hours_saved": weighted_saved,
        "compensation_share": aggregate["compensation_gpu_hours"] / weighted_saved if weighted_saved > 0 else None,
        "compensation_share_undefined": weighted_saved <= 0,
        "native_harm_by_donor": harm_rows,
        "borrowing_peaks": peak_rows,
    }
    if output_dir is not None:
        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "frontier_metrics.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        pd.DataFrame(harm_rows, columns=HARM_COLUMNS).to_csv(destination / "native_harm_by_donor.csv", index=False)
        pd.DataFrame(peak_rows, columns=PEAK_COLUMNS).to_csv(destination / "borrowing_peaks.csv", index=False)
    return result
