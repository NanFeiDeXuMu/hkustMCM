"""Verify v2 delivery completeness, provenance and published decision records."""
import hashlib
import json
from pathlib import Path
import re

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "2026_MCM_Senior_GPU_Scheduling/2026_MCM_Senior_GPU_Scheduling"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    problems, counts, timings = [], {}, []
    q2 = ROOT / "outputs/question2"
    q3 = ROOT / "outputs/question3/v2"
    manifests = list((q2 / "v2_candidates").glob("*/scenario.json"))
    manifests += list((q2 / "log_aging_sweep").glob("*/scenario.json"))
    manifests += [q2 / "q2_star/scenario.json"] + list(q3.glob("*/scenario.json"))
    chosen2, chosen3 = read(q2 / "selection_v2.json"), read(q3 / "selection.json")
    expected_full = {"outputs/question2/q2_star", "outputs/question3/v2/v2_ah", "outputs/question3/v2/v2_ahs"}
    for key in ("primary_selected_scenario", "conditional_selected_scenario"):
        if chosen3[key] != "v2_base":
            expected_full.add(f"outputs/question3/v2/{chosen3[key]}")
    actual_full = set()
    for path in manifests:
        meta = read(path)
        label = path.parent.relative_to(ROOT).as_posix()
        for name, expected in meta["source_sha256"].items():
            if sha(DATA / name) != expected:
                problems.append(f"Source mismatch: {label}/{name}")
        for name, expected in meta["code_sha256"].items():
            if sha(ROOT / "src" / name) != expected:
                problems.append(f"Current code mismatch: {label}/{name}")
        if meta["schedule_storage"] == "full":
            actual_full.add(label)
            if not (path.parent / "schedule.csv").exists() or not (path.parent / "allocations.csv").exists():
                problems.append(f"Incomplete full artifact: {label}")
        elif any((path.parent / name).exists() for name in ("schedule.csv", "allocations.csv")):
            problems.append(f"Unexpected large scan CSV: {label}")
        metric = read(path.parent / "metrics.json")
        if metric["jobs"] != 224112 or sum(metric[f"{state}_at_90d"] for state in ("completed", "running", "waiting")) != 224112:
            problems.append(f"Population mismatch: {label}")
        rates = pd.read_csv(path.parent / "metrics_by_assigned_model.csv")
        for resource in ("gpu", "cpu"):
            if not rates[f"{resource}_allocation_rate_90d"].between(-1e-12, 1+1e-12).all():
                problems.append(f"Invalid {resource} allocation rate: {label}")
        if meta.get("simulation_wall_seconds") is not None:
            timings.append(meta["simulation_wall_seconds"])
    if expected_full != actual_full:
        problems.append(f"Saved full set differs: expected={sorted(expected_full)}, actual={sorted(actual_full)}")
    equal_recommendations = None
    primary = chosen3["primary_selected_scenario"]
    conditional = chosen3["conditional_selected_scenario"]
    if primary != "v2_base" and conditional != "v2_base":
        equal_recommendations = True
        for filename, keys in [("schedule.csv", ["job_name"]), ("allocations.csv", ["job_name", "node_name", "worker_count"])]:
            frames = [pd.read_csv(q3 / name / filename, dtype={"job_name": str, "node_name": str}, float_precision="round_trip").sort_values(keys).reset_index(drop=True) for name in (primary, conditional)]
            try:
                pd.testing.assert_frame_equal(*frames, check_dtype=False, check_exact=True)
            except AssertionError:
                equal_recommendations = False
    table2, table3 = pd.read_csv(q2 / "comparison_v2.csv"), pd.read_csv(q3 / "comparison.csv")
    if len(table2) != 22 or int((table2.policy == "log_aging").sum()) != 6:
        problems.append("Q2 grid incomplete")
    if len(table3) != 19 or int((table3.phase == "main").sum()) != 11 or int((table3.phase == "sensitivity").sum()) != 7:
        problems.append("Q3 grid incomplete")
    if chosen2["legacy_candidates_only_selected"] != "alpha_1":
        problems.append("Legacy-only selection sanity check failed")
    named = chosen3["primary_selected_scenario"]
    if named != "v2_base":
        winner = table3.set_index("scenario").loc[named]
        if winner.includes_anonymous_donor or not winner.management_feasible or not winner.selection_eligible:
            problems.append("Primary recommendation violates named/main/management requirements")
    for path in [(q2 / "v2_candidates" / name) for name in table2[table2.policy != "log_aging"].scenario] + [(q2 / "log_aging_sweep" / name) for name in table2[table2.policy == "log_aging"].scenario]:
        for filename in ("tail_metrics.json", "tail_by_duration_group.csv", "tail_cohorts.csv", "tail_model_distribution.csv"):
            if not (path / filename).exists():
                problems.append(f"Missing tail artifact: {path.name}/{filename}")
    for filename in ("v2_regression.json", "scheduling_validation.json"):
        report = read(ROOT / "outputs" / filename)
        if report.get("passed") is not True:
            problems.append(f"Validation not passed: {filename}")
    documents = [ROOT / name for name in ("README.md", "CURRENT_STATUS.md", "reports/B_Q23_analysis_zh.md", "reports/B_Q23_report_en.md", "reports/B_Q23_methods_zh.md", "reports/paper_outline.md")]
    links = 0
    for path in documents:
        for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', path.read_text(encoding="utf-8-sig")):
            if "://" in target or target.startswith("#"):
                continue
            links += 1
            if not (path.parent / target.split("#", 1)[0]).exists():
                problems.append(f"Broken link: {path.name}: {target}")
    for base, name in [(q2 / "figures_v2", "04_policy_tradeoff"), (q2 / "figures_v2", "06_group_fairness"),
                       (q3 / "figures", "05_benefit_vs_native_cost"), (q3 / "figures", "06_allocation_imbalance")]:
        for ext in ("png", "svg"):
            if not (base / f"{name}.{ext}").is_file():
                problems.append(f"Missing figure: {base}/{name}.{ext}")
    log = (ROOT / "outputs/v2_unit_tests.txt").read_text(encoding="utf-8-sig")
    tests = re.search(r"Ran (\d+) tests", log)
    if not tests or not re.search(r"\bOK\s*$", log):
        problems.append("Unit-test completion log missing or not OK")
    result = {"passed": not problems, "failure_count": len(problems), "failures": problems,
              "v2_manifests_checked": len(manifests), "q2_candidates": len(table2), "q3_simulated_configs": len(table3)-1,
              "full_scenarios": sorted(actual_full), "unit_tests": int(tests.group(1)) if tests else None,
              "local_links_checked": links, "new_figure_files": 8,
              "primary_and_conditional_full_schedule_allocation_exactly_equal": equal_recommendations,
              "minimum_simulation_seconds": min(timings), "maximum_simulation_seconds": max(timings),
              "core_sha256": sha(ROOT / "src/scheduler.py")}
    (ROOT / "outputs/v2_delivery_review.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
