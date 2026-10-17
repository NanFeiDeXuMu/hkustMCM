"""Final predeclared routing frontier, compensation ledger and Q2 curve points.

The scheduling core and all historical artifacts are read-only. D026 fixes the
candidate sets and thresholds before running; sensitivities never reselect.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import platform
import time

import numpy as np
import pandas as pd

from analyze_routing_frontier import analyze_routing_frontier
from analyze_tail import analyze_tail
from evaluate_scheduling import evaluate, paired_comparison
from run_v2_experiments import A100, H800, S2, GROUPS, digest, flat, read_json, write_json
from scheduler import RoutingConfig, run_simulation

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "2026_MCM_Senior_GPU_Scheduling/2026_MCM_Senior_GPU_Scheduling"
Q2, OLD, OUT = ROOT / "outputs/question2", ROOT / "outputs/question3/v2", ROOT / "outputs/question3/v3"
CORE_SHA = "714e94bdc388abd63a9274e67c394157eb6e06bfb5a6ffba0a8a0320acb10966"
THRESHOLDS = {"strict_mean_hours": .5, "strict_p95_hours": 2.,
              "compensation_exchange_ratio": 100., "compensation_mean_hours": 1.,
              "compensation_affected_fraction_per_donor": .2}


def routing_from_manifest(value):
    value = dict(value)
    value["donors"] = tuple(value["donors"])
    value["donor_caps"] = tuple(tuple(item) for item in value.get("donor_caps", ()))
    return RoutingConfig(**value)


def main_configs():
    ah = RoutingConfig(mode="pull", donors=(A100, H800))
    configs = {f"v3_ah_d{d}": replace(ah, max_routed_duration_seconds=d*86400) for d in (14, 30, 60)}
    configs.update({f"v3_ah_d{d}_t1h": replace(ah, max_routed_duration_seconds=d*86400, min_wait_seconds=3600)
                    for d in (7, 14, 30, 60)})
    configs.update({"v3_ah_t1h": replace(ah, min_wait_seconds=3600),
                    "v3_ah_cap10": replace(ah, donor_cap_fraction=.1),
                    "v3_ah_cap15": replace(ah, donor_cap_fraction=.15),
                    "v3_ha_d30": replace(ah, donors=(H800, A100), max_routed_duration_seconds=30*86400),
                    "v3_ha": replace(ah, donors=(H800, A100))})
    return configs


def classify(table):
    table = table.copy()
    table["strict_feasible"] = ((table.max_donor_mean_wait_delta_hours <= THRESHOLDS["strict_mean_hours"])
                                & (table.max_donor_p95_delta_hours <= THRESHOLDS["strict_p95_hours"]))
    exchange_ok = table.exchange_ratio_infinite.astype(bool) | (table.exchange_ratio >= THRESHOLDS["compensation_exchange_ratio"])
    table["compensation_feasible"] = (exchange_ok
        & (table.max_donor_mean_wait_delta_hours <= THRESHOLDS["compensation_mean_hours"])
        & (table.max_donor_native_wait_increased_fraction <= THRESHOLDS["compensation_affected_fraction_per_donor"]))
    return table


def select_recommendations(table):
    table = classify(table)
    candidates = table[table.selection_eligible & ~table.includes_anonymous_donor & (table.a10_mean_wait_gain_hours > 0)]
    def choose(mask):
        pool = candidates[mask]
        return None if pool.empty else str(pool.sort_values(["a10_mean_wait_gain_hours", "scenario"], ascending=[False, True]).iloc[0].scenario)
    strict = choose(candidates.strict_feasible) or "v3_base"
    compensation = choose(candidates.compensation_feasible)
    return {"strict_selected_scenario": strict, "compensation_selected_scenario": compensation or strict,
            "compensation_fallback_to_strict": compensation is None, "thresholds": THRESHOLDS,
            "strict_feasible_candidates": candidates[candidates.strict_feasible].scenario.tolist(),
            "compensation_feasible_candidates": candidates[candidates.compensation_feasible].scenario.tolist(),
            "rule": "DECISIONS.md D026", "affected_fraction_scope": "each donor separately; pooled fraction also reported",
            "scope": "v2 main + v3 main only; named donors; positive net A10 gain; in-sample; sensitivities excluded"}


def c2_sensitivity(table):
    index = table.set_index("scenario")
    sjf = float(index.loc["sjf", "wait_hours_mean"])
    c1 = pd.Series(True, index=table.index)
    for key, _ in GROUPS:
        column = f"group_{key}_mean_wait_hours"
        c1 &= table[column] <= float(index.loc["fcfs", column])
    rows = []
    for multiplier in (1.5, 2., 3.):
        feasible = table[c1 & (table.wait_hours_mean <= multiplier*sjf)]
        chosen = index.loc["sjf"] if feasible.empty else feasible.sort_values(["wait_hours_max", "wait_hours_mean", "scenario"]).iloc[0]
        name = "sjf" if feasible.empty else str(chosen.scenario)
        rows.append({"c2_multiplier": multiplier, "mean_bound_hours": multiplier*sjf,
                     "selected_scenario": name, "wait_hours_mean": float(chosen.wait_hours_mean),
                     "wait_hours_max": float(chosen.wait_hours_max), "feasible_candidates": len(feasible),
                     "fallback": feasible.empty, "candidate_scope": "original 22 v2 candidates; v3 curve points excluded"})
    return pd.DataFrame(rows)


class FrontierExperiment:
    def __init__(self, args):
        self.args = args
        if digest(ROOT / "src/scheduler.py") != CORE_SHA:
            raise ValueError("Scheduling core differs from v2; a fresh historical regression is required")
        self.jobs = pd.read_csv(DATA / "job_info_clean.csv", dtype={"job_name": str}, float_precision="round_trip")
        self.nodes = pd.read_csv(DATA / "node_info_df.csv", dtype={"node_name": str}, float_precision="round_trip")
        self.base_config = read_json(Q2 / "selection_v2.json")["selected_config"]
        if self.base_config != {"policy": "log_aging", "log_aging_tau_seconds": 604800}:
            raise ValueError("D026 requires the frozen log-aging T7d baseline")
        OUT.mkdir(parents=True, exist_ok=True)
        timebox_path = OUT / "timebox.json"
        if not timebox_path.exists():
            write_json(timebox_path, {"registered_start_utc": "2026-10-17T06:03:00+00:00",
                                     "deadline_utc": "2026-10-17T08:03:00+00:00", "rule": "D026"})
        self.deadline = datetime.fromisoformat(read_json(timebox_path)["deadline_utc"])
        if args.reproduction:
            start = datetime.now(timezone.utc)
            self.deadline = start + timedelta(hours=2)
            write_json(OUT/"reproduction_timebox.json", {"start_utc": start.isoformat(),
                       "deadline_utc": self.deadline.isoformat(), "purpose": "Explicit later reproduction of the frozen grid"})
        if args.bootstrap_dependencies:
            for name, config in (("fcfs", {"policy": "fcfs"}), ("sjf", {"policy": "sjf"}), ("q2_star", self.base_config)):
                path = Q2/name
                if not (path/"schedule.csv").exists() or not (path/"allocations.csv").exists():
                    schedule, allocations, _, _ = self.simulate(f"bootstrap_{name}", config)
                    path.mkdir(parents=True, exist_ok=True)
                    for filename, frame in (("schedule.csv", schedule), ("allocations.csv", allocations)):
                        if not (path/filename).exists():
                            frame.to_csv(path/filename, index=False)
        self.base = self.read_schedule(Q2 / "q2_star")
        self.provenance = {"source_sha256": {name: digest(DATA/name) for name in ("job_info_clean.csv", "node_info_df.csv")},
            "code_sha256": {name: digest(ROOT/"src"/name) for name in
                            ("scheduler.py", "evaluate_scheduling.py", "analyze_routing_frontier.py", "analyze_tail.py", "run_v3_experiments.py")},
            "versions": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__}}

    @staticmethod
    def read_schedule(path):
        return pd.read_csv(path/"schedule.csv", dtype={"job_name": str}, float_precision="round_trip")

    def simulate(self, name, config):
        if datetime.now(timezone.utc) >= self.deadline:
            raise TimeoutError("D026 two-hour experiment timebox expired; retain partial results and finish documentation")
        print(f"Start {name}", flush=True)
        started = time.perf_counter()
        schedule, allocations, diagnostics = run_simulation(self.jobs, self.nodes, **config)
        return schedule, allocations, diagnostics, time.perf_counter()-started

    def route(self, name, route, *, phase, baseline_name="q2_star", historical=None, full=False, anchor=None):
        path = OUT / ("v2_reference" if phase.startswith("v2_") else "") / name
        path.mkdir(parents=True, exist_ok=True)
        base_dir = Q2 / ("sjf" if baseline_name == "sjf" else "q2_star")
        base = self.read_schedule(base_dir) if baseline_name == "sjf" else self.base
        config = {"policy": "sjf"} if baseline_name == "sjf" else self.base_config
        manifest = {"name": name, "experiment_version": "v3", **config, "routing": asdict(route),
                    "baseline": baseline_name, "baseline_schedule_sha256": digest(base_dir/"schedule.csv"),
                    "phase": phase, "sensitivity_anchor": anchor, **self.provenance}
        if historical:
            manifest["historical_source"] = historical.relative_to(ROOT).as_posix()
            manifest["historical_manifest_sha256"] = digest(historical/"scenario.json")
        prior_path = path / "scenario.json"
        if prior_path.exists() and not self.args.force:
            prior = read_json(prior_path)
            expected = json.loads(json.dumps(manifest))
            ready = all((path/name).exists() for name in ("metrics.json", "frontier_metrics.json", "comparison_row.json"))
            has_full = prior.get("schedule_storage") == "full" and all((path/f).is_file() for f in ("schedule.csv", "allocations.csv"))
            if ready and all(prior.get(k) == value for k, value in expected.items()) and (not full or has_full):
                print(f"Reuse {name}", flush=True)
                return read_json(path/"comparison_row.json")
        elapsed, diagnostics = None, None
        full = full or (path/"schedule.csv").exists()
        if historical and (historical/"schedule.csv").is_file():
            schedule = self.read_schedule(historical)
            allocations = pd.read_csv(historical/"allocations.csv", dtype={"job_name": str, "node_name": str}, float_precision="round_trip") if full else None
            origin = "historical_full_csv"
        else:
            schedule, allocations, diagnostics, elapsed = self.simulate(name, {**config, "routing": route})
            origin = "frozen_v2_core"
        metric = evaluate(schedule, self.nodes, path)
        paired_comparison(base, schedule, path)
        frontier = analyze_routing_frontier(base, schedule, self.nodes, route.donors, path)
        if historical:
            old = read_json(historical/"metrics.json")
            for key in ("wait_hours_mean", "wait_hours_max", "wait_hours_p99", "last_completion_days", "completed_at_90d", "migrated_jobs"):
                if not np.isclose(metric[key], old[key], rtol=1e-12, atol=1e-10):
                    raise AssertionError(f"Frozen historical replay differs: {name}/{key}")
        if full:
            if allocations is None:
                raise ValueError("Cannot save a full result without allocations")
            schedule.to_csv(path/"schedule.csv", index=False)
            allocations.to_csv(path/"allocations.csv", index=False)
        manifest.update(schedule_storage="full" if full else "metrics_only", simulation_wall_seconds=elapsed,
                        simulation_origin=origin, diagnostics=diagnostics)
        write_json(path/"scenario.json", manifest)
        row = {"scenario": name, "policy": config["policy"], "baseline": baseline_name, "phase": phase,
               "selection_eligible": phase in ("v2_main", "main"), "includes_anonymous_donor": S2 in route.donors,
               "sensitivity_anchor": anchor, "output_path": path.relative_to(ROOT).as_posix(),
               "donors": ",".join(route.donors), "max_routed_duration_seconds": route.max_routed_duration_seconds,
               "donor_reserve_fraction": route.donor_reserve_fraction, "donor_cap_fraction": route.donor_cap_fraction,
               "flexible_fraction": route.flexible_fraction, "seed": route.seed, "speedup": route.speedup,
               "min_wait_seconds": route.min_wait_seconds, **flat(metric), **flat(frontier)}
        row["a10_mean_wait_gain_hours"] = row["a10_job_hours_saved"] / row["a10_jobs"]
        write_json(path/"comparison_row.json", row)
        print(json.dumps({"scenario": name, "seconds": elapsed, "mean_wait_h": row["wait_hours_mean"],
                          "gain_h": row["a10_mean_wait_gain_hours"], "native_mean_h": row["max_donor_mean_wait_delta_hours"],
                          "ratio": row["exchange_ratio"], "ratio_infinite": row["exchange_ratio_infinite"]}), flush=True)
        return row

    def save_tables(self, rows):
        table = classify(pd.DataFrame(rows))
        table.to_csv(OUT/"comparison.csv", index=False)
        columns = ["scenario", "phase", "baseline", "selection_eligible", "a10_job_hours_saved", "donor_native_job_hours_added",
                   "exchange_ratio", "exchange_ratio_infinite", "a10_gpu_wait_hours_saved", "compensation_gpu_hours",
                   "compensation_share", "compensation_share_undefined", "native_wait_increased_jobs", "native_wait_increased_fraction",
                   "max_donor_native_wait_increased_fraction", "a10_wait_increased_jobs", "a10_wait_increased_fraction"]
        table[columns].to_csv(OUT/"compensation.csv", index=False)
        return table

    def q3(self):
        old_table = pd.read_csv(OLD/"comparison.csv")
        rows, definitions = [], {}
        for old_row in old_table[old_table.phase != "baseline"].to_dict("records"):
            name = old_row["scenario"]
            historical = OLD/name
            meta = read_json(historical/"scenario.json")
            route = routing_from_manifest(meta["routing"])
            phase = "v2_main" if old_row["phase"] == "main" else "v2_sensitivity"
            definitions[name] = (route, phase, historical)
            rows.append(self.route(name, route, phase=phase, historical=historical, baseline_name=old_row["baseline"]))
            self.save_tables(rows)
        for name, route in main_configs().items():
            definitions[name] = (route, "main", None)
            rows.append(self.route(name, route, phase="main"))
            self.save_tables(rows)
        selection = select_recommendations(pd.DataFrame(rows))
        selection.update(q2_star_config=self.base_config, q2_star_unchanged=True,
                         v2_main_candidates=11, v3_main_candidates=12,
                         selected_output_paths={}, selected_configs={})
        chosen = list(dict.fromkeys([selection["strict_selected_scenario"], selection["compensation_selected_scenario"]]))
        for name in chosen:
            if name == "v3_base":
                selection["selected_output_paths"][name] = "outputs/question2/q2_star"
                continue
            route, phase, historical = definitions[name]
            row = self.route(name, route, phase=phase, historical=historical, full=True)
            selection["selected_output_paths"][name] = row["output_path"]
            selection["selected_configs"][name] = asdict(route)
        write_json(OUT/"selection.json", selection)
        print(json.dumps(selection, ensure_ascii=False), flush=True)
        for index, name in enumerate(chosen):
            if name == "v3_base":
                continue
            route = definitions[name][0]
            label = "strict" if name == selection["strict_selected_scenario"] else "compensation"
            for suffix, changes in (("seed18", {"seed": 20261018}), ("seed19", {"seed": 20261019}),
                                    ("p25", {"flexible_fraction": .25}), ("p100", {"flexible_fraction": 1.})):
                rows.append(self.route(f"v3_{label}_{suffix}", replace(route, **changes), phase="sensitivity", anchor=name))
                self.save_tables(rows)
        table = self.save_tables(rows)
        selection["completed"] = True
        selection["sensitivity_configurations"] = int((table.phase == "sensitivity").sum())
        write_json(OUT/"selection.json", selection)

    def q2(self):
        c2 = c2_sensitivity(pd.read_csv(Q2/"comparison_v2.csv"))
        c2.to_csv(Q2/"c2_multiplier_sensitivity.csv", index=False)
        if c2.selected_scenario.tolist() != ["tau_2592000", "tau_604800", "tau_604800"]:
            raise AssertionError("C2 multiplier check differs from expected result; investigate without changing Q2*")
        fcfs = self.read_schedule(Q2/"fcfs")
        rows = []
        for days in (10, 14, 21):
            name = f"tau_{days*86400}"
            path = Q2/"v3_curve_points"/name
            path.mkdir(parents=True, exist_ok=True)
            manifest = {"name": name, "experiment_version": "v3", "policy": "log_aging", "log_aging_tau_seconds": days*86400,
                        "routing": None, "schedule_storage": "metrics_only", "selection_eligible": False,
                        "purpose": "Curve interpolation only; excluded from Q2 selection", **self.provenance}
            prior = read_json(path/"scenario.json") if (path/"scenario.json").exists() else {}
            ready = (path/"metrics.json").exists() and (path/"tail_metrics.json").exists()
            if not self.args.force and ready and all(prior.get(k) == value for k, value in manifest.items()):
                metric, tail = read_json(path/"metrics.json"), read_json(path/"tail_metrics.json")
            else:
                schedule, _, diagnostics, elapsed = self.simulate(name, {"policy": "log_aging", "log_aging_tau_seconds": days*86400})
                metric = evaluate(schedule, self.nodes, path)
                tail = analyze_tail(schedule, fcfs, path)
                manifest.update(simulation_wall_seconds=elapsed, diagnostics=diagnostics)
                write_json(path/"scenario.json", manifest)
            rows.append({"scenario": name, "tau_seconds": days*86400, "policy": "log_aging", "selection_eligible": False,
                         "output_path": path.relative_to(ROOT).as_posix(), **flat(metric), **flat(tail)})
        pd.DataFrame(rows).to_csv(Q2/"v3_curve_points/comparison.csv", index=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("all", "q3", "q2"), default="all")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--bootstrap-dependencies", action="store_true", help="Rebuild omitted FCFS/SJF/Q2* full CSVs from raw data when unpacking the supplement")
    parser.add_argument("--reproduction", action="store_true", help="Explicit later reproduction: a fresh two-hour budget for the same frozen grid")
    args = parser.parse_args()
    experiment = FrontierExperiment(args)
    if args.suite in ("all", "q3"):
        experiment.q3()
    if args.suite in ("all", "q2"):
        experiment.q2()
    if digest(ROOT/"src/scheduler.py") != CORE_SHA:
        raise AssertionError("Frozen core changed during v3")


if __name__ == "__main__":
    main()
