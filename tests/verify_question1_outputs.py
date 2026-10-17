"""Independently verify Q1 outputs with CSV, Decimal, and an event sweep.

This verifier does not import the analysis implementation. Source data is read
only; the sole generated file is outputs/question1/independent_validation.json.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "2026_MCM_Senior_GPU_Scheduling/2026_MCM_Senior_GPU_Scheduling"
DAY = Decimal(86400)
ZERO = Decimal(0)
RESOURCES = ("gpu", "cpu")
DURATION_BINS = (
    (Decimal(60), "(0,1 min]"),
    (Decimal(600), "(1,10 min]"),
    (Decimal(3600), "(10 min,1 h]"),
    (DAY, "(1 h,1 d]"),
    (7 * DAY, "(1,7 d]"),
    (Decimal("Infinity"), ">7 d"),
)
GPU_SIZE_BINS = (
    (Decimal(1), "1"),
    (Decimal(4), "2-4"),
    (Decimal(8), "5-8"),
    (Decimal(32), "9-32"),
    (Decimal("Infinity"), ">32"),
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Comparisons:
    """Collect every failure instead of stopping at the first mismatch."""

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.failures: list[dict] = []

    def numeric(self, section: str, name: str, expected, actual, exact=False) -> None:
        self.counts[section] += 1
        expected_decimal = Decimal(str(expected))
        actual_decimal = Decimal(str(actual))
        # Derived CSVs use 12 significant digits. Decimal accumulation of the
        # raw data stays exact; only comparisons to rounded exports use tolerance.
        valid = expected_decimal.is_finite() and actual_decimal.is_finite()
        if exact:
            valid = valid and expected_decimal == actual_decimal
        else:
            tolerance = max(Decimal("1e-7"), abs(expected_decimal) * Decimal("1e-10"))
            valid = valid and abs(expected_decimal - actual_decimal) <= tolerance
        if not valid:
            self.failures.append({"section": section, "name": name,
                                  "expected": str(expected), "actual": str(actual)})

    def text(self, section: str, name: str, expected: str, actual: str) -> None:
        self.counts[section] += 1
        if expected != actual:
            self.failures.append({"section": section, "name": name,
                                  "expected": expected, "actual": actual})


def verify(data_dir: Path, output_dir: Path) -> dict:
    checks = Comparisons()
    source_files = {"jobs": data_dir / "job_info_clean.csv",
                    "nodes": data_dir / "node_info_df.csv"}
    jobs = read_csv(source_files["jobs"])
    nodes = read_csv(source_files["nodes"])
    table_dir = output_dir / "tables"
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    provenance = json.loads((output_dir / "provenance.json").read_text(encoding="utf-8"))
    validation = json.loads((output_dir / "validation.json").read_text(encoding="utf-8"))
    model_table = {row["gpu_model"]: row for row in read_csv(table_dir / "model_summary.csv")}
    daily_table = {(row["gpu_model"], int(row["day_index"])): row
                   for row in read_csv(table_dir / "offered_load_daily.csv")}
    sources = {key: {"file": path.name, "sha256": sha256(path)}
               for key, path in source_files.items()}
    for key, source in sources.items():
        checks.text("provenance", key + " source hash", source["sha256"],
                    provenance["sources"][key]["sha256"])

    capacities = defaultdict(lambda: [ZERO, ZERO])
    node_counts: Counter[str] = Counter()
    for node in nodes:
        model = node["gpu_model"]
        capacities[model][0] += Decimal(node["gpu_capacity_num"])
        capacities[model][1] += Decimal(node["cpu_num"])
        node_counts[model] += 1
    days = int(max(Decimal(job["submit_time"]) for job in jobs) // DAY) + 1
    horizon = days * DAY
    checks.numeric("window", "days", days, summary["window_days"], exact=True)
    checks.numeric("window", "seconds", horizon, summary["window_seconds"], exact=True)
    checks.numeric("window", "provenance days", days, provenance["window_days"], exact=True)

    events = defaultdict(lambda: defaultdict(lambda: [ZERO, ZERO]))
    direct_work = defaultdict(lambda: [ZERO, ZERO])
    model_job_counts: Counter[str] = Counter()
    bands = {"duration_band": defaultdict(lambda: [0, ZERO, ZERO]),
             "gpu_size_band": defaultdict(lambda: [0, ZERO, ZERO])}
    fractional_cpu = 0
    zero_cpu = 0
    integer_gpu_work = True
    for job in jobs:
        model = job["gpu_model"]
        start, duration, workers, gpu, cpu = (
            Decimal(job[field]) for field in
            ("submit_time", "duration", "worker_num", "gpu_request", "cpu_request")
        )
        gpu *= workers
        fractional_cpu += cpu != cpu.to_integral_value()
        zero_cpu += cpu == 0
        cpu *= workers
        work = [gpu * duration, cpu * duration]
        integer_gpu_work = integer_gpu_work and work[0] == work[0].to_integral_value()
        model_job_counts[model] += 1
        for index, request in enumerate((gpu, cpu)):
            events[model][start][index] += request
            events[model][start + duration][index] -= request
            direct_work[model][index] += work[index]
        for name, value, boundaries in (("duration_band", duration, DURATION_BINS),
                                        ("gpu_size_band", gpu, GPU_SIZE_BINS)):
            label = next(label for upper, label in boundaries if value <= upper)
            bucket = bands[name][label]
            bucket[0] += 1
            bucket[1] += work[0]
            bucket[2] += work[1]

    checks.numeric("totals", "jobs", len(jobs), summary["jobs"], exact=True)
    checks.numeric("totals", "nodes", len(nodes), summary["nodes"], exact=True)
    checks.numeric("totals", "GPUs", sum(c[0] for c in capacities.values()),
                   summary["physical_gpus"], exact=True)
    checks.numeric("totals", "CPUs", sum(c[1] for c in capacities.values()),
                   summary["vcpus"], exact=True)
    checks.numeric("fractional_requests", "fractional CPU requests", fractional_cpu,
                   validation["fractional_cpu_request_jobs"], exact=True)
    checks.numeric("fractional_requests", "zero CPU requests", zero_cpu,
                   validation["zero_cpu_request_jobs"], exact=True)
    totals = [sum(work[index] for work in direct_work.values()) for index in range(2)]
    for index, resource in enumerate(RESOURCES):
        checks.numeric("totals", resource + " full hours", totals[index] / 3600,
                       summary[f"total_{resource}_hours"])

    diagnostics = {}
    for model, changes in events.items():
        changes[ZERO]
        changes[horizon]
        times = sorted(changes)
        load = [ZERO, ZERO]
        full_work = [ZERO, ZERO]
        window_work = [ZERO, ZERO]
        peak = [ZERO, ZERO]
        over_seconds = [ZERO, ZERO]
        daily_work = [[ZERO, ZERO] for _ in range(days)]
        for start, end in zip(times[:-1], times[1:]):
            for index, resource in enumerate(RESOURCES):
                load[index] += changes[start][index]
                if load[index] < 0:
                    raise AssertionError(f"Negative {resource} event load for {model}")
                full_work[index] += load[index] * (end - start)
                if start >= horizon:
                    continue
                clipped_end = min(end, horizon)
                window_work[index] += load[index] * (clipped_end - start)
                peak[index] = max(peak[index], load[index])
                if load[index] > capacities[model][index]:
                    over_seconds[index] += clipped_end - start
                cursor = start
                while cursor < clipped_end:
                    day = int(cursor // DAY)
                    next_boundary = min(clipped_end, (day + 1) * DAY)
                    daily_work[day][index] += load[index] * (next_boundary - cursor)
                    cursor = next_boundary

        row = model_table[model]
        checks.numeric("models", model + " jobs", model_job_counts[model], row["jobs"], exact=True)
        checks.numeric("models", model + " nodes", node_counts[model], row["nodes"], exact=True)
        diagnostics[model] = {}
        for index, resource in enumerate(RESOURCES):
            capacity = capacities[model][index]
            checks.numeric("event_invariants", model + " final " + resource + " load",
                           0, load[index] + changes[times[-1]][index], exact=True)
            checks.numeric("event_invariants", model + " full " + resource + " integral",
                           direct_work[model][index], full_work[index], exact=True)
            checks.numeric("models", model + " " + resource + " capacity", capacity,
                           row["gpus" if resource == "gpu" else "vcpus"], exact=True)
            expected = {
                f"{resource}_hours": full_work[index] / 3600,
                f"{resource}_work_in_window_hours": window_work[index] / 3600,
                f"{resource}_work_after_window_hours": (full_work[index] - window_work[index]) / 3600,
                f"peak_{resource}_demand_to_capacity": peak[index] / capacity,
                f"fraction_window_{resource}_over_capacity": over_seconds[index] / horizon,
                f"{resource}_immediate_window_mean_to_capacity": window_work[index] / horizon / capacity,
            }
            for field, value in expected.items():
                checks.numeric("event_metrics", model + " " + field, value, row[field])
            for day in range(days):
                daily_row = daily_table[(model, day)]
                checks.numeric("daily_integrals", f"{model} {resource} day {day}",
                               daily_work[day][index] / 3600, daily_row[f"{resource}_hours"])
                checks.numeric("daily_integrals", f"{model} {resource} ratio day {day}",
                               daily_work[day][index] / DAY / capacity,
                               daily_row[f"mean_{resource}_demand_to_capacity"])
            diagnostics[model][resource] = {
                "full_work_seconds": str(full_work[index]),
                "window_work_seconds": str(window_work[index]),
                "peak_demand_units": str(peak[index]),
                "peak_demand_to_capacity": str(peak[index] / capacity),
                "over_capacity_seconds": str(over_seconds[index]),
                "over_capacity_fraction": str(over_seconds[index] / horizon),
            }

    for name, calculated in bands.items():
        exported = read_csv(table_dir / f"{name}.csv")
        checks.numeric("bands", name + " number of labels", len(calculated), len(exported), exact=True)
        for row in exported:
            label = row[name]
            expected = calculated[label]
            checks.numeric("bands", name + " " + label + " jobs", expected[0], row["jobs"], exact=True)
            for index, resource in enumerate(RESOURCES):
                checks.numeric("bands", name + " " + label + " " + resource,
                               expected[index + 1] / 3600, row[f"{resource}_hours"])
            checks.numeric("bands", name + " " + label + " job share",
                           Decimal(expected[0]) / len(jobs), row["job_share"])
            checks.numeric("bands", name + " " + label + " GPU work share",
                           expected[1] / totals[0], row["gpu_work_share"])

    return {
        "passed": not checks.failures,
        "method": "Independent stdlib CSV and Decimal event sweep; no imports from src",
        "sources": sources,
        "verifier_sha256": sha256(Path(__file__)),
        "window": {"days": days, "seconds": str(horizon), "interval": "[0, days * 86400)"},
        "comparison_tolerance": {"relative": "1e-10", "absolute": "1e-7",
                                 "counts_and_event_reconciliations": "exact"},
        "comparison_count": sum(checks.counts.values()),
        "comparisons_by_section": dict(checks.counts),
        "failures": checks.failures,
        "exact_totals": {"gpu_work_seconds": str(totals[0]), "cpu_work_seconds": str(totals[1]),
                         "all_gpu_work_values_integral": integer_gpu_work,
                         "fractional_cpu_request_jobs": fractional_cpu, "zero_cpu_request_jobs": zero_cpu},
        "event_diagnostics": diagnostics,
        "interpretation": "Unconstrained immediate-start demand; not utilization or a feasible schedule.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/question1")
    args = parser.parse_args()
    try:
        report = verify(args.data_dir, args.output_dir)
    except Exception as error:
        report = {"passed": False, "error_type": type(error).__name__, "error": str(error),
                  "verifier_sha256": sha256(Path(__file__))}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result_path = args.output_dir / "independent_validation.json"
    result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                           encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "comparison_count": report.get("comparison_count", 0),
                      "failures": report.get("failures", []), "error": report.get("error"),
                      "report": str(result_path.resolve())}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
