"""Reproducible full-trace experiments for Questions 2 and 3."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import platform
import time

import numpy as np
import pandas as pd

from evaluate_scheduling import evaluate, paired_comparison
from scheduler import RoutingConfig, run_simulation

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "2026_MCM_Senior_GPU_Scheduling/2026_MCM_Senior_GPU_Scheduling"


def scenarios() -> dict:
    configurations = {
        "fcfs": {"suite": "question2", "policy": "fcfs", "lookahead": 128, "routing": None},
        "sjf": {"suite": "question2", "policy": "sjf", "lookahead": 128, "routing": None},
        "backfill": {"suite": "question2", "policy": "backfill", "lookahead": 128, "routing": None},
        "backfill_l512": {"suite": "question2", "policy": "backfill", "lookahead": 512, "routing": None},
    }
    grid = [
        ("flex25", .25, 1, .25, 20261017),
        ("flex50", .5, 1, .25, 20261017),
        ("flex100", 1, 1, .25, 20261017),
        ("speed2", .5, 2, .25, 20261017),
        ("cap10", .5, 1, .1, 20261017),
        ("cap50", .5, 1, .5, 20261017),
        ("seed18", .5, 1, .25, 20261018),
        ("seed19", .5, 1, .25, 20261019),
    ]
    for name, fraction, speed, cap, seed in grid:
        configurations[name] = {"suite": "question3", "policy": "sjf", "lookahead": 128,
                                "routing": RoutingConfig(flexible_fraction=fraction, speedup=speed,
                                                         donor_cap_fraction=cap, seed=seed)}
    configurations["backfill_flex50"] = {"suite": "question3", "policy": "backfill", "lookahead": 128,
                                        "routing": RoutingConfig(flexible_fraction=.5, speedup=1,
                                                                 donor_cap_fraction=.25, seed=20261017)}
    return configurations


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=["question2", "question3", "all", "aging", "routing", "optimization"], default="optimization")
    parser.add_argument("--save-schedules", choices=["key", "all", "none"], default="key",
                        help="New optimization suites: save key/all/no new per-job artifacts; keep existing full outputs")
    parser.add_argument("--scenarios", nargs="+")
    parser.add_argument("--force", action="store_true", help="Recompute existing outputs")
    args = parser.parse_args()
    if args.suite in {"aging", "routing", "optimization"}:
        from run_optimization_experiments import main as optimize
        optimize(args)
        return
    definitions = scenarios()
    selected = args.scenarios or [name for name, config in definitions.items() if args.suite == "all" or config["suite"] == args.suite]
    if any(name not in definitions for name in selected):
        parser.error("Unknown scenario name")
    jobs = pd.read_csv(DATA / "job_info_clean.csv", dtype={"job_name": str})
    nodes = pd.read_csv(DATA / "node_info_df.csv", dtype={"node_name": str})
    baselines = {}
    source_hashes = {name: digest(DATA / name) for name in ("job_info_clean.csv", "node_info_df.csv")}
    code_hashes = {name: digest(ROOT / "src" / name) for name in ("scheduler.py", "evaluate_scheduling.py", "run_scheduling_experiments.py")}
    for name in selected:
        config = definitions[name]
        destination = ROOT / "outputs" / config["suite"] / name
        destination.mkdir(parents=True, exist_ok=True)
        manifest_path = destination / "scenario.json"
        manifest = {"name": name, "suite": config["suite"], "policy": config["policy"],
                    "max_backfill_scan": config["lookahead"], "routing": asdict(config["routing"]) if config["routing"] else None,
                    "source_sha256": source_hashes, "code_sha256": code_hashes,
                    "versions": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__}}
        reusable = False
        if manifest_path.exists() and (destination / "metrics.json").exists() and not args.force:
            prior = json.loads(manifest_path.read_text(encoding="utf-8"))
            normalized = json.loads(json.dumps(manifest))
            reusable = all(prior.get(key) == normalized[key] for key in normalized)
        if reusable:
            print(f"Reuse verified configuration: {name}", flush=True)
            schedule = pd.read_csv(destination / "schedule.csv", dtype={"job_name": str})
        else:
            print(f"Start {name}: {len(jobs):,} jobs", flush=True)
            started = time.perf_counter()
            schedule, allocations, diagnostics = run_simulation(jobs, nodes, policy=config["policy"],
                                                                routing=config["routing"], max_backfill_scan=config["lookahead"])
            elapsed = time.perf_counter() - started
            # The default shortest round-trip representation preserves simple
            # fractional requests (e.g. 0.1), whereas %.17g exposes binary tails.
            schedule.to_csv(destination / "schedule.csv", index=False)
            allocations.to_csv(destination / "allocations.csv", index=False)
            manifest["simulation_wall_seconds"] = elapsed
            manifest["diagnostics"] = diagnostics
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            metrics = evaluate(schedule, nodes, destination)
            print(json.dumps({"scenario": name, "seconds": round(elapsed, 3), "mean_wait_h": metrics["wait_hours_mean"],
                              "p95_wait_h": metrics["wait_hours_p95"], "completed90": metrics["completed_at_90d"],
                              "migrated": metrics["migrated_jobs"]}), flush=True)
        if name in ("backfill", "sjf"):
            baselines[name] = schedule
        if config["suite"] == "question3":
            base_name = config["policy"]
            base_path = ROOT / "outputs/question2" / base_name / "schedule.csv"
            if base_name not in baselines and base_path.exists():
                baselines[base_name] = pd.read_csv(base_path, dtype={"job_name": str})
            if base_name not in baselines:
                raise RuntimeError(f"Run question2/{base_name} before Q3 for paired comparisons")
            paired_comparison(baselines[base_name], schedule, destination)
    for suite in ("question2", "question3"):
        rows = []
        for name, config in definitions.items():
            directory = ROOT / "outputs" / suite / name
            if config["suite"] != suite or not (directory / "metrics.json").exists():
                continue
            metric = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
            meta = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
            row = {"scenario": name, "policy": config["policy"], "max_backfill_scan": config["lookahead"],
                   "simulation_wall_seconds": meta["simulation_wall_seconds"]}
            row.update({key: value for key, value in metric.items() if not isinstance(value, dict)})
            if meta["routing"]:
                row.update({key: value for key, value in meta["routing"].items() if key != "donors"})
            rows.append(row)
        if rows:
            pd.DataFrame(rows).to_csv(ROOT / "outputs" / suite / "comparison.csv", index=False)


if __name__ == "__main__":
    main()
