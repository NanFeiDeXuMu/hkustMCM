"""Predeclared aging and pull-routing experiments; preserves legacy outputs."""
from __future__ import annotations

from dataclasses import asdict, replace
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
ALPHAS = [0, .001, .003, .01, .03, .1, .3, 1, 3, 10, 30, 100]
A100, A800, H800, SERIES2 = "A100-SXM4-80GB", "A800-SXM4-80GB", "H800", "GPU-series-2"
MAIN = {"q3_arrival_old", "q3_pull_aa", "q3_pull_ahs", "q3_pull_all"}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def select_aging(table: pd.DataFrame, fcfs: dict) -> dict:
    """D018, fixed before any sweep: FCFS bounds, otherwise Pareto compromise."""
    fields = ["wait_hours_mean", "wait_hours_max", "last_completion_days"]
    feasible = table[(table.wait_hours_max <= fcfs["wait_hours_max"]) &
                     (table.last_completion_days <= fcfs["last_completion_days"])]
    if len(feasible):
        selected = feasible.sort_values(["wait_hours_mean", "alpha"]).iloc[0]
        method = "minimum_mean_subject_to_fcfs_bounds"
        distance = None
    else:
        values = table[fields].to_numpy(float)
        keep = [not np.any(np.all(values <= row, axis=1) & np.any(values < row, axis=1)) for row in values]
        spans = np.ptp(values, axis=0)
        scaled = (values - values.min(axis=0)) / np.where(spans > 0, spans, 1)
        candidates = table.assign(ideal_distance=np.linalg.norm(scaled, axis=1)).loc[keep]
        selected = candidates.sort_values(["ideal_distance", "alpha"]).iloc[0]
        method = "pareto_normalized_ideal_distance_compromise"
        distance = float(selected.ideal_distance)
    return {"selected_alpha": float(selected.alpha), "method": method,
            "fcfs_bounds_feasible_count": len(feasible), "ideal_distance": distance,
            "fcfs_max_wait_bound_hours": fcfs["wait_hours_max"],
            "fcfs_last_completion_bound_days": fcfs["last_completion_days"],
            "selection_scope": "Same-trace tuning; not out-of-sample validation", "rule": "DECISIONS.md D018"}


def select_routing(table: pd.DataFrame, *, named_only=False) -> str:
    feasible = table[table.management_feasible & table.selection_eligible & (table.a10_mean_wait_gain_hours > 0)]
    if named_only:
        feasible = feasible[~feasible.includes_anonymous_donor]
    if feasible.empty:
        return "q3_base"
    return str(feasible.sort_values(["a10_mean_wait_gain_hours", "scenario"], ascending=[False, True]).iloc[0].scenario)


def routing_scenarios(alpha):
    center = RoutingConfig(mode="pull", donors=(A100, H800, SERIES2))
    definitions = {
        "q3_arrival_old": RoutingConfig(),
        "q3_pull_aa": replace(center, donors=(A100, A800)),
        "q3_pull_ahs": center,
        "q3_pull_all": replace(center, donors=(A100, H800, SERIES2, A800), donor_caps=((A800, .1),)),
        "q3_p25": replace(center, flexible_fraction=.25),
        "q3_p100": replace(center, flexible_fraction=1),
        "q3_cap10": replace(center, donor_cap_fraction=.1),
        "q3_cap50": replace(center, donor_cap_fraction=.5),
        "q3_tau1h": replace(center, min_wait_seconds=3600),
        "q3_tau6h": replace(center, min_wait_seconds=21600),
        "q3_speed2": replace(center, speedup=2),
        "q3_seed18": replace(center, seed=20261018),
        "q3_seed19": replace(center, seed=20261019),
        "q3_noanon": replace(center, donors=(A100, H800)),
        "q3_sjf_bridge": center,
    }
    return {name: {"policy": "sjf" if name == "q3_sjf_bridge" else "aging",
                   "aging_alpha": 0 if name == "q3_sjf_bridge" else alpha, "routing": config}
            for name, config in definitions.items()}


class Experiment:
    def __init__(self, args):
        from run_scheduling_experiments import digest
        self.args = args
        self.jobs = pd.read_csv(DATA / "job_info_clean.csv", dtype={"job_name": str})
        self.nodes = pd.read_csv(DATA / "node_info_df.csv", dtype={"node_name": str})
        self.provenance = {
            "source_sha256": {name: digest(DATA / name) for name in ("job_info_clean.csv", "node_info_df.csv")},
            "code_sha256": {name: digest(ROOT / "src" / name) for name in
                            ("scheduler.py", "evaluate_scheduling.py", "run_scheduling_experiments.py", "run_optimization_experiments.py")},
            "versions": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__},
        }

    def run(self, directory, name, *, policy="aging", aging_alpha=0., routing=None, key=False, baseline=None, baseline_name=None, need_schedule=False):
        directory.mkdir(parents=True, exist_ok=True)
        full = self.args.save_schedules == "all" or (key and self.args.save_schedules == "key")
        manifest = {"name": name, "policy": policy, "aging_alpha": aging_alpha, "max_backfill_scan": 128,
                    "routing": asdict(routing) if routing else None, "baseline": baseline_name,
                    **self.provenance}
        path = directory / "scenario.json"
        if path.exists() and not self.args.force:
            prior = read_json(path)
            normalized = json.loads(json.dumps(manifest))
            matches = all(prior.get(k) == v for k, v in normalized.items())
            has_full = prior.get("schedule_storage") == "full" and (directory / "schedule.csv").exists() and (directory / "allocations.csv").exists()
            if matches and (directory / "metrics.json").exists() and (baseline is None or (directory / "paired_wait_changes.csv").exists()) and (not (full or need_schedule) or has_full):
                print(f"Reuse {name} ({prior['schedule_storage']})", flush=True)
                schedule = pd.read_csv(directory / "schedule.csv", dtype={"job_name": str}) if need_schedule else None
                return read_json(directory / "metrics.json"), schedule
        print(f"Start {name}", flush=True)
        started = time.perf_counter()
        schedule, allocations, diagnostics = run_simulation(self.jobs, self.nodes, policy=policy, aging_alpha=aging_alpha, routing=routing)
        elapsed = time.perf_counter() - started
        # A later metrics-only invocation never deletes an existing complete result.
        full = full or (directory / "schedule.csv").exists() or (directory / "allocations.csv").exists()
        if full:
            schedule.to_csv(directory / "schedule.csv", index=False)
            allocations.to_csv(directory / "allocations.csv", index=False)
        metric = evaluate(schedule, self.nodes, directory)
        if baseline is not None:
            paired_comparison(baseline, schedule, directory)
        manifest.update(schedule_storage="full" if full else "metrics_only", simulation_wall_seconds=elapsed,
                        diagnostics=diagnostics, simulation_under_60_seconds=elapsed < 60)
        write_json(path, manifest)
        print(json.dumps({"scenario": name, "seconds": round(elapsed, 3), "mean_wait_h": metric["wait_hours_mean"],
                          "max_wait_h": metric["wait_hours_max"], "last_day": metric["last_completion_days"],
                          "migrated": metric["migrated_jobs"], "storage": manifest["schedule_storage"]}), flush=True)
        return metric, schedule if need_schedule else None

    def legacy_diagnostics(self):
        for name in ("fcfs", "sjf", "backfill", "backfill_l512"):
            old = ROOT / "outputs/question2" / name
            new = ROOT / "outputs/question2/legacy_diagnostics" / name
            new.mkdir(parents=True, exist_ok=True)
            schedule = pd.read_csv(old / "schedule.csv", dtype={"job_name": str})
            evaluate(schedule, self.nodes, new)
            write_json(new / "scenario.json", {"name": name, "schedule_storage": "metrics_only", "derived_from": str(old.relative_to(ROOT)), **self.provenance})

    def aging(self):
        directory = ROOT / "outputs/question2"
        rows = []
        for alpha in ALPHAS:
            name = f"alpha_{alpha:g}"
            metric, _ = self.run(directory / "aging_sweep" / name, name, aging_alpha=alpha)
            meta = read_json(directory / "aging_sweep" / name / "scenario.json")
            rows.append({"alpha": alpha, "simulation_wall_seconds": meta["simulation_wall_seconds"],
                         **{k: v for k, v in metric.items() if not isinstance(v, dict)}})
        table = pd.DataFrame(rows)
        table.to_csv(directory / "aging_sweep/sweep.csv", index=False)
        selected = select_aging(table, read_json(directory / "fcfs/metrics.json"))
        alpha = selected["selected_alpha"]
        index = ALPHAS.index(alpha)
        selected["neighbor_alphas"] = ALPHAS[max(0, index-1):min(len(ALPHAS), index+2)]
        write_json(directory / "aging_selection.json", selected)
        table[table.alpha.isin(selected["neighbor_alphas"])].to_csv(directory / "aging_sweep/neighbor_sensitivity.csv", index=False)
        print(json.dumps(selected), flush=True)
        self.run(directory / "aging_best", "aging_best", aging_alpha=alpha, key=True)
        return alpha

    def routing(self, alpha):
        directory = ROOT / "outputs/question3/improved"
        directory.mkdir(parents=True, exist_ok=True)
        _, baseline = self.run(ROOT / "outputs/question2/aging_best", "aging_best", aging_alpha=alpha, key=True, need_schedule=True)
        definitions = routing_scenarios(alpha)
        # The no-routing row is a lightweight view of the same selected baseline.
        base_dir = directory / "q3_base"
        base_dir.mkdir(exist_ok=True)
        base_metric = evaluate(baseline, self.nodes, base_dir)
        paired_comparison(baseline, baseline, base_dir)
        write_json(base_dir / "scenario.json", {"name": "q3_base", "schedule_storage": "metrics_only",
                   "derived_from": "outputs/question2/aging_best", "policy": "aging", "aging_alpha": alpha, "routing": None, **self.provenance})
        rows = [{"scenario": "q3_base", "policy": "aging", "baseline": "aging_best", "selection_eligible": False,
                 "includes_anonymous_donor": False, "management_feasible": True, "a10_mean_wait_gain_hours": 0.,
                 "max_donor_mean_wait_delta_hours": 0., "max_donor_p95_delta_hours": 0.,
                 **{k: v for k, v in base_metric.items() if not isinstance(v, dict)}}]
        for name, config in definitions.items():
            bridge = name == "q3_sjf_bridge"
            base = pd.read_csv(ROOT / "outputs/question2/sjf/schedule.csv", dtype={"job_name": str}) if bridge else baseline
            base_name = "sjf" if bridge else "aging_best"
            metric, _ = self.run(directory / name, name, **config, key=name in MAIN, baseline=base, baseline_name=base_name)
            paired = pd.read_csv(directory / name / "paired_wait_changes.csv").set_index("requested_model")
            route = config["routing"]
            native = paired.loc[list(route.donors)]
            mean_cost, tail_cost = float(native.wait_delta_mean_hours.max()), float(native.wait_p95_delta_hours.max())
            meta = read_json(directory / name / "scenario.json")
            rows.append({"scenario": name, "policy": config["policy"], "baseline": base_name,
                         "selection_eligible": name not in {"q3_speed2", "q3_seed18", "q3_seed19", "q3_sjf_bridge"},
                         "includes_anonymous_donor": SERIES2 in route.donors,
                         "management_feasible": mean_cost <= .5 and tail_cost <= 2,
                         "a10_mean_wait_gain_hours": -float(paired.loc["A10", "wait_delta_mean_hours"]),
                         "max_donor_mean_wait_delta_hours": mean_cost, "max_donor_p95_delta_hours": tail_cost,
                         "simulation_wall_seconds": meta["simulation_wall_seconds"],
                         **{k: v for k, v in metric.items() if not isinstance(v, dict)}})
        table = pd.DataFrame(rows)
        table.to_csv(directory / "comparison.csv", index=False)
        chosen, named = select_routing(table), select_routing(table, named_only=True)
        selection = {"selected_scenario": chosen, "named_only_selected_scenario": named, "aging_alpha": alpha,
                     "max_native_mean_increase_hours": .5, "max_native_p95_increase_hours": 2,
                     "rule": "DECISIONS.md D019", "anonymous_compatibility_is_assumed": True}
        write_json(directory / "selection.json", selection)
        for name in sorted({chosen, named} - MAIN - {"q3_base"}):
            self.run(directory / name, name, **definitions[name], key=True, baseline=baseline, baseline_name="aging_best")
        print(json.dumps(selection), flush=True)


def main(args):
    experiment = Experiment(args)
    if args.scenarios:
        raise ValueError("Optimization suites use the predeclared complete grid; --scenarios is for legacy suites")
    if args.suite in {"aging", "optimization"}:
        experiment.legacy_diagnostics()
        alpha = experiment.aging()
    else:
        alpha = read_json(ROOT / "outputs/question2/aging_selection.json")["selected_alpha"]
    if args.suite in {"routing", "optimization"}:
        experiment.routing(alpha)
