"""Verify v2 default compatibility without rewriting historical artifacts.

All 19 historical full scenarios are compared with their original CSVs. The
previous 12 linear-aging and 15 Q3 configurations also run under both the saved
pre-v2 core and current core, including metric-only cases, entirely in memory.
Every schedule field and per-node worker allocation is compared exactly.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "2026_MCM_Senior_GPU_Scheduling/2026_MCM_Senior_GPU_Scheduling"
REFERENCE_SHA256 = "81058052fcf18c2f6a15e2251fef7892fcdffcda3ebbc615e03e2a607167d36b"
FULL_SCENARIOS = (
    "question2/fcfs", "question2/sjf", "question2/backfill", "question2/backfill_l512",
    "question3/flex25", "question3/flex50", "question3/flex100", "question3/speed2",
    "question3/cap10", "question3/cap50", "question3/seed18", "question3/seed19",
    "question3/backfill_flex50", "question2/aging_best",
    "question3/improved/q3_arrival_old", "question3/improved/q3_pull_aa",
    "question3/improved/q3_pull_ahs", "question3/improved/q3_pull_all",
    "question3/improved/q3_cap10",
)
AGING_NAMES = ("0", "0.001", "0.003", "0.01", "0.03", "0.1", "0.3", "1", "3", "10", "30", "100")
Q3_NAMES = (
    "q3_arrival_old", "q3_pull_aa", "q3_pull_ahs", "q3_pull_all", "q3_p25", "q3_p100",
    "q3_cap10", "q3_cap50", "q3_tau1h", "q3_tau6h", "q3_speed2", "q3_seed18",
    "q3_seed19", "q3_noanon", "q3_sjf_bridge",
)
REPLAY_SCENARIOS = tuple(f"question2/aging_sweep/alpha_{a}" for a in AGING_NAMES) + tuple(
    f"question3/improved/{name}" for name in Q3_NAMES)
ALL_SCENARIOS = tuple(dict.fromkeys(FULL_SCENARIOS + REPLAY_SCENARIOS))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact_hashes(directory: Path) -> dict[str, str]:
    """Protect manifests, metrics, full CSVs, and existing scenario audits."""
    return {path.name: sha256(path) for path in sorted(directory.iterdir()) if path.is_file()}


def load_core(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load scheduler from {path}")
    module = importlib.util.module_from_spec(spec)
    # Dataclass annotation resolution requires the module to be registered.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def configuration(manifest: dict, module) -> dict:
    values = manifest.get("routing")
    if values is not None:
        values = dict(values)
        values["donors"] = tuple(values["donors"])
        if "donor_caps" in values:
            values["donor_caps"] = tuple(tuple(pair) for pair in values["donor_caps"])
        routing = module.RoutingConfig(**values)
    else:
        routing = None
    return {"policy": manifest["policy"], "aging_alpha": manifest.get("aging_alpha", 0),
            "max_backfill_scan": int(manifest.get("max_backfill_scan", 128)), "routing": routing}


def compare_frame(reference: pd.DataFrame, current: pd.DataFrame, keys: list[str]) -> dict:
    """Exact values after stable identifier sorting; no tolerance or rounding."""
    result = {"passed": False, "reference_rows": len(reference), "current_rows": len(current),
              "reference_columns": reference.columns.tolist(), "current_columns": current.columns.tolist(),
              "failure_count": 0, "compared_cells": 0, "mismatched_cells": 0,
              "mismatches_by_column": {}, "examples": []}
    if reference.columns.tolist() != current.columns.tolist():
        result.update(error="Column schemas differ", failure_count=1)
        return result
    if len(reference) != len(current):
        result.update(error="Row counts differ", failure_count=abs(len(reference) - len(current)))
        return result
    reference = reference.sort_values(keys, kind="stable").reset_index(drop=True)
    current = current.sort_values(keys, kind="stable").reset_index(drop=True)
    different = reference.ne(current) & ~(reference.isna() & current.isna())
    counts = different.sum()
    result["mismatches_by_column"] = {name: int(count) for name, count in counts.items() if count}
    result["mismatched_cells"] = result["failure_count"] = int(counts.sum())
    result["compared_cells"] = int(reference.size)
    for row in different.index[different.any(axis=1)][:5]:
        columns = different.columns[different.loc[row]]
        result["examples"].append({
            "row_identifiers": {key: str(reference.loc[row, key]) for key in keys},
            "differences": {column: {"reference": str(reference.loc[row, column]),
                                     "current": str(current.loc[row, column])} for column in columns},
        })
    result["passed"] = result["failure_count"] == 0
    return result


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def verify(outputs_root: Path, data_dir: Path, summary_path: Path, scenarios: tuple[str, ...]) -> dict:
    started = time.perf_counter()
    core_paths = {"current": ROOT / "src/scheduler.py",
                  "previous": outputs_root / "v2_reference/source/scheduler.py"}
    core_before = {name: sha256(path) for name, path in core_paths.items()}
    if core_before["previous"] != REFERENCE_SHA256:
        raise ValueError("Saved pre-v2 scheduler hash does not match the registered reference")
    current_core = load_core(core_paths["current"], "v2_regression_current_scheduler")
    previous_core = load_core(core_paths["previous"], "v2_regression_previous_scheduler")
    source_paths = [data_dir / "job_info_clean.csv", data_dir / "node_info_df.csv"]
    source_before = {path.name: sha256(path) for path in source_paths}
    before_hashes = {name: artifact_hashes(outputs_root / name) for name in scenarios}
    jobs = pd.read_csv(source_paths[0], dtype={"job_name": str, "gpu_model": str})
    nodes = pd.read_csv(source_paths[1], dtype={"node_name": str, "gpu_model": str})
    report = {
        "passed": False, "completed": False, "all_requested": scenarios == ALL_SCENARIOS,
        "expected_scenarios": len(scenarios),
        "expected_saved_csv_comparisons": sum(name in FULL_SCENARIOS for name in scenarios),
        "expected_previous_core_comparisons": sum(name in REPLAY_SCENARIOS for name in scenarios),
        "source_sha256_before": source_before, "scheduler_sha256_before": core_before,
        "verifier_sha256": sha256(Path(__file__)), "failure_count": 0,
        "method": "Exact comparison of every schedule field and sorted per-node worker allocation. The 19 full historical cases use unchanged CSVs; all 12 prior aging and 15 prior Q3 configurations also compare current and saved pre-v2 cores in memory.",
        "csv_reading": "Identifiers are strings; stored floats use pandas float_precision='round_trip'.",
        "results": [],
    }
    write_report(summary_path, report)
    for name in scenarios:
        directory = outputs_root / name
        record = {"scenario": name, "passed": False, "failure_count": 0,
                  "compare_saved_csv": name in FULL_SCENARIOS,
                  "compare_previous_core": name in REPLAY_SCENARIOS,
                  "artifact_sha256_before": before_hashes[name]}
        scenario_clock = time.perf_counter()
        current_schedule = current_allocations = previous_schedule = previous_allocations = None
        stored_schedule = stored_allocations = None
        try:
            manifest = json.loads((directory / "scenario.json").read_text(encoding="utf-8-sig"))
            if manifest["source_sha256"] != source_before:
                raise ValueError("Current data hashes differ from the historical manifest")
            if record["compare_previous_core"] and manifest["code_sha256"]["scheduler.py"] != REFERENCE_SHA256:
                raise ValueError("Historical configuration does not belong to the saved pre-v2 core")
            record["configuration"] = {key: manifest.get(key) for key in
                                       ("policy", "aging_alpha", "max_backfill_scan", "routing", "schedule_storage")}
            clock = time.perf_counter()
            current_schedule, current_allocations, diagnostics = current_core.run_simulation(
                jobs, nodes, **configuration(manifest, current_core))
            record["current_simulation_wall_seconds"] = time.perf_counter() - clock
            record["diagnostics"] = {key: diagnostics[key] for key in
                                     ("jobs_scheduled", "allocation_rows", "event_count",
                                      "backfilled_jobs", "migrated_jobs", "simulated_until")}
            if record["compare_saved_csv"]:
                stored_schedule = pd.read_csv(directory / "schedule.csv", dtype={"job_name": str},
                                              float_precision="round_trip")
                stored_allocations = pd.read_csv(directory / "allocations.csv",
                                                 dtype={"job_name": str, "node_name": str},
                                                 float_precision="round_trip")
                record["stored_schedule_comparison"] = compare_frame(stored_schedule, current_schedule, ["job_name"])
                record["stored_allocation_comparison"] = compare_frame(
                    stored_allocations, current_allocations, ["job_name", "node_name", "worker_count"])
                stored_schedule = stored_allocations = None
            if record["compare_previous_core"]:
                clock = time.perf_counter()
                previous_schedule, previous_allocations, _ = previous_core.run_simulation(
                    jobs, nodes, **configuration(manifest, previous_core))
                record["previous_simulation_wall_seconds"] = time.perf_counter() - clock
                record["snapshot_schedule_comparison"] = compare_frame(previous_schedule, current_schedule, ["job_name"])
                record["snapshot_allocation_comparison"] = compare_frame(
                    previous_allocations, current_allocations, ["job_name", "node_name", "worker_count"])
            record["failure_count"] += sum(value["failure_count"] for key, value in record.items()
                                            if key.endswith("_comparison"))
        except Exception as error:
            record["error"] = f"{type(error).__name__}: {error}"
            record["failure_count"] += 1
        finally:
            del current_schedule, current_allocations, previous_schedule, previous_allocations
            del stored_schedule, stored_allocations
        try:
            record["artifact_sha256_after"] = artifact_hashes(directory)
            record["historical_artifacts_unchanged"] = record["artifact_sha256_after"] == before_hashes[name]
            if not record["historical_artifacts_unchanged"]:
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
                          "current_simulation_wall_seconds": record.get("current_simulation_wall_seconds"),
                          "error": record.get("error")}), flush=True)

    report["scheduler_sha256_after"] = {name: sha256(path) for name, path in core_paths.items()}
    report["source_sha256_after"] = {path.name: sha256(path) for path in source_paths}
    report["cores_unchanged_during_run"] = report["scheduler_sha256_after"] == core_before
    report["sources_unchanged"] = report["source_sha256_after"] == source_before
    report["historical_artifact_hash_errors"] = [name for name in scenarios
        if artifact_hashes(outputs_root / name) != before_hashes[name]]
    report["failure_count"] += int(not report["cores_unchanged_during_run"]) + int(not report["sources_unchanged"])
    report["failure_count"] += len(report["historical_artifact_hash_errors"])
    report["completed_scenarios"] = len(report["results"])
    for prefix in ("stored", "snapshot"):
        for kind in ("schedule", "allocation"):
            key = f"{prefix}_{kind}_comparison"
            report[f"{prefix}_{kind}_cells"] = sum(result.get(key, {}).get("compared_cells", 0)
                                                    for result in report["results"])
    report["total_compared_cells"] = sum(report[f"{prefix}_{kind}_cells"]
                                          for prefix in ("stored", "snapshot") for kind in ("schedule", "allocation"))
    report["total_wall_seconds"] = time.perf_counter() - started
    report["completed"] = True
    report["passed"] = bool(scenarios) and report["failure_count"] == 0
    write_report(summary_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outputs-root", type=Path, default=ROOT / "outputs")
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--summary-path", type=Path, help="Default: outputs/v2_regression.json")
    parser.add_argument("--scenarios", nargs="+", choices=ALL_SCENARIOS,
                        help="Optional subset; omission covers all 19 full plus 27 replay cases")
    args = parser.parse_args()
    scenarios = tuple(dict.fromkeys(args.scenarios)) if args.scenarios else ALL_SCENARIOS
    summary_path = args.summary_path or args.outputs_root / "v2_regression.json"
    report = verify(args.outputs_root, args.data_dir, summary_path, scenarios)
    print(json.dumps({"passed": report["passed"], "scenarios": report["completed_scenarios"],
                      "failure_count": report["failure_count"], "compared_cells": report["total_compared_cells"],
                      "total_wall_seconds": report["total_wall_seconds"],
                      "report": str(summary_path.resolve())}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
