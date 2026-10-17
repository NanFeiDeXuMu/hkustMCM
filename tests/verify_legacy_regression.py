"""Re-run the 13 legacy scenarios and compare outputs without overwriting them.

Every schedule field and every per-node worker allocation is compared exactly,
after sorting by stable identifiers. Only legacy_regression.json is written.
The stored CSVs and manifests are SHA-256 checked before and after the run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "2026_MCM_Senior_GPU_Scheduling/2026_MCM_Senior_GPU_Scheduling"
sys.path.insert(0, str(ROOT / "src"))
from scheduler import RoutingConfig, run_simulation

LEGACY_SCENARIOS = (
    "question2/fcfs", "question2/sjf", "question2/backfill", "question2/backfill_l512",
    "question3/flex25", "question3/flex50", "question3/flex100", "question3/speed2",
    "question3/cap10", "question3/cap50", "question3/seed18", "question3/seed19",
    "question3/backfill_flex50",
)
ARTIFACTS = ("scenario.json", "schedule.csv", "allocations.csv")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact_hashes(directory: Path) -> dict[str, str]:
    return {name: sha256(directory / name) for name in ARTIFACTS}


def compare_frame(stored: pd.DataFrame, fresh: pd.DataFrame, keys: list[str]) -> dict:
    """Exact value equality; CSV numeric values are read with round-trip floats."""
    result = {"passed": False, "stored_rows": len(stored), "fresh_rows": len(fresh),
              "stored_columns": stored.columns.tolist(), "fresh_columns": fresh.columns.tolist(),
              "mismatched_cells": 0, "mismatches_by_column": {}, "examples": []}
    if stored.columns.tolist() != fresh.columns.tolist():
        result["error"] = "Column schemas differ"
        result["failure_count"] = 1
        return result
    if len(stored) != len(fresh):
        result["error"] = "Row counts differ"
        result["failure_count"] = abs(len(stored) - len(fresh))
        return result
    stored = stored.sort_values(keys, kind="stable").reset_index(drop=True)
    fresh = fresh.sort_values(keys, kind="stable").reset_index(drop=True)
    different = stored.ne(fresh) & ~(stored.isna() & fresh.isna())
    counts = different.sum()
    result["mismatches_by_column"] = {name: int(count) for name, count in counts.items() if count}
    result["mismatched_cells"] = int(counts.sum())
    result["failure_count"] = result["mismatched_cells"]
    result["compared_cells"] = int(stored.size)
    for row in different.index[different.any(axis=1)][:5]:
        columns = different.columns[different.loc[row]]
        result["examples"].append({
            "row_identifiers": {key: str(stored.loc[row, key]) for key in keys},
            "differences": {column: {"stored": str(stored.loc[row, column]),
                                     "fresh": str(fresh.loc[row, column])} for column in columns},
        })
    result["passed"] = result["failure_count"] == 0
    return result


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def verify_legacy(outputs_root: Path, data_dir: Path, summary_path: Path,
                  scenarios: tuple[str, ...]) -> dict:
    started = time.perf_counter()
    scheduler_path = ROOT / "src/scheduler.py"
    core_before = sha256(scheduler_path)
    source_paths = [data_dir / "job_info_clean.csv", data_dir / "node_info_df.csv"]
    source_before = {path.name: sha256(path) for path in source_paths}
    before_hashes = {}
    for name in scenarios:
        try:
            before_hashes[name] = artifact_hashes(outputs_root / name)
        except OSError as error:
            before_hashes[name] = {"error": str(error)}
    jobs = pd.read_csv(source_paths[0], dtype={"job_name": str, "gpu_model": str})
    nodes = pd.read_csv(source_paths[1], dtype={"node_name": str, "gpu_model": str})
    report = {
        "passed": False, "completed": False, "expected_scenarios": len(scenarios),
        "all_legacy_scenarios_requested": scenarios == LEGACY_SCENARIOS,
        "source_sha256_before": source_before, "scheduler_sha256_before": core_before,
        "verifier_sha256": sha256(Path(__file__)), "failure_count": 0,
        "method": "Re-run current core using each unchanged legacy manifest; exact equality of every schedule field and sorted per-node worker allocations; no tolerances.",
        "csv_reading": "Identifiers are strings; stored floats use pandas float_precision='round_trip'.",
        "results": [],
    }
    write_report(summary_path, report)
    for name in scenarios:
        directory = outputs_root / name
        record = {"scenario": name, "passed": False, "failure_count": 0,
                  "artifact_sha256_before": before_hashes[name]}
        scenario_clock = time.perf_counter()
        try:
            if "error" in before_hashes[name]:
                raise FileNotFoundError(before_hashes[name]["error"])
            manifest = json.loads((directory / "scenario.json").read_text(encoding="utf-8-sig"))
            if manifest["source_sha256"] != source_before:
                raise ValueError("Current source files do not match the legacy manifest hashes")
            if manifest["policy"] not in {"fcfs", "sjf", "backfill"}:
                raise ValueError("Selected manifest is not a legacy policy")
            routing_values = manifest.get("routing")
            if routing_values:
                routing_values = dict(routing_values)
                routing_values["donors"] = tuple(routing_values["donors"])
                if routing_values.get("mode", "arrival") != "arrival":
                    raise ValueError("Legacy routing must use arrival mode")
                routing = RoutingConfig(**routing_values)
            else:
                routing = None
            simulation_clock = time.perf_counter()
            schedule, allocations, diagnostics = run_simulation(
                jobs, nodes, manifest["policy"], routing=routing,
                max_backfill_scan=int(manifest.get("max_backfill_scan", 128)),
            )
            record["simulation_wall_seconds"] = time.perf_counter() - simulation_clock
            record["old_scheduler_sha256"] = manifest.get("code_sha256", {}).get("scheduler.py")
            record["policy"] = manifest["policy"]
            record["max_backfill_scan"] = manifest.get("max_backfill_scan", 128)
            record["routing"] = routing_values
            stored_schedule = pd.read_csv(directory / "schedule.csv", dtype={"job_name": str},
                                          float_precision="round_trip")
            stored_allocations = pd.read_csv(directory / "allocations.csv",
                                             dtype={"job_name": str, "node_name": str},
                                             float_precision="round_trip")
            record["schedule_comparison"] = compare_frame(stored_schedule, schedule, ["job_name"])
            record["allocation_comparison"] = compare_frame(
                stored_allocations, allocations, ["job_name", "node_name", "worker_count"])
            record["failure_count"] += record["schedule_comparison"]["failure_count"]
            record["failure_count"] += record["allocation_comparison"]["failure_count"]
            record["required_fields"] = ["job_name", "start_time", "end_time", "assigned_model"]
            record["diagnostics"] = {key: diagnostics[key] for key in
                                     ("jobs_scheduled", "allocation_rows", "event_count",
                                      "backfilled_jobs", "migrated_jobs", "simulated_until")}
            # Drop large frame references before moving to the next scenario.
            del schedule, allocations, stored_schedule, stored_allocations
        except Exception as error:
            record["error"] = f"{type(error).__name__}: {error}"
            record["failure_count"] += 1
        try:
            record["artifact_sha256_after"] = artifact_hashes(directory)
            record["legacy_artifacts_unchanged"] = record["artifact_sha256_after"] == before_hashes[name]
            if not record["legacy_artifacts_unchanged"]:
                record["failure_count"] += 1
        except OSError as error:
            record["hash_check_error"] = str(error)
            record["failure_count"] += 1
        record["passed"] = record["failure_count"] == 0
        record["scenario_wall_seconds"] = time.perf_counter() - scenario_clock
        report["results"].append(record)
        report["failure_count"] += record["failure_count"]
        write_report(summary_path, report)
        print(json.dumps({"scenario": name, "passed": record["passed"],
                          "failure_count": record["failure_count"],
                          "simulation_wall_seconds": record.get("simulation_wall_seconds"),
                          "error": record.get("error")}), flush=True)

    report["scheduler_sha256_after"] = sha256(scheduler_path)
    report["source_sha256_after"] = {path.name: sha256(path) for path in source_paths}
    report["scheduler_unchanged_during_run"] = report["scheduler_sha256_after"] == core_before
    report["sources_unchanged"] = report["source_sha256_after"] == source_before
    final_hash_errors = []
    for name in scenarios:
        try:
            if artifact_hashes(outputs_root / name) != before_hashes[name]:
                final_hash_errors.append(name)
        except OSError:
            final_hash_errors.append(name)
    report["final_legacy_artifact_hash_errors"] = final_hash_errors
    report["failure_count"] += (not report["scheduler_unchanged_during_run"]) + (not report["sources_unchanged"])
    report["failure_count"] += len(final_hash_errors)
    report["completed_scenarios"] = len(report["results"])
    report["compared_schedule_cells"] = sum(result.get("schedule_comparison", {}).get("compared_cells", 0)
                                             for result in report["results"])
    report["compared_allocation_cells"] = sum(result.get("allocation_comparison", {}).get("compared_cells", 0)
                                               for result in report["results"])
    report["total_wall_seconds"] = time.perf_counter() - started
    report["completed"] = True
    report["passed"] = bool(scenarios) and report["failure_count"] == 0
    write_report(summary_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outputs-root", type=Path, default=ROOT / "outputs")
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--summary-path", type=Path, help="Default: outputs/legacy_regression.json")
    parser.add_argument("--scenarios", nargs="+", choices=LEGACY_SCENARIOS,
                        help="Optional subset; omission verifies all 13 legacy scenarios")
    args = parser.parse_args()
    scenarios = tuple(args.scenarios) if args.scenarios else LEGACY_SCENARIOS
    summary_path = args.summary_path or args.outputs_root / "legacy_regression.json"
    report = verify_legacy(args.outputs_root, args.data_dir, summary_path, scenarios)
    print(json.dumps({"passed": report["passed"], "scenarios": report["completed_scenarios"],
                      "failure_count": report["failure_count"],
                      "total_wall_seconds": report["total_wall_seconds"],
                      "report": str(summary_path.resolve())}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
