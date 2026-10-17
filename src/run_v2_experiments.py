"""D023/D024 experiments: grouped fairness, log aging and native-harm controls.

Historical outputs are read only. Scans save metrics; only named key results
save per-job schedules and per-node allocations.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import time

import numpy as np
import pandas as pd

from analyze_tail import analyze_tail
from evaluate_scheduling import evaluate, paired_comparison
from scheduler import RoutingConfig, run_simulation

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "2026_MCM_Senior_GPU_Scheduling/2026_MCM_Senior_GPU_Scheduling"
Q2, Q3 = ROOT / "outputs/question2", ROOT / "outputs/question3/v2"
ALPHAS = [0, .001, .003, .01, .03, .1, .3, 1, 3, 10, 30, 100]
TAUS = [3600, 21600, 86400, 259200, 604800, 2592000]
GROUPS = [("le1h", "<=1h"), ("1h_1d", "1h-1d"), ("1d_7d", "1d-7d"), ("gt7d", ">7d")]
A100, H800, A800, S2 = "A100-SXM4-80GB", "H800", "A800-SXM4-80GB", "GPU-series-2"
DEADLINE = datetime(2026, 10, 18, 4, tzinfo=timezone.utc)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def flat(metric):
    return {key: value for key, value in metric.items() if not isinstance(value, (dict, list))}


def frame_digest(frame):
    return hashlib.sha256(frame.to_csv(index=False).encode("utf-8")).hexdigest()


def select_q2(table: pd.DataFrame, fcfs_groups: dict, sjf_mean: float) -> dict:
    checks = []
    for row in table.to_dict("records"):
        groups = {label: {"candidate_mean_hours": row[f"group_{key}_mean_wait_hours"],
                          "fcfs_mean_hours": fcfs_groups[label],
                          "passed": row[f"group_{key}_mean_wait_hours"] <= fcfs_groups[label]}
                  for key, label in GROUPS}
        c1 = all(group["passed"] for group in groups.values())
        c2 = row["wait_hours_mean"] <= 2 * sjf_mean
        checks.append({"scenario": row["scenario"], "groups": groups, "c1_passed": c1, "c2_passed": c2,
                       "mean_wait_hours": row["wait_hours_mean"], "mean_bound_hours": 2 * sjf_mean,
                       "max_wait_hours": row["wait_hours_max"], "feasible": c1 and c2})
    feasible = [check["scenario"] for check in checks if check["feasible"]]
    candidates = table[table.scenario.isin(feasible)]
    chosen = "sjf" if candidates.empty else str(candidates.sort_values(["wait_hours_max", "wait_hours_mean", "scenario"]).iloc[0].scenario)
    return {"selected_scenario": chosen, "fallback": candidates.empty, "feasible_candidates": feasible,
            "c1_c2_checks": checks, "c2_mean_bound_hours": 2 * sjf_mean, "rule": "DECISIONS.md D023",
            "scope": "Same-trace in-sample tuning; maximum and P99 reported separately"}


def select_q3(table: pd.DataFrame) -> dict:
    eligible = table[table.selection_eligible & table.management_feasible & (table.a10_mean_wait_gain_hours > 0)]
    def choose(subset):
        return "v2_base" if subset.empty else str(subset.sort_values(["a10_mean_wait_gain_hours", "scenario"], ascending=[False, True]).iloc[0].scenario)
    return {"primary_selected_scenario": choose(eligible[~eligible.includes_anonymous_donor]),
            "conditional_selected_scenario": choose(eligible[eligible.includes_anonymous_donor]),
            "rule": "DECISIONS.md D024", "native_mean_increase_bound_hours": .5,
            "native_p95_increase_bound_hours": 2., "scope": "Main grid only; sensitivities do not reselect"}


def main_routing_configs():
    ah = RoutingConfig(mode="pull", donors=(A100, H800))
    ahs = replace(ah, donors=(A100, H800, S2))
    return {
        "v2_ahs": ahs, "v2_ah": ah,
        "v2_ah_d1": replace(ah, max_routed_duration_seconds=86400),
        "v2_ah_d7": replace(ah, max_routed_duration_seconds=7*86400),
        "v2_ah_r10": replace(ah, donor_reserve_fraction=.1),
        "v2_ah_r20": replace(ah, donor_reserve_fraction=.2),
        "v2_ah_d7_r10": replace(ah, max_routed_duration_seconds=7*86400, donor_reserve_fraction=.1),
        "v2_ah_d1_cap50": replace(ah, max_routed_duration_seconds=86400, donor_cap_fraction=.5),
        "v2_ahs_d1": replace(ahs, max_routed_duration_seconds=86400),
        "v2_ahs_d7_r10": replace(ahs, max_routed_duration_seconds=7*86400, donor_reserve_fraction=.1),
        "v2_aha_d7_r10": replace(ah, donors=(A100, H800, A800), max_routed_duration_seconds=7*86400,
                                 donor_reserve_fraction=.1, donor_caps=((A800, .1),)),
    }


class ExperimentV2:
    def __init__(self, args):
        self.args = args
        self.jobs = pd.read_csv(DATA / "job_info_clean.csv", dtype={"job_name": str}, float_precision="round_trip")
        self.nodes = pd.read_csv(DATA / "node_info_df.csv", dtype={"node_name": str}, float_precision="round_trip")
        self.fcfs = self.read_schedule(Q2 / "fcfs")
        self.fcfs_sha = digest(Q2 / "fcfs/schedule.csv")
        self.provenance = {
            "source_sha256": {name: digest(DATA / name) for name in ("job_info_clean.csv", "node_info_df.csv")},
            "code_sha256": {name: digest(ROOT / "src" / name) for name in
                            ("scheduler.py", "evaluate_scheduling.py", "analyze_tail.py", "run_v2_experiments.py")},
            "versions": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__},
        }

    @staticmethod
    def read_schedule(directory):
        return pd.read_csv(directory / "schedule.csv", dtype={"job_name": str}, float_precision="round_trip")

    def run(self, directory, name, config, *, key=False, baseline=None, baseline_name=None,
            baseline_sha=None, need_schedule=False, historical=None, tail=False):
        directory.mkdir(parents=True, exist_ok=True)
        route = config.get("routing")
        manifest = {"name": name, "policy": config["policy"], "aging_alpha": config.get("aging_alpha", 0.),
                    "log_aging_tau_seconds": config.get("log_aging_tau_seconds", 86400.),
                    "max_backfill_scan": config.get("max_backfill_scan", 128),
                    "routing": asdict(route) if route else None, "baseline": baseline_name,
                    "baseline_schedule_sha256": baseline_sha, "tail_fcfs_schedule_sha256": self.fcfs_sha if tail else None,
                    "experiment_version": "v2", **self.provenance}
        if historical:
            manifest.update(derived_from=str(historical.relative_to(ROOT)), historical_schedule_sha256=digest(historical / "schedule.csv"))
        path = directory / "scenario.json"
        if path.exists() and not self.args.force:
            prior = read_json(path)
            normalized = json.loads(json.dumps(manifest))
            has_full = prior.get("schedule_storage") == "full" and (directory / "schedule.csv").is_file() and (directory / "allocations.csv").is_file()
            ready = (directory / "metrics.json").exists() and (not tail or (directory / "tail_metrics.json").exists())
            ready = ready and (baseline is None or (directory / "paired_wait_changes.csv").exists())
            if ready and all(prior.get(k) == v for k, v in normalized.items()) and (not (key or need_schedule) or has_full):
                print(f"Reuse {name} ({prior['schedule_storage']})", flush=True)
                return read_json(directory / "metrics.json"), self.read_schedule(directory) if need_schedule else None
        allocations, diagnostics, elapsed = None, None, None
        if historical:
            schedule = self.read_schedule(historical)
            if key:
                allocations = pd.read_csv(historical / "allocations.csv", dtype={"job_name": str, "node_name": str})
        else:
            if datetime.now(timezone.utc) >= DEADLINE:
                raise TimeoutError("Experiment deadline reached; preserve partial results and prepare report")
            print(f"Start {name}", flush=True)
            started = time.perf_counter()
            schedule, allocations, diagnostics = run_simulation(self.jobs, self.nodes, **config)
            elapsed = time.perf_counter() - started
        full = key or (directory / "schedule.csv").exists()
        if full:
            if allocations is None:
                raise ValueError("Cannot save a complete schedule without allocations")
            schedule.to_csv(directory / "schedule.csv", index=False)
            allocations.to_csv(directory / "allocations.csv", index=False)
        metric = evaluate(schedule, self.nodes, directory)
        if tail:
            tail_metric = analyze_tail(schedule, self.fcfs, directory)
            metric.update({k: v for k, v in tail_metric.items() if k != "metric_definitions"})
            metric["metric_definitions"]["tail_analysis"] = "See tail_metrics.json; FCFS paired baseline and 60-second bounded slowdown"
        if route:
            a10 = schedule[schedule.requested_model == "A10"]
            eligible = a10.flexible.astype(bool)
            if route.max_routed_duration_seconds is not None and route.mode == "pull":
                eligible &= a10.original_duration <= route.max_routed_duration_seconds
            metric["a10_duration_eligible_jobs"] = int(eligible.sum())
            metric["a10_duration_eligible_job_fraction"] = float(eligible.mean())
            work = a10.gpu_request * a10.worker_num * a10.original_duration
            metric["a10_duration_eligible_original_work_fraction"] = float(work[eligible].sum() / work.sum())
            metric["metric_definitions"]["duration_eligible"] = "Compatible A10 jobs passing the original-duration filter; independent of whether they queue, become old enough, fit or actually migrate"
        write_json(directory / "metrics.json", metric)
        if baseline is not None:
            paired_comparison(baseline, schedule, directory)
        manifest.update(schedule_storage="full" if full else "metrics_only", simulation_wall_seconds=elapsed,
                        diagnostics=diagnostics, simulation_origin="historical_full_csv" if historical else "current_core")
        write_json(path, manifest)
        print(json.dumps({"scenario": name, "seconds": None if elapsed is None else round(elapsed, 3),
                          "mean_wait_h": metric["wait_hours_mean"], "max_wait_h": metric["wait_hours_max"],
                          "migrated": metric["migrated_jobs"], "storage": manifest["schedule_storage"]}), flush=True)
        return metric, schedule if need_schedule else None

    def q2(self):
        definitions = {
            "fcfs": {"policy": "fcfs"}, "sjf": {"policy": "sjf"},
            "backfill": {"policy": "backfill"}, "backfill_l512": {"policy": "backfill", "max_backfill_scan": 512},
            **{f"alpha_{alpha:g}": {"policy": "aging", "aging_alpha": alpha} for alpha in ALPHAS},
            **{f"tau_{tau}": {"policy": "log_aging", "log_aging_tau_seconds": tau} for tau in TAUS},
        }
        rows = []
        for name, config in definitions.items():
            is_log = config["policy"] == "log_aging"
            directory = Q2 / ("log_aging_sweep" if is_log else "v2_candidates") / name
            historical = Q2 / name if name in {"fcfs", "sjf", "backfill", "backfill_l512"} else None
            metric, _ = self.run(directory, name, config, historical=historical, tail=True)
            row = {"scenario": name, "policy": config["policy"], "alpha": config.get("aging_alpha"),
                   "tau_seconds": config.get("log_aging_tau_seconds"), "output_path": directory.relative_to(ROOT).as_posix(), **flat(metric)}
            groups = pd.read_csv(directory / "tail_by_duration_group.csv").set_index("group")
            for key, label in GROUPS:
                row[f"group_{key}_mean_wait_hours"] = float(groups.loc[label, "wait_hours_mean"])
            rows.append(row)
        table = pd.DataFrame(rows)
        fcfs_groups = {label: float(table.set_index("scenario").loc["fcfs", f"group_{key}_mean_wait_hours"]) for key, label in GROUPS}
        selected = select_q2(table, fcfs_groups, float(table.set_index("scenario").loc["sjf", "wait_hours_mean"]))
        legacy_only = select_q2(table[table.policy != "log_aging"], fcfs_groups, float(table.set_index("scenario").loc["sjf", "wait_hours_mean"]))
        selected["legacy_candidates_only_selected"] = legacy_only["selected_scenario"]
        if legacy_only["selected_scenario"] != "alpha_1":
            raise AssertionError("Legacy-only D023 result differs from alpha_1; investigate before selecting")
        for key in ("c1_passed", "c2_passed", "feasible"):
            lookup = {check["scenario"]: check[key] for check in selected["c1_c2_checks"]}
            table[key] = table.scenario.map(lookup)
        table.to_csv(Q2 / "comparison_v2.csv", index=False)
        table[table.policy == "log_aging"].to_csv(Q2 / "log_aging_sweep/sweep.csv", index=False)
        chosen = selected["selected_scenario"]
        selected.update(selected_config=definitions[chosen], selected_metrics=flat(rows[list(definitions).index(chosen)]))
        write_json(Q2 / "selection_v2.json", selected)
        print(json.dumps({"selection_v2": chosen, "feasible": selected["feasible_candidates"]}), flush=True)
        self.run(Q2 / "q2_star", "q2_star", definitions[chosen], key=True, tail=True)
        return definitions[chosen]

    def q3(self, base_config):
        Q3.mkdir(parents=True, exist_ok=True)
        _, baseline = self.run(Q2 / "q2_star", "q2_star", base_config, key=True, tail=True, need_schedule=True)
        base_sha = frame_digest(baseline)
        base_dir = Q3 / "v2_base"
        base_dir.mkdir(exist_ok=True)
        base_metric = evaluate(baseline, self.nodes, base_dir)
        paired_comparison(baseline, baseline, base_dir)
        write_json(base_dir / "scenario.json", {"name": "v2_base", "experiment_version": "v2", "schedule_storage": "metrics_only",
                    "derived_from": "outputs/question2/q2_star", "policy": base_config["policy"],
                    "routing": None, "baseline_schedule_sha256": base_sha, **self.provenance})
        rows = [{"scenario": "v2_base", "policy": base_config["policy"], "baseline": "q2_star", "phase": "baseline",
                 "selection_eligible": False, "includes_anonymous_donor": False, "management_feasible": True,
                 "a10_mean_wait_gain_hours": 0., "max_donor_mean_wait_delta_hours": 0., "max_donor_p95_delta_hours": 0., **flat(base_metric)}]
        definitions = main_routing_configs()

        def run_route(name, route, config, base, baseline_name, phase, key=False):
            metric, _ = self.run(Q3 / name, name, {**config, "routing": route}, key=key, baseline=base,
                                 baseline_name=baseline_name, baseline_sha=frame_digest(base))
            paired = pd.read_csv(Q3 / name / "paired_wait_changes.csv").set_index("requested_model")
            native = paired.loc[list(route.donors)]
            mean_cost, p95_cost = float(native.wait_delta_mean_hours.max()), float(native.wait_p95_delta_hours.max())
            return {"scenario": name, "policy": config["policy"], "baseline": baseline_name, "phase": phase,
                    "selection_eligible": phase == "main", "includes_anonymous_donor": S2 in route.donors,
                    "management_feasible": mean_cost <= .5 and p95_cost <= 2,
                    "a10_mean_wait_gain_hours": -float(paired.loc["A10", "wait_delta_mean_hours"]),
                    "max_donor_mean_wait_delta_hours": mean_cost, "max_donor_p95_delta_hours": p95_cost,
                    "donors": ",".join(route.donors), "max_routed_duration_seconds": route.max_routed_duration_seconds,
                    "donor_reserve_fraction": route.donor_reserve_fraction, "donor_cap_fraction": route.donor_cap_fraction,
                    "flexible_fraction": route.flexible_fraction, "speedup": route.speedup, "seed": route.seed,
                    "min_wait_seconds": route.min_wait_seconds, **flat(metric)}

        for name, route in definitions.items():
            rows.append(run_route(name, route, base_config, baseline, "q2_star", "main", key=name in {"v2_ah", "v2_ahs"}))
        table = pd.DataFrame(rows)
        selected = select_q3(table)
        selected["q2_star_config"] = base_config
        primary, conditional = selected["primary_selected_scenario"], selected["conditional_selected_scenario"]
        for name in sorted({primary, conditional} - {"v2_base", "v2_ah", "v2_ahs"}):
            run_route(name, definitions[name], base_config, baseline, "q2_star", "main", key=True)
        if primary != "v2_base":
            anchor = primary
        else:
            named = table[(table.phase == "main") & ~table.includes_anonymous_donor]
            anchor = str(named.sort_values(["a10_mean_wait_gain_hours", "scenario"], ascending=[False, True]).iloc[0].scenario)
        selected["sensitivity_anchor"] = anchor
        selected["sensitivity_anchor_is_recommendation"] = primary != "v2_base"
        central = definitions[anchor]
        sensitivities = {"v2_p25": replace(central, flexible_fraction=.25), "v2_p100": replace(central, flexible_fraction=1),
                         "v2_seed18": replace(central, seed=20261018), "v2_seed19": replace(central, seed=20261019),
                         "v2_speed2": replace(central, speedup=2), "v2_tau1h": replace(central, min_wait_seconds=3600)}
        for name, route in sensitivities.items():
            rows.append(run_route(name, route, base_config, baseline, "q2_star", "sensitivity"))
        sjf = self.read_schedule(Q2 / "sjf")
        bridge = run_route("v2_sjf_bridge", central, {"policy": "sjf"}, sjf, "sjf", "sensitivity")
        rows.append(bridge)
        selected["sjf_bridge_management_feasible"] = bridge["management_feasible"]
        selected["primary_and_sjf_bridge_both_feasible"] = primary != "v2_base" and bridge["management_feasible"]
        pd.DataFrame(rows).to_csv(Q3 / "comparison.csv", index=False)
        write_json(Q3 / "selection.json", selected)
        print(json.dumps(selected), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=["q2", "q3", "all"], default="all")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    experiment = ExperimentV2(args)
    config = experiment.q2() if args.suite in {"q2", "all"} else read_json(Q2 / "selection_v2.json")["selected_config"]
    if args.suite in {"q3", "all"}:
        experiment.q3(config)


if __name__ == "__main__":
    main()
