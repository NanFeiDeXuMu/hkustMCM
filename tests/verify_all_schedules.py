"""Audit saved full schedules, distinguish metric-only scans, preserve old audits.

New manifests declare ``schedule_storage`` as ``full`` or ``metrics_only``.
An absent field means a legacy full-output manifest. All existing audit files
are kept byte for byte; their source/output hashes are checked before reuse.
Historical feasibility evidence does not prove regression of a new scheduler.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from verify_schedules import DEFAULT_DATA, sha256, verify_scenario

ROOT = Path(__file__).resolve().parents[1]


def preserved_report(directory: Path, data_dir: Path) -> dict:
    """Authenticate prior feasibility evidence without overwriting that file."""
    report = json.loads((directory / "independent_validation.json").read_text(encoding="utf-8-sig"))
    recorded = report.get("file_sha256", {})
    paths = [data_dir / "job_info_clean.csv", data_dir / "node_info_df.csv"]
    paths += [directory / name for name in ("scenario.json", "schedule.csv", "allocations.csv")]
    for path in paths:
        # Basenames permit moving an otherwise identical project directory;
        # the five artifacts have distinct names and their content is hashed.
        candidates = [digest for name, digest in recorded.items() if Path(name).name == path.name]
        if len(candidates) != 1 or candidates[0] != sha256(path):
            raise ValueError(f"Preserved audit hash does not match {path.name}")
    if report.get("passed") is not True or report.get("failure_count") != 0:
        raise ValueError("Preserved audit did not pass")
    return report


def audit_saved_scenarios(outputs_root: Path, data_dir: Path = DEFAULT_DATA, *, verbose: bool = True) -> dict:
    """Audit recursively; metric-only outputs never count as feasibility passes."""
    records, skipped = [], []
    discovered = 0
    for suite in ("question2", "question3"):
        for manifest_path in sorted((outputs_root / suite).rglob("scenario.json")):
            discovered += 1
            directory = manifest_path.parent
            relative = directory.relative_to(outputs_root).as_posix()
            record = {"suite": suite, "scenario": directory.relative_to(outputs_root / suite).as_posix(),
                      "relative_path": relative}
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
                storage = manifest.get("schedule_storage", "full")
                if storage not in {"full", "metrics_only"}:
                    raise ValueError(f"Unrecognized schedule_storage value: {storage!r}")
                schedule_path = directory / "schedule.csv"
                allocation_path = directory / "allocations.csv"
                if storage == "metrics_only":
                    if schedule_path.exists() or allocation_path.exists():
                        raise ValueError("metrics_only manifest has unexpected schedule/allocation CSV artifacts")
                    record.update(status="skipped_metrics", reason="No per-job/per-node artifacts saved; no independent feasibility audit.")
                    skipped.append(record)
                    if verbose:
                        print(json.dumps(record), flush=True)
                    continue
                missing = [path.name for path in (schedule_path, allocation_path) if not path.is_file()]
                if missing:
                    raise ValueError(f"Full schedule manifest is missing required CSV files: {', '.join(missing)}")
                preserve = (directory / "independent_validation.json").is_file()
                if preserve:
                    report = preserved_report(directory, data_dir)
                    status = "preserved_history"
                    record["legacy_manifest"] = "schedule_storage" not in manifest
                else:
                    report = verify_scenario(directory, data_dir)
                    (directory / "independent_validation.json").write_text(
                        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    status = "audited_new"
                record.update(status=status, passed=report["passed"], comparisons=report.get("comparisons", 0),
                              failure_count=report["failure_count"], schedule_rows=report.get("schedule_rows", 0),
                              allocation_rows=report.get("allocation_rows", 0))
                if not report["passed"]:
                    record["failures"] = report.get("failures", [])
            except Exception as error:
                # Preserve prior audit evidence even when manifests/files have
                # become inconsistent; the batch summary records the failure.
                record.update(status="failed", passed=False, comparisons=0, failure_count=1,
                              failures=[f"{type(error).__name__}: {error}"])
            records.append(record)
            if verbose:
                print(json.dumps(record), flush=True)
    fresh = [record for record in records if record["status"] == "audited_new"]
    prior = [record for record in records if record["status"] == "preserved_history"]
    return {
        "passed": bool(records) and all(record["passed"] for record in records),
        "discovered_scenarios": discovered,
        "scenarios": len(records),
        "audited_new": len(fresh),
        "preserved_history": len(prior),
        "preserved_legacy": sum(record["legacy_manifest"] for record in prior),
        "skipped_metrics": len(skipped),
        "comparisons": sum(record["comparisons"] for record in records),
        "current_run_comparisons": sum(record["comparisons"] for record in fresh),
        "failure_count": sum(record["failure_count"] for record in records),
        "results": records,
        "skipped": skipped,
        "scope": "Only full schedule/placement CSVs receive independent feasibility audits. Metric-only scans are explicitly skipped. Preserved audits are hash-authenticated historical evidence, not proof that current scheduler code reproduces earlier schedules; separate regression evidence is required.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outputs-root", type=Path, default=ROOT / "outputs")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--summary-path", type=Path, help="Default: <outputs-root>/scheduling_validation.json")
    args = parser.parse_args()
    summary = audit_saved_scenarios(args.outputs_root, args.data_dir)
    summary_path = args.summary_path or args.outputs_root / "scheduling_validation.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
