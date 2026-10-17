"""Verify frozen v3 decisions, ledgers, provenance and saved full schedules.

No simulation or scheduling implementation is imported. Historical regression
is authenticated by its frozen report/core/input/artifact hashes, not rerun.
Use --audit-schedules once final recommendation CSVs have been saved. Existing
independent audit files are authenticated and never overwritten.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from decimal import Decimal
import json
from pathlib import Path
import re

from verify_all_schedules import preserved_report
from verify_schedules import Checks, DEFAULT_DATA, sha256, verify_scenario

ROOT = Path(__file__).resolve().parents[1]
D = Decimal
HOUR = D(3600)
CORE_SHA = "714e94bdc388abd63a9274e67c394157eb6e06bfb5a6ffba0a8a0320acb10966"
OLD_CORE_SHA = "81058052fcf18c2f6a15e2251fef7892fcdffcda3ebbc615e03e2a607167d36b"
REGRESSION_SHA = "ebac19e2bed44adc4c141594b8093102808529a892024646ea3ad3c39eec54f6"
HISTORICAL_AUDIT_SUMMARY_SHA = "fbf253bc1f0590187b154b37df46b67757ef9c39a2fcf1d64ba33ccf1555dc0c"
SOURCE_SHA = {"job_info_clean.csv": "735bc06c3986b1d0c5932236f8685b7204463829118fc6a1d37030e253ed38e1",
              "node_info_df.csv": "1aba161961a5a4a1a61aa581383c5e5abe3400b59f8597ba8c4eef7597bc9d18"}
MAIN_NAMES = {"v3_ah_d14", "v3_ah_d30", "v3_ah_d60", "v3_ah_d7_t1h", "v3_ah_d14_t1h",
              "v3_ah_d30_t1h", "v3_ah_d60_t1h", "v3_ah_t1h", "v3_ah_cap10", "v3_ah_cap15",
              "v3_ha_d30", "v3_ha"}
GROUPS = ("le1h", "1h_1d", "1d_7d", "gt7d")
CODE_FILES = {"scheduler.py", "evaluate_scheduling.py", "analyze_routing_frontier.py", "analyze_tail.py", "run_v3_experiments.py"}


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def boolean(value) -> bool:
    if value is True or value == "True" or value == "true":
        return True
    if value is False or value == "False" or value == "false":
        return False
    raise ValueError(f"Not a boolean: {value!r}")


def number(value) -> Decimal:
    result = D(str(value))
    if not result.is_finite():
        raise ValueError(f"Nonfinite numeric value: {value!r}")
    return result


def close(checks: Checks, actual, expected, label: str) -> None:
    if expected is None:
        checks.check(actual is None or actual == "", f"{label}: expected an undefined numeric value")
    else:
        a, b = number(actual), number(expected)
        checks.check(abs(a-b) <= max(D("1e-8"), abs(b)*D("1e-11")), f"{label}: {a} != {b}")


def authenticate_history(checks: Checks, data: Path) -> dict:
    """Authenticate the unchanged earlier experiment, including all 314 files."""
    report_path = ROOT / "outputs/v2_regression.json"
    checks.check(sha256(report_path) == REGRESSION_SHA, "Historical regression report changed")
    report = read(report_path)
    checks.check(report.get("passed") is True and report.get("completed") is True
                 and report.get("failure_count") == 0 and report.get("completed_scenarios") == 41,
                 "Historical regression report is incomplete or failed")
    checks.check(report.get("total_compared_cells") == 208704795, "Historical regression comparison count changed")
    core_hashes = {"current": sha256(ROOT / "src/scheduler.py"),
                   "previous": sha256(ROOT / "outputs/v2_reference/source/scheduler.py")}
    expected_cores = {"current": CORE_SHA, "previous": OLD_CORE_SHA}
    checks.check(core_hashes == expected_cores == report["scheduler_sha256_before"]
                 == report["scheduler_sha256_after"], "Frozen current or previous core hash mismatch")
    source_hashes = {name: sha256(data / name) for name in SOURCE_SHA}
    checks.check(source_hashes == SOURCE_SHA == report["source_sha256_before"]
                 == report["source_sha256_after"], "Original input hash mismatch")
    artifact_count = 0
    for record in report["results"]:
        checks.check(record["passed"] and record["historical_artifacts_unchanged"],
                     f"Historical record not passed: {record['scenario']}")
        checks.check(record["artifact_sha256_before"] == record["artifact_sha256_after"],
                     f"Historical before/after hashes differ: {record['scenario']}")
        for name, expected in record["artifact_sha256_after"].items():
            path = ROOT / "outputs" / record["scenario"] / name
            checks.check(path.is_file() and sha256(path) == expected, f"Historical artifact changed/missing: {path}")
            artifact_count += 1
    audit_summary_path = ROOT / "outputs/scheduling_validation.json"
    checks.check(sha256(audit_summary_path) == HISTORICAL_AUDIT_SUMMARY_SHA, "Prior scheduling summary changed")
    audit_summary = read(audit_summary_path)
    checks.check(audit_summary["passed"] is True and audit_summary["scenarios"] == 24
                 and audit_summary["comparisons"] == 137251679, "Prior feasibility summary differs")
    for record in audit_summary["results"]:
        previous = preserved_report(ROOT / "outputs" / record["relative_path"], data)
        checks.check(previous["comparisons"] == record["comparisons"] and previous["schedule_rows"] == 224112,
                     f"Prior feasibility evidence differs: {record['relative_path']}")
    return {"evidence": "outputs/v2_regression.json", "sha256": sha256(report_path),
            "scenarios": 41, "artifact_hashes_checked": artifact_count,
            "historical_compared_cells": report["total_compared_cells"], "regression_rerun": False,
            "preserved_feasibility_audits": 24, "historical_feasibility_comparisons": 137251679,
            "historical_feasibility_summary_sha256": sha256(audit_summary_path),
            "current_core_sha256": core_hashes["current"], "previous_core_sha256": core_hashes["previous"],
            "source_sha256": source_hashes}


def inspect_ledger(checks: Checks, row: dict, path: Path) -> None:
    """Reconcile separate tables and recompute ledger identities and bounds."""
    label = row["scenario"]
    metric = read(path / "frontier_metrics.json")
    harm = rows(path / "native_harm_by_donor.csv")
    peaks = rows(path / "borrowing_peaks.csv")
    donors = row["donors"].split(",")
    checks.check([item["donor_model"] for item in harm] == donors, f"{label}: donor harm rows/order mismatch")
    checks.check([item["donor_model"] for item in peaks] == donors, f"{label}: donor peak rows/order mismatch")
    for key, value in metric.items():
        if isinstance(value, (dict, list)):
            continue
        if isinstance(value, bool):
            checks.check(boolean(row[key]) == value, f"{label}: {key} mismatch")
        else:
            close(checks, row[key], value, f"{label}/{key}")
    added = sum((number(item["donor_native_job_hours_added"]) for item in harm), D(0))
    credit = sum((number(item["compensation_gpu_hours"]) for item in harm), D(0))
    native_jobs = sum(int(item["native_jobs"]) for item in harm)
    harmed = sum(int(item["native_wait_increased_jobs"]) for item in harm)
    close(checks, row["donor_native_job_hours_added"], added, f"{label}: positive delay total")
    close(checks, row["compensation_gpu_hours"], credit, f"{label}: credit total")
    close(checks, row["donor_native_jobs"], native_jobs, f"{label}: native count")
    close(checks, row["native_wait_increased_jobs"], harmed, f"{label}: harmed count")
    close(checks, row["native_wait_increased_fraction"], D(harmed)/native_jobs, f"{label}: pooled harmed fraction")
    for item in harm:
        count = int(item["native_jobs"])
        close(checks, item["native_wait_increased_fraction"], D(item["native_wait_increased_jobs"])/count,
              f"{label}/{item['donor_model']}: harmed fraction")
    for target, field in (("max_donor_mean_wait_delta_hours", "native_wait_mean_delta_hours"),
                          ("max_donor_p95_delta_hours", "native_wait_p95_delta_hours"),
                          ("max_donor_native_wait_increased_fraction", "native_wait_increased_fraction")):
        close(checks, row[target], max(number(item[field]) for item in harm), f"{label}: {target}")
    saved, weighted = number(row["a10_job_hours_saved"]), number(row["a10_gpu_wait_hours_saved"])
    close(checks, row["a10_mean_wait_gain_hours"], saved/number(row["a10_jobs"]), f"{label}: A10 mean gain")
    checks.check(boolean(row["exchange_ratio_infinite"]) == (added == 0), f"{label}: infinite ratio flag")
    close(checks, row["exchange_ratio"], None if added == 0 else saved/added, f"{label}: exchange ratio")
    checks.check(boolean(row["compensation_share_undefined"]) == (weighted <= 0), f"{label}: undefined C/S flag")
    close(checks, row["compensation_share"], None if weighted <= 0 else credit/weighted, f"{label}: C/S")
    checks.check(sum(int(item["migrated_jobs"]) for item in peaks) == int(row["migrated_jobs"]), f"{label}: donor migration counts")
    for item in peaks:
        for resource in ("gpu", "cpu"):
            key = "gpus" if resource == "gpu" else "cpus"
            value, cap = number(item[f"peak_borrowed_{key}"]), number(item[f"donor_{resource}_capacity"])
            checks.check(0 <= value <= cap, f"{label}: borrowing peak outside physical capacity")
            close(checks, item[f"peak_borrowed_{resource}_fraction"], value/cap, f"{label}: peak {resource} share")


def independent_selection(table: list[dict]) -> dict:
    candidates = [row for row in table if row["phase"] in {"main", "v2_main"}
                  and "GPU-series-2" not in row["donors"].split(",")
                  and number(row["a10_mean_wait_gain_hours"]) > 0]
    strict = [row for row in candidates if number(row["max_donor_mean_wait_delta_hours"]) <= D(".5")
              and number(row["max_donor_p95_delta_hours"]) <= 2]
    compensated = [row for row in candidates
                   if (boolean(row["exchange_ratio_infinite"]) or number(row["exchange_ratio"]) >= 100)
                   and number(row["max_donor_mean_wait_delta_hours"]) <= 1
                   and number(row["max_donor_native_wait_increased_fraction"]) <= D(".2")]
    choose = lambda group: min(group, key=lambda row: (-number(row["a10_mean_wait_gain_hours"]), row["scenario"]))["scenario"]
    chosen = choose(strict) if strict else "v3_base"
    return {"strict_selected_scenario": chosen,
            "compensation_selected_scenario": choose(compensated) if compensated else chosen,
            "compensation_fallback_to_strict": not compensated,
            "strict_feasible_candidates": [row["scenario"] for row in strict],
            "compensation_feasible_candidates": [row["scenario"] for row in compensated]}


def percentile(values: list[Decimal], fraction: Decimal) -> Decimal:
    values = sorted(values)
    position = (len(values)-1)*fraction
    lower = int(position)
    return values[lower] if lower == len(values)-1 else values[lower] + (position-lower)*(values[lower+1]-values[lower])


def paired_full_check(checks: Checks, path: Path, audit: dict) -> dict:
    """Recompute recommendation ledgers from start times using Decimal."""
    manifest = read(path / "scenario.json")
    donors = manifest["routing"]["donors"]
    baseline = {}
    with (ROOT / "outputs/question2/q2_star/schedule.csv").open(encoding="utf-8-sig", newline="") as handle:
        for job in csv.DictReader(handle):
            baseline[job["job_name"]] = (job["requested_model"], D(job["start_time"])-D(job["submit_time"]),
                                        int(job["gpu_request"])*int(job["worker_num"]))
    groups = defaultdict(lambda: {"old": [], "new": [], "deltas": [], "gpu": []})
    seen = set()
    with (path / "schedule.csv").open(encoding="utf-8-sig", newline="") as handle:
        for job in csv.DictReader(handle):
            name = job["job_name"]
            if name not in baseline or name in seen:
                raise ValueError(f"Paired ledger identifier mismatch: {name}")
            seen.add(name)
            model, old, gpu = baseline[name]
            if model == "A10" or model in donors:
                new = D(job["start_time"])-D(job["submit_time"])
                group = groups[model]
                for key, value in (("old", old), ("new", new), ("deltas", new-old), ("gpu", gpu)):
                    group[key].append(value)
    checks.check(seen == set(baseline), f"{path.name}: paired full population")
    a10 = groups["A10"]
    saved = -sum(a10["deltas"])/HOUR
    weighted = -sum(delta*gpu for delta, gpu in zip(a10["deltas"], a10["gpu"]))/HOUR
    total_added = total_credit = D(0)
    ledger = read(path / "frontier_metrics.json")
    donor_rows = {row["donor_model"]: row for row in rows(path / "native_harm_by_donor.csv")}
    for model in donors:
        group = groups[model]
        positive = [max(D(0), delta) for delta in group["deltas"]]
        added = sum(positive)/HOUR
        credit = sum(delta*gpu for delta, gpu in zip(positive, group["gpu"]))/HOUR
        total_added += added
        total_credit += credit
        expected = {"native_jobs": len(positive), "native_wait_increased_jobs": sum(delta > 0 for delta in positive),
                    "native_wait_increased_fraction": D(sum(delta > 0 for delta in positive))/len(positive),
                    "native_wait_mean_delta_hours": sum(group["deltas"])/len(positive)/HOUR,
                    "native_wait_p95_delta_hours": (percentile(group["new"], D(".95"))-percentile(group["old"], D(".95")))/HOUR,
                    "donor_native_job_hours_added": added, "compensation_gpu_hours": credit,
                    "native_max_wait_increase_hours": max(positive)/HOUR}
        for key, value in expected.items():
            close(checks, donor_rows[model][key], value, f"{path.name}/{model}: independent {key}")
    expected = {"a10_job_hours_saved": saved, "a10_gpu_wait_hours_saved": weighted,
                "donor_native_job_hours_added": total_added, "compensation_gpu_hours": total_credit,
                "a10_wait_increased_jobs": sum(delta > 0 for delta in a10["deltas"]),
                "a10_wait_increased_fraction": D(sum(delta > 0 for delta in a10["deltas"]))/len(a10["deltas"]),
                "a10_max_wait_increase_hours": max(D(0), max(a10["deltas"]))/HOUR,
                "exchange_ratio": saved/total_added if total_added else None,
                "compensation_share": total_credit/weighted if weighted > 0 else None}
    for key, value in expected.items():
        close(checks, ledger[key], value, f"{path.name}: independent {key}")
    for peak in rows(path / "borrowing_peaks.csv"):
        evidence = audit["donor_cap_checks"][peak["donor_model"]]
        close(checks, peak["peak_borrowed_gpus"], evidence["peak_borrowed_gpu"], f"{path.name}: independent GPU peak")
        close(checks, peak["peak_borrowed_cpus"], evidence["peak_borrowed_cpu"], f"{path.name}: independent CPU peak")
    return {key: str(value) if value is not None else None for key, value in expected.items()}


def audit_recommendations(checks: Checks, selection: dict, data: Path, audit_new: bool) -> dict:
    records = []
    paths = set(selection["selected_output_paths"].values())
    for relative in sorted(paths):
        path = ROOT / relative
        previous = (path / "independent_validation.json").exists()
        if previous:
            report = preserved_report(path, data)
        else:
            if not audit_new:
                raise ValueError(f"Missing recommendation audit: {relative}; use --audit-schedules")
            report = verify_scenario(path, data)
            (path / "independent_validation.json").write_text(json.dumps(report, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
        checks.check(report["passed"] is True and report["failure_count"] == 0, f"Recommendation audit failed: {relative}")
        prior_failures = checks.failure_count
        paired = paired_full_check(checks, path, report) if read(path / "scenario.json").get("routing") else None
        paired_failures = checks.failure_count-prior_failures
        record = {"relative_path": relative, "status": "preserved_history" if previous else "audited_new",
                  "passed": report["passed"] and paired_failures == 0,
                  "failure_count": report["failure_count"]+paired_failures,
                  "feasibility_passed": report["passed"], "paired_ledger_failure_count": paired_failures,
                  "comparisons": report["comparisons"], "schedule_rows": report["schedule_rows"],
                  "allocation_rows": report["allocation_rows"], "paired_ledger_decimal": paired,
                  "audit_sha256": sha256(path / "independent_validation.json")}
        records.append(record)
        print(json.dumps({key: record[key] for key in ("relative_path", "status", "passed", "comparisons")}), flush=True)
    return {"passed": all(record["passed"] for record in records), "scenarios": len(records),
            "audited_new": sum(record["status"] == "audited_new" for record in records),
            "preserved_history": sum(record["status"] == "preserved_history" for record in records),
            "comparisons": sum(record["comparisons"] for record in records),
            "current_run_comparisons": sum(record["comparisons"] for record in records if record["status"] == "audited_new"),
            "failure_count": sum(record["failure_count"] for record in records), "results": records,
            "scope": "Only final selected full CSVs independently audited. Historical reports remain unchanged; other v3 scenarios are metrics-only."}


def verify(data: Path, audit_new: bool) -> dict:
    checks = Checks()
    history = authenticate_history(checks, data)
    q2, q3 = ROOT / "outputs/question2", ROOT / "outputs/question3/v3"
    selection = read(q3 / "selection.json")
    checks.check(selection.get("completed") is True, "Final selection is incomplete")
    checks.check(read(q2 / "selection_v2.json")["selected_config"] == {"policy": "log_aging", "log_aging_tau_seconds": 604800}, "Q2* changed")
    table = rows(q3 / "comparison.csv")
    checks.check(len({row["scenario"] for row in table}) == len(table), "Duplicate comparison scenarios")
    checks.check({row["scenario"] for row in table if row["phase"] == "main"} == MAIN_NAMES, "v3 main grid differs")
    checks.check(sum(row["phase"] == "v2_main" for row in table) == 11, "v2 main candidate count differs")
    checks.check(sum(row["phase"] == "v2_sensitivity" for row in table) == 7, "v2 sensitivity count differs")
    independently_chosen = independent_selection(table)
    for key, value in independently_chosen.items():
        checks.check(selection[key] == value, f"Independent D026 selection differs: {key}")
    unique_chosen = set(selection[key] for key in ("strict_selected_scenario", "compensation_selected_scenario")) - {"v3_base"}
    sensitivity_count = sum(row["phase"] == "sensitivity" for row in table)
    checks.check(sensitivity_count == 4*len(unique_chosen) == selection["sensitivity_configurations"], "Recommendation sensitivity grid incomplete")
    roles = []
    if selection["strict_selected_scenario"] != "v3_base":
        roles.append("strict")
    if selection["compensation_selected_scenario"] not in {"v3_base", selection["strict_selected_scenario"]}:
        roles.append("compensation")
    expected_sensitivity = {f"v3_{role}_{suffix}" for role in roles for suffix in ("seed18", "seed19", "p25", "p100")}
    checks.check({row["scenario"] for row in table if row["phase"] == "sensitivity"} == expected_sensitivity,
                 "A recommended seed/participation sensitivity is missing or duplicated")
    expected_full = set(selection["selected_output_paths"].values()) - {"outputs/question2/q2_star"}
    actual_full = set()
    by_name = {row["scenario"]: row for row in table}
    base_hashes = {name: sha256(q2 / name / "schedule.csv") for name in ("q2_star", "sjf")}
    compensation = rows(q3 / "compensation.csv")
    checks.check([row["scenario"] for row in compensation] == [row["scenario"] for row in table], "Compensation table rows differ")
    for row, credit_row in zip(table, compensation):
        path = (ROOT / row["output_path"]).resolve()
        checks.check(path.is_relative_to(q3), f"Unexpected v3 result directory: {path}")
        meta = read(path / "scenario.json")
        checks.check(meta["source_sha256"] == SOURCE_SHA, f"{path.name}: input provenance")
        checks.check(set(meta["code_sha256"]) == CODE_FILES, f"{path.name}: incomplete code provenance")
        for name, expected in meta["code_sha256"].items():
            checks.check(sha256(ROOT / "src" / name) == expected, f"{path.name}: current code hash {name}")
        checks.check(meta["baseline_schedule_sha256"] == base_hashes[row["baseline"]], f"{path.name}: paired baseline hash")
        checks.check(row["baseline"] == ("sjf" if row["scenario"] == "v2_sjf_bridge" else "q2_star"), f"{path.name}: paired baseline identity")
        checks.check(boolean(row["selection_eligible"]) == (row["phase"] in {"main", "v2_main"}), f"{path.name}: eligibility flag")
        checks.check(boolean(row["includes_anonymous_donor"]) == ("GPU-series-2" in row["donors"].split(",")), f"{path.name}: anonymous flag")
        checks.check(meta["name"] == row["scenario"] and meta["phase"] == row["phase"], f"{path.name}: manifest identity")
        checks.check(meta["routing"]["donors"] == row["donors"].split(","), f"{path.name}: manifest donors")
        for key in ("max_routed_duration_seconds", "donor_reserve_fraction", "donor_cap_fraction", "flexible_fraction", "seed", "speedup", "min_wait_seconds"):
            close(checks, row[key], meta["routing"][key], f"{path.name}: manifest {key}")
        strict = number(row["max_donor_mean_wait_delta_hours"]) <= D(".5") and number(row["max_donor_p95_delta_hours"]) <= 2
        compensated = ((boolean(row["exchange_ratio_infinite"]) or number(row["exchange_ratio"]) >= 100)
                       and number(row["max_donor_mean_wait_delta_hours"]) <= 1
                       and number(row["max_donor_native_wait_increased_fraction"]) <= D(".2"))
        checks.check(boolean(row["strict_feasible"]) == strict and boolean(row["compensation_feasible"]) == compensated,
                     f"{path.name}: management classification flags")
        route = meta["routing"]
        if row["phase"] == "main":
            match = re.search(r"_d(\d+)", row["scenario"])
            expected_duration = int(match.group(1))*86400 if match else None
            expected_cap = .1 if row["scenario"] == "v3_ah_cap10" else .15 if row["scenario"] == "v3_ah_cap15" else .25
            expected_donors = ["H800", "A100-SXM4-80GB"] if row["scenario"].startswith("v3_ha") else ["A100-SXM4-80GB", "H800"]
            expected = {"donors": expected_donors, "mode": "pull", "flexible_fraction": .5, "seed": 20261017,
                        "speedup": 1., "donor_cap_fraction": expected_cap, "donor_reserve_fraction": 0.,
                        "min_wait_seconds": 3600 if row["scenario"].endswith("_t1h") else 0,
                        "max_routed_duration_seconds": expected_duration, "donor_caps": [], "pull_scan_limit": 256}
            checks.check(route == expected, f"{path.name}: predeclared v3 configuration differs")
        elif row["phase"] == "sensitivity":
            anchor = row["sensitivity_anchor"]
            checks.check(anchor in unique_chosen, f"{path.name}: sensitivity has wrong recommendation anchor")
            anchor_route = read(ROOT / by_name[anchor]["output_path"] / "scenario.json")["routing"]
            suffix = row["scenario"].rsplit("_", 1)[-1]
            changes = {"seed18": {"seed": 20261018}, "seed19": {"seed": 20261019},
                       "p25": {"flexible_fraction": .25}, "p100": {"flexible_fraction": 1.}}[suffix]
            checks.check(route == {**anchor_route, **changes}, f"{path.name}: sensitivity changes more than one factor")
        else:
            historical = ROOT / meta["historical_source"]
            checks.check(sha256(historical / "scenario.json") == meta["historical_manifest_sha256"], f"{path.name}: historical manifest provenance")
        metric = read(path / "metrics.json")
        checks.check(metric["jobs"] == 224112 == sum(metric[f"{state}_at_90d"] for state in ("completed", "running", "waiting")), f"{path.name}: population")
        storage = meta["schedule_storage"]
        if storage == "full":
            actual_full.add(row["output_path"])
            checks.check(all((path / filename).is_file() for filename in ("schedule.csv", "allocations.csv")), f"{path.name}: full CSV missing")
        else:
            checks.check(storage == "metrics_only" and not any((path / filename).exists() for filename in ("schedule.csv", "allocations.csv")), f"{path.name}: metrics-only artifact mismatch")
        for key in credit_row:
            checks.check(credit_row[key] == row[key], f"{path.name}: compensation column {key}")
        inspect_ledger(checks, row, path)
    checks.check(actual_full == expected_full, "Final full schedule set differs from the two recommendations")
    original_q2 = rows(q2 / "comparison_v2.csv")
    checks.check(len(original_q2) == 22, "Original Q2 grid changed")
    index = {row["scenario"]: row for row in original_q2}
    c2_rows = rows(q2 / "c2_multiplier_sensitivity.csv")
    checks.check([row["c2_multiplier"] for row in c2_rows] == ["1.5", "2.0", "3.0"], "C2 multiplier grid differs")
    for row in c2_rows:
        bound = number(row["c2_multiplier"])*number(index["sjf"]["wait_hours_mean"])
        feasible = [candidate for candidate in original_q2 if number(candidate["wait_hours_mean"]) <= bound
                    and all(number(candidate[f"group_{group}_mean_wait_hours"]) <= number(index["fcfs"][f"group_{group}_mean_wait_hours"]) for group in GROUPS)]
        expected = min(feasible, key=lambda item: (number(item["wait_hours_max"]), number(item["wait_hours_mean"]), item["scenario"]))["scenario"] if feasible else "sjf"
        checks.check(row["selected_scenario"] == expected and int(row["feasible_candidates"]) == len(feasible), "Independent C2 selection differs")
    curves = rows(q2 / "v3_curve_points/comparison.csv")
    checks.check({int(row["tau_seconds"]) for row in curves} == {864000, 1209600, 1814400}, "Q2 curve points incomplete")
    for row in curves:
        path = ROOT / row["output_path"]
        meta = read(path / "scenario.json")
        checks.check(not boolean(row["selection_eligible"]) and meta["selection_eligible"] is False, "Curve point entered Q2 selection")
        checks.check(meta["schedule_storage"] == "metrics_only" and not any((path / name).exists() for name in ("schedule.csv", "allocations.csv")), "Curve point saved a full schedule")
        checks.check(meta["source_sha256"] == SOURCE_SHA, "Curve source hash mismatch")
        checks.check(set(meta["code_sha256"]) == CODE_FILES, "Curve code provenance incomplete")
        for name, expected in meta["code_sha256"].items():
            checks.check(sha256(ROOT / "src" / name) == expected, f"Curve source-code mismatch: {name}")
    scheduling = audit_recommendations(checks, selection, data, audit_new)
    scheduling["skipped_metrics"] = len(table)-len(actual_full)+len(curves)
    scheduling["preserved_pre_v3_history"] = history["preserved_feasibility_audits"]
    scheduling["historical_summary_sha256"] = history["historical_feasibility_summary_sha256"]
    summary_path = ROOT / "outputs/v3_scheduling_validation.json"
    if audit_new or not summary_path.exists():
        summary_path.write_text(json.dumps(scheduling, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    else:
        saved = read(summary_path)
        checks.check(saved["passed"] is True, "Saved v3 schedule summary failed")
        checks.check({item["relative_path"]: item["audit_sha256"] for item in saved["results"]}
                     == {item["relative_path"]: item["audit_sha256"] for item in scheduling["results"]}, "Saved v3 audit summary hashes differ")
        # A prompt recommendation audit may precede the final sensitivity and
        # curve files. Complete only its coverage metadata, retaining the first
        # audit's new/preserved counts and per-scenario evidence unchanged.
        updated = {**saved, **{key: scheduling[key] for key in
                   ("skipped_metrics", "preserved_pre_v3_history", "historical_summary_sha256")}}
        updated.pop("pending_metrics_completion", None)
        if updated != saved:
            summary_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    documents = [ROOT / name for name in ("README.md", "CURRENT_STATUS.md", "SUBMISSION_PACKAGE.md", "AGENTS.md", "DECISIONS.md",
                 "reports/B_Q23_analysis_zh.md", "reports/B_Q23_report_en.md", "reports/B_Q23_methods_zh.md", "reports/paper_outline.md")]
    link_count = 0
    for document in documents:
        for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', document.read_text(encoding="utf-8-sig")):
            if "://" not in target and not target.startswith("#"):
                destination = (document.parent / target.split("#", 1)[0]).resolve()
                checks.check(destination.exists() or destination == ROOT / "outputs/v3_delivery_review.json",
                             f"Broken link in {document.name}: {target}")
                link_count += 1
    for directory, name in ((q2 / "figures_v3", "04_policy_tradeoff"), (q3 / "figures", "05_routing_frontier"), (q3 / "figures", "07_compensation")):
        for ext in ("png", "svg"):
            checks.check((directory / f"{name}.{ext}").is_file(), f"Missing v3 figure: {name}.{ext}")
    log = (ROOT / "outputs/v3_unit_tests.txt").read_text(encoding="utf-8-sig")
    match = re.search(r"Ran (\d+) tests", log)
    checks.check(bool(match) and int(match.group(1)) >= 140 and bool(re.search(r"\bOK\s*$", log)), "Final unit test log absent or failed")
    return {"passed": checks.failure_count == 0, "failure_count": checks.failure_count, "failures": checks.failures,
            "comparisons": checks.comparisons, "q3_rows": len(table), "q3_main_new": 12, "q3_main_historical": 11,
            "new_sensitivities": sensitivity_count, "v3_manifests_checked": len(table)+len(curves),
            "current_code_hash_checks": 5*(len(table)+len(curves)), "q2_curve_points": len(curves),
            "selected": independently_chosen, "selected_full_paths": sorted(actual_full),
            "scheduling_summary": "outputs/v3_scheduling_validation.json", "historical_regression": history,
            "local_links_checked": link_count, "new_figure_files": 6,
            "unit_tests": int(match.group(1)) if match else None, "verifier_sha256": sha256(Path(__file__))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--audit-schedules", action="store_true", help="Create independent audit files for newly selected full scenarios")
    args = parser.parse_args()
    try:
        report = verify(args.data_dir, args.audit_schedules)
    except (OSError, ValueError, KeyError, ArithmeticError) as error:
        report = {"passed": False, "failure_count": 1, "failures": [f"{type(error).__name__}: {error}"],
                  "verifier_sha256": sha256(Path(__file__))}
    destination = ROOT / "outputs/v3_delivery_review.json"
    destination.write_text(json.dumps(report, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
