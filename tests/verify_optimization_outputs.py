"""Delivery checks, including full-trace aging-zero equivalence to saved SJF."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import time

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from scheduler import run_simulation


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    data = ROOT / "2026_MCM_Senior_GPU_Scheduling/2026_MCM_Senior_GPU_Scheduling"
    failures = []
    manifests = list((ROOT / "outputs/question2/aging_sweep").glob("*/scenario.json"))
    manifests += [ROOT / "outputs/question2/aging_best/scenario.json"]
    manifests += list((ROOT / "outputs/question3/improved").glob("*/scenario.json"))
    simulated = []
    for path in manifests:
        meta = json.loads(path.read_text(encoding="utf-8"))
        for name, expected in meta["source_sha256"].items():
            if digest(data / name) != expected:
                failures.append(f"Source hash mismatch: {path.parent.name}/{name}")
        for name, expected in meta["code_sha256"].items():
            if digest(ROOT / "src" / name) != expected:
                failures.append(f"Code hash mismatch: {path.parent.name}/{name}")
        metric = json.loads((path.parent / "metrics.json").read_text(encoding="utf-8"))
        if sum(metric[f"{state}_at_90d"] for state in ("completed", "running", "waiting")) != metric["jobs"]:
            failures.append(f"90-day population mismatch: {path.parent.name}")
        pools = pd.read_csv(path.parent / "metrics_by_assigned_model.csv")
        for resource in ("gpu", "cpu"):
            rates = pools[f"{resource}_allocation_rate_90d"]
            if not rates.between(-1e-12, 1+1e-12).all():
                failures.append(f"Invalid {resource} allocation rate: {path.parent.name}")
        if "simulation_wall_seconds" in meta:
            simulated.append({"scenario": path.parent.name, "seconds": meta["simulation_wall_seconds"]})
    jobs = pd.read_csv(data / "job_info_clean.csv", dtype={"job_name": str})
    nodes = pd.read_csv(data / "node_info_df.csv", dtype={"node_name": str})
    started = time.perf_counter()
    schedule, allocations, _ = run_simulation(jobs, nodes, policy="aging", aging_alpha=0)
    old_dir = ROOT / "outputs/question2/sjf"
    old_schedule = pd.read_csv(old_dir / "schedule.csv", dtype={"job_name": str}, float_precision="round_trip")
    old_allocations = pd.read_csv(old_dir / "allocations.csv", dtype={"job_name": str, "node_name": str}, float_precision="round_trip")
    zero_passed = True
    try:
        pd.testing.assert_frame_equal(schedule, old_schedule, check_dtype=False, check_exact=True)
        pd.testing.assert_frame_equal(allocations, old_allocations, check_dtype=False, check_exact=True)
    except AssertionError as error:
        zero_passed = False
        failures.append(f"Full-trace aging alpha=0 differs from SJF: {error}")
    result = {"passed": not failures, "failure_count": len(failures), "failures": failures,
              "new_manifests_checked": len(manifests), "aging_zero_full_trace_exact": zero_passed,
              "aging_zero_comparison_cells": int(schedule.size+allocations.size),
              "aging_zero_check_wall_seconds": time.perf_counter()-started,
              "max_simulation_seconds": max(row["seconds"] for row in simulated),
              "simulations_above_60_seconds": [row for row in simulated if row["seconds"] >= 60],
              "code_sha256": {"scheduler.py": digest(ROOT / "src/scheduler.py")}}
    (ROOT / "outputs/optimization_delivery_review.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
