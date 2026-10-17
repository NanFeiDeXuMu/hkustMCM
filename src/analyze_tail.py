"""Paired FCFS harm and tail diagnostics without changing legacy metrics.

All jobs are followed until they start and finish; no 90-day censoring applies
to these waiting-time or slowdown distributions. Raw requests are per worker.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
HOUR = 3600.0
DAY = 24 * HOUR
GROUPS = ["<=1h", "1h-1d", "1d-7d", ">7d"]
QUANTILES = {"min": 0., "p25": .25, "p50": .5, "p75": .75,
             "p95": .95, "p99": .99, "max": 1.}
NUMERIC = ["submit_time", "start_time", "end_time", "original_duration",
           "run_duration", "gpu_request", "worker_num"]
ORIGINAL = ["requested_model", "submit_time", "original_duration", "gpu_request", "worker_num"]


def _validated(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    missing = set(["job_name", "requested_model", *NUMERIC]) - set(frame.columns)
    if missing:
        raise ValueError(f"{label}: missing columns {sorted(missing)}")
    if frame.empty:
        raise ValueError(f"{label}: an empty schedule has no population to analyze")
    identifiers = frame.job_name
    if identifiers.isna().any() or not identifiers.map(lambda value: isinstance(value, str) and bool(value)).all():
        raise ValueError(f"{label}: job_name must contain nonempty strings without nulls")
    if identifiers.duplicated().any():
        raise ValueError(f"{label}: duplicate job_name")
    models = frame.requested_model
    if models.isna().any() or not models.map(lambda value: isinstance(value, str) and bool(value)).all():
        raise ValueError(f"{label}: requested_model must contain nonempty strings")
    result = frame.set_index("job_name")
    try:
        numbers = result[NUMERIC].to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}: nonnumeric schedule field") from exc
    if not np.isfinite(numbers).all():
        raise ValueError(f"{label}: nonfinite schedule field")
    result = result.copy()
    result[NUMERIC] = result[NUMERIC].astype(float)
    if ((result.submit_time < 0).any() or (result.start_time < result.submit_time).any()
            or (result.end_time <= result.start_time).any()
            or (result[["original_duration", "run_duration", "gpu_request", "worker_num"]] <= 0).any().any()):
        raise ValueError(f"{label}: invalid time, duration, or positive resource domain")
    if not np.allclose(result.end_time-result.start_time, result.run_duration, rtol=1e-12, atol=1e-7):
        raise ValueError(f"{label}: run_duration differs from end_time - start_time")
    return result


def _slowdown_summary(values: pd.Series) -> dict:
    if values.empty:
        return {f"bounded_slowdown_60s_{name}": None for name in ("mean", "p99", "max")}
    return {"bounded_slowdown_60s_mean": float(values.mean()),
            "bounded_slowdown_60s_p99": float(values.quantile(.99, interpolation="linear")),
            "bounded_slowdown_60s_max": float(values.max())}


def analyze_tail(schedule: pd.DataFrame, fcfs: pd.DataFrame, output: Path) -> dict:
    """Save exact-ID paired harm, tail cohorts, and bounded slowdown summaries.

    Harm is strictly positive change in ``start-submit`` (no epsilon). Work
    weights are original_duration * gpu_request * worker_num, regardless of a
    job's simulated runtime. Cohort duration quantiles use original duration.
    Empty cohorts have zero counts/work shares and null conditional statistics.
    """
    candidate = _validated(schedule, "schedule")
    baseline = _validated(fcfs, "fcfs")
    missing = baseline.index.difference(candidate.index)
    extra = candidate.index.difference(baseline.index)
    if len(missing) or len(extra):
        raise ValueError(f"Job ID sets differ: missing={len(missing)}, extra={len(extra)}")
    candidate = candidate.reindex(baseline.index)
    for field in ORIGINAL:
        if not np.array_equal(candidate[field].to_numpy(), baseline[field].to_numpy()):
            raise ValueError(f"Original job attribute differs after ID alignment: {field}")

    wait = candidate.start_time - candidate.submit_time
    baseline_wait = baseline.start_time - baseline.submit_time
    delta = wait - baseline_wait
    harmed = delta > 0
    original_work = candidate.original_duration * candidate.gpu_request * candidate.worker_num / HOUR
    total_work = float(original_work.sum())
    if not np.isfinite(total_work) or total_work <= 0:
        raise ValueError("Original GPU work must have a positive finite total")
    slowdown = np.maximum(1., (candidate.end_time-candidate.submit_time) / candidate.run_duration.clip(lower=60.))
    fcfs_max = float(baseline_wait.max())
    cohorts = {
        "all": pd.Series(True, index=candidate.index),
        "fcfs_harmed": harmed,
        "wait_over_1000h": wait > 1000*HOUR,
        "wait_over_fcfs_max": wait > fcfs_max,
    }
    result = {
        "jobs": len(candidate),
        "fcfs_max_wait_hours": fcfs_max/HOUR,
        "fcfs_harmed_jobs": int(harmed.sum()),
        "fcfs_harmed_fraction": float(harmed.mean()),
        "fcfs_harmed_mean_wait_increase_hours": float(delta[harmed].mean()/HOUR) if harmed.any() else None,
        "fcfs_harmed_original_gpu_work_fraction": float(original_work[harmed].sum()/total_work),
        "wait_over_1000h_jobs": int(cohorts["wait_over_1000h"].sum()),
        "wait_over_fcfs_max_jobs": int(cohorts["wait_over_fcfs_max"].sum()),
        **_slowdown_summary(slowdown),
    }

    groups = pd.cut(candidate.original_duration, [0, HOUR, DAY, 7*DAY, np.inf], labels=GROUPS)
    group_rows = []
    for name in ["ALL", *GROUPS]:
        mask = cohorts["all"] if name == "ALL" else groups == name
        group_rows.append({"group": name, "jobs": int(mask.sum()),
                           "wait_hours_mean": float(wait[mask].mean()/HOUR) if mask.any() else None,
                           **_slowdown_summary(slowdown[mask])})

    cohort_rows, model_rows = [], []
    for name, mask in cohorts.items():
        count = int(mask.sum())
        work = float(original_work[mask].sum())
        durations = candidate.loc[mask, "original_duration"]/HOUR
        quantiles = {f"original_duration_hours_{label}": float(durations.quantile(q, interpolation="linear")) if count else None
                     for label, q in QUANTILES.items()}
        cohort_rows.append({"cohort": name, "jobs": count, "job_fraction": count/len(candidate),
                            "original_gpu_work_hours": work, "original_gpu_work_fraction": work/total_work,
                            **quantiles})
        for model in sorted(candidate.requested_model.unique()):
            selected = mask & (candidate.requested_model == model)
            model_count = int(selected.sum())
            model_work = float(original_work[selected].sum())
            model_rows.append({"cohort": name, "requested_model": model, "jobs": model_count,
                               "fraction_of_cohort_jobs": model_count/count if count else None,
                               "original_gpu_work_hours": model_work,
                               "fraction_of_cohort_work": model_work/work if work else None})

    result["metric_definitions"] = {
        "population": "All submitted jobs followed through completion; no fixed-window censoring",
        "pairing": "Exact, unique, nonnull text job_name sets; original attributes must match after alignment",
        "harm": "(candidate start-submit) - (FCFS start-submit) > 0 exactly; no epsilon",
        "harm_mean": "Mean positive wait increment conditional on strict harm; null when no job is harmed",
        "original_gpu_work": "original_duration_seconds * gpu_request_per_worker * worker_num / 3600",
        "harm_work_fraction": "Original GPU work of harmed jobs divided by original GPU work of all jobs",
        "tail_thresholds": "Strict wait > 1000 hours and strict wait > FCFS's exact maximum wait",
        "duration_quantiles": "Original requested runtime in hours; linear interpolation; empty cohorts null",
        "duration_groups": "Original duration: (0,1h], (1h,1d], (1d,7d], (7d,infinity)",
        "bounded_slowdown": "max(1,(end-submit)/max(run_duration_seconds,60)); unitless",
        "quantiles": "Linear interpolation as in pandas Series.quantile; no rounding before comparisons",
    }
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(group_rows).to_csv(output / "tail_by_duration_group.csv", index=False)
    pd.DataFrame(cohort_rows).to_csv(output / "tail_cohorts.csv", index=False)
    pd.DataFrame(model_rows).to_csv(output / "tail_model_distribution.csv", index=False)
    (output / "tail_metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule", type=Path, required=True)
    parser.add_argument("--fcfs", type=Path, default=ROOT / "outputs/question2/fcfs/schedule.csv")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    candidate = pd.read_csv(args.schedule, dtype={"job_name": str})
    baseline = pd.read_csv(args.fcfs, dtype={"job_name": str})
    result = analyze_tail(candidate, baseline, args.output)
    print(json.dumps({key: value for key, value in result.items() if key != "metric_definitions"}, indent=2))


if __name__ == "__main__":
    main()
