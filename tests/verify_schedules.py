"""Independently verify complete scheduling CSVs against the original trace.

This verifier uses only the Python standard library and does not import the
scheduler. Decimal arithmetic preserves fractional CPU requests. Intervals are
half open: departures and arrivals at the same timestamp are aggregated before
checking resource use. It verifies feasibility and accounting, not optimality or
whether the scheduler followed its queue-priority policy.
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "2026_MCM_Senior_GPU_Scheduling" / "2026_MCM_Senior_GPU_Scheduling"
ZERO = Decimal(0)
TIME_TOLERANCE = Decimal("0.000001")
WORK_ABSOLUTE_TOLERANCE = Decimal("0.001")
WORK_RELATIVE_TOLERANCE = Decimal("1e-12")
DONOR_MODELS = {"A100-SXM4-80GB", "A800-SXM4-80GB", "H800", "GPU-series-2"}
DEFAULT_DONORS = ("A100-SXM4-80GB", "A800-SXM4-80GB")


@dataclass(slots=True)
class Job:
    model: str
    gpu: int
    cpu: Decimal
    workers: int
    submit: Decimal
    duration: Decimal


@dataclass(slots=True)
class Node:
    model: str
    gpu: int
    cpu: Decimal


@dataclass(slots=True)
class Run:
    model: str
    start: Decimal
    end: Decimal
    duration: Decimal
    migrated: bool
    start_sequence: int | None = None


class Checks:
    def __init__(self) -> None:
        self.comparisons = 0
        self.failure_count = 0
        self.failures: list[str] = []

    def check(self, condition: bool, message: str) -> bool:
        self.comparisons += 1
        if not condition:
            self.failure_count += 1
            if len(self.failures) < 100:
                self.failures.append(message)
        return condition

    def close_time(self, actual: Decimal, expected: Decimal, message: str) -> bool:
        return self.check(abs(actual - expected) <= TIME_TOLERANCE, message)

    def close_work(self, actual: Decimal, expected: Decimal, message: str) -> bool:
        tolerance = max(WORK_ABSOLUTE_TOLERANCE, abs(expected) * WORK_RELATIVE_TOLERANCE)
        return self.check(abs(actual - expected) <= tolerance, message)


def number(value: object) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError(f"nonfinite number: {value!r}")
    return result


def integer(value: object) -> int:
    result = number(value)
    if result != result.to_integral_value():
        raise ValueError(f"noninteger value: {value!r}")
    return int(result)


def boolean(value: object) -> bool:
    text = str(value).strip().lower()
    if text in {"true", "1"}:
        return True
    if text in {"false", "0"}:
        return False
    raise ValueError(f"not a boolean: {value!r}")


def rows(path: Path, required: set[str]):
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path.name}: missing columns {sorted(missing)}")
        yield from enumerate(reader, 2)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_sources(data_dir: Path, checks: Checks):
    jobs: dict[str, Job] = {}
    nodes: dict[str, Node] = {}
    for line, row in rows(data_dir / "job_info_clean.csv", {
        "job_name", "gpu_model", "gpu_request", "cpu_request", "worker_num", "submit_time", "duration"
    }):
        key = row["job_name"]
        job = Job(row["gpu_model"], integer(row["gpu_request"]), number(row["cpu_request"]),
                  integer(row["worker_num"]), number(row["submit_time"]), number(row["duration"]))
        checks.check(bool(key) and key not in jobs, f"source job line {line}: empty or duplicate ID {key}")
        checks.check(job.gpu > 0 and job.cpu >= 0 and job.workers > 0 and job.submit >= 0 and job.duration > 0,
                     f"source job {key}: invalid numeric domain")
        jobs[key] = job
    for line, row in rows(data_dir / "node_info_df.csv", {"node_name", "gpu_model", "gpu_capacity_num", "cpu_num"}):
        key = row["node_name"]
        node = Node(row["gpu_model"], integer(row["gpu_capacity_num"]), number(row["cpu_num"]))
        checks.check(bool(key) and key not in nodes, f"source node line {line}: empty or duplicate ID {key}")
        checks.check(node.gpu > 0 and node.cpu >= 0, f"source node {key}: invalid capacity")
        nodes[key] = node
    checks.check(bool(jobs) and bool(nodes), "source files must contain jobs and nodes")
    return jobs, nodes


def sweep(events, gpu_capacity: int, cpu_capacity: Decimal, label: str, checks: Checks):
    """Independently integrate a node/pool's sorted signed allocation events."""
    events.sort(key=lambda item: item[0])
    active_gpu, active_cpu = 0, ZERO
    peak_gpu, peak_cpu = 0, ZERO
    gpu_work, cpu_work = ZERO, ZERO
    previous = ZERO
    index = 0
    while index < len(events):
        moment = events[index][0]
        elapsed = moment - previous
        gpu_work += active_gpu * elapsed
        cpu_work += active_cpu * elapsed
        while index < len(events) and events[index][0] == moment:
            _, gpu_delta, cpu_delta = events[index]
            active_gpu += gpu_delta
            active_cpu += cpu_delta
            index += 1
        checks.check(0 <= active_gpu <= gpu_capacity,
                     f"{label} at {moment}: GPU allocation {active_gpu} outside [0, {gpu_capacity}]")
        checks.check(ZERO <= active_cpu <= cpu_capacity,
                     f"{label} at {moment}: CPU allocation {active_cpu} outside [0, {cpu_capacity}]")
        peak_gpu = max(peak_gpu, active_gpu)
        peak_cpu = max(peak_cpu, active_cpu)
        previous = moment
    checks.check(active_gpu == 0 and active_cpu == 0, f"{label}: final resource balance is not zero")
    return {"gpu_work_seconds": gpu_work, "cpu_work_seconds": cpu_work,
            "peak_gpu": peak_gpu, "peak_cpu": peak_cpu, "event_count": len(events)}


def verify_migration_reserve(runs, jobs, nodes, donor_models, reserve_fraction, checks):
    """Check admission-time headroom, respecting saved same-time start order.

    Completions release resources before starts at that time. Every start,
    native or migrated, affects subsequent headroom. Only migration starts have
    the reserve constraint: later native starts may legally consume the reserve.
    """
    events_by_model = defaultdict(list)
    for key, run in runs.items():
        if run.model not in donor_models:
            continue
        job = jobs[key]
        gpu, cpu = job.gpu * job.workers, job.cpu * job.workers
        events_by_model[run.model].append((run.end, 0, 0, key, -gpu, -cpu, False))
        events_by_model[run.model].append((run.start, 1, run.start_sequence, key, gpu, cpu, run.migrated))
    result = {}
    for model in sorted(donor_models):
        gpu_capacity = sum(node.gpu for node in nodes.values() if node.model == model)
        cpu_capacity = sum((node.cpu for node in nodes.values() if node.model == model), ZERO)
        gpu_floor, cpu_floor = reserve_fraction * gpu_capacity, reserve_fraction * cpu_capacity
        busy_gpu, busy_cpu, starts_checked = 0, ZERO, 0
        min_free_gpu, min_free_cpu = None, None
        for moment, phase, sequence, key, gpu_delta, cpu_delta, migrated in sorted(events_by_model[model]):
            busy_gpu += gpu_delta
            busy_cpu += cpu_delta
            if phase == 1 and migrated:
                starts_checked += 1
                free_gpu, free_cpu = gpu_capacity - busy_gpu, cpu_capacity - busy_cpu
                checks.check(free_gpu >= gpu_floor,
                             f"job {key}, {model} at {moment}, start_sequence {sequence}: free GPU {free_gpu} below reserve {gpu_floor}")
                checks.check(free_cpu >= cpu_floor,
                             f"job {key}, {model} at {moment}, start_sequence {sequence}: free CPU {free_cpu} below reserve {cpu_floor}")
                min_free_gpu = free_gpu if min_free_gpu is None else min(min_free_gpu, free_gpu)
                min_free_cpu = free_cpu if min_free_cpu is None else min(min_free_cpu, free_cpu)
        result[model] = {"migration_starts_checked": starts_checked, "required_free_gpu": str(gpu_floor),
                         "required_free_cpu": str(cpu_floor), "minimum_free_gpu_after_migration_start": min_free_gpu,
                         "minimum_free_cpu_after_migration_start": str(min_free_cpu) if min_free_cpu is not None else None}
    return result


def verify_scenario(scenario_dir: Path, data_dir: Path = DEFAULT_DATA) -> dict:
    """Return a JSON-compatible verification report without writing files."""
    checks = Checks()
    source_files = [data_dir / "job_info_clean.csv", data_dir / "node_info_df.csv"]
    scenario_files = [scenario_dir / name for name in ("scenario.json", "schedule.csv", "allocations.csv")]
    jobs, nodes = load_sources(data_dir, checks)
    with scenario_files[0].open(encoding="utf-8-sig") as handle:
        metadata = json.load(handle, parse_float=Decimal)
    routing = metadata.get("routing")
    if routing is not None:
        speedup = number(routing["speedup"])
        cap_fraction = number(routing["donor_cap_fraction"])
        flexible_fraction = number(routing["flexible_fraction"])
        routing_seed = integer(routing["seed"])
        donor_list = routing.get("donors", DEFAULT_DONORS)
        donor_models = set(donor_list)
        checks.check(len(donor_list) == len(donor_models), "routing donors must not contain duplicate models")
        checks.check(donor_models.issubset(DONOR_MODELS), "routing contains donors outside the declared A100/A800/H800/GPU-series-2 alternatives")
        routing_mode = routing.get("mode", "arrival")
        checks.check(routing_mode in {"arrival", "pull"}, "routing mode must be arrival or pull")
        min_wait, donor_caps = ZERO, {}
        max_routed_duration, reserve_fraction = None, ZERO
        if routing_mode == "pull":
            min_wait = number(routing.get("min_wait_seconds", 0))
            checks.check(min_wait >= 0, "pull minimum wait must be nonnegative")
            scan_limit = integer(routing.get("pull_scan_limit", 256))
            checks.check(scan_limit >= 0, "pull scan limit must be a nonnegative integer")
            if routing.get("max_routed_duration_seconds") is not None:
                max_routed_duration = number(routing["max_routed_duration_seconds"])
                checks.check(max_routed_duration >= 0, "maximum routed original duration must be nonnegative")
            reserve_fraction = number(routing.get("donor_reserve_fraction", 0))
            checks.check(0 <= reserve_fraction <= 1, "donor reserve fraction must lie in [0, 1]")
            for pair in routing.get("donor_caps", []):
                if not checks.check(isinstance(pair, (tuple, list)) and len(pair) == 2,
                                    "pull donor cap overrides must be model/fraction pairs"):
                    continue
                model, fraction_value = pair
                fraction = number(fraction_value)
                checks.check(model not in donor_caps, f"duplicate pull donor cap override for {model}")
                checks.check(0 <= fraction <= 1, f"pull donor cap fraction for {model} must lie in [0, 1]")
                donor_caps[model] = fraction
        # Arrival mode deliberately ignores the new override/minimum-wait
        # fields so old routing semantics remain exactly unchanged.
        checks.check(speedup > 0, "routing speedup must be positive")
        checks.check(0 <= cap_fraction <= 1, "routing donor cap fraction must lie in [0, 1]")
        checks.check(0 <= flexible_fraction <= 1, "routing flexible fraction must lie in [0, 1]")
        if speedup <= 0:
            raise ValueError("routing speedup must be positive")
    else:
        speedup, cap_fraction, flexible_fraction = Decimal(1), ZERO, ZERO
        routing_seed, donor_models = 0, set()
        routing_mode, min_wait, donor_caps = None, ZERO, {}
        max_routed_duration, reserve_fraction = None, ZERO

    runs: dict[str, Run] = {}
    expected_work = defaultdict(lambda: [ZERO, ZERO])
    migration_events = defaultdict(list)
    migrated_count = 0
    schedule_rows = 0
    schedule_columns = {"job_name", "requested_model", "assigned_model", "submit_time", "start_time", "end_time",
                        "original_duration", "run_duration", "wait_time", "gpu_request", "cpu_request", "worker_num", "migrated", "flexible"}
    require_start_sequence = routing_mode == "pull" and reserve_fraction > 0
    if require_start_sequence:
        schedule_columns.add("start_sequence")
    start_sequences = set()
    for line, row in rows(scenario_files[1], schedule_columns):
        schedule_rows += 1
        key = row["job_name"]
        if not checks.check(key in jobs and key not in runs, f"schedule line {line}: unknown or duplicate job {key}"):
            continue
        source = jobs[key]
        try:
            start, end = number(row["start_time"]), number(row["end_time"])
            duration, wait = number(row["run_duration"]), number(row["wait_time"])
            migrated = boolean(row["migrated"])
            flexible = boolean(row["flexible"])
            gpu, cpu, workers = integer(row["gpu_request"]), number(row["cpu_request"]), integer(row["worker_num"])
            submit, original_duration = number(row["submit_time"]), number(row["original_duration"])
            start_sequence = integer(row["start_sequence"]) if require_start_sequence else None
        except (ValueError, InvalidOperation) as exc:
            checks.check(False, f"schedule job {key}: {exc}")
            continue
        assigned_model = row["assigned_model"]
        if require_start_sequence:
            checks.check(start_sequence > 0 and start_sequence not in start_sequences,
                         f"job {key}: start_sequence must be positive and unique")
            start_sequences.add(start_sequence)
        checks.check(row["requested_model"] == source.model, f"job {key}: requested model changed")
        checks.check(submit == source.submit and original_duration == source.duration, f"job {key}: source timing changed")
        checks.check(gpu == source.gpu and cpu == source.cpu and workers == source.workers,
                     f"job {key}: worker resource requirements changed")
        checks.check(start >= source.submit, f"job {key}: starts before submission")
        checks.check(end > start and duration > 0 and wait >= 0, f"job {key}: nonpositive runtime or negative wait")
        checks.close_time(wait, start - source.submit, f"job {key}: wait time inconsistent with start")
        checks.close_time(end - start, duration, f"job {key}: end/start interval differs from runtime")
        checks.check(migrated == (assigned_model != source.model), f"job {key}: migrated flag inconsistent with assigned model")
        eligible = False
        if routing is not None and source.model == "A10":
            digest = hashlib.sha256(f"{routing_seed}:{key}".encode("utf-8")).digest()
            eligible = int.from_bytes(digest[:8], "big") / 2**64 < float(flexible_fraction)
        checks.check(flexible == eligible, f"job {key}: flexible flag differs from the fixed-seed hash selection")
        if migrated:
            migrated_count += 1
            checks.check(routing is not None, f"job {key}: migration in strict-model scenario")
            checks.check(source.model == "A10" and assigned_model in donor_models, f"job {key}: disallowed model substitution")
            checks.check(eligible, f"job {key}: migrated job was not selected as flexible")
            if routing_mode == "pull":
                checks.check(start >= source.submit + min_wait,
                             f"job {key}: pull migration starts before submission plus minimum wait")
                if max_routed_duration is not None:
                    checks.check(source.duration <= max_routed_duration,
                                 f"job {key}: original duration exceeds maximum routed duration")
            else:
                checks.close_time(start, source.submit, f"job {key}: migrated job must start at submission")
            true_duration = source.duration / speedup
            total_gpu, total_cpu = source.gpu * source.workers, source.cpu * source.workers
            migration_events[assigned_model].extend([(start, total_gpu, total_cpu), (end, -total_gpu, -total_cpu)])
        else:
            true_duration = source.duration
        checks.close_time(duration, true_duration, f"job {key}: runtime does not match the scenario assumption")
        runs[key] = Run(assigned_model, start, end, duration, migrated, start_sequence)
        expected_work[assigned_model][0] += source.gpu * source.workers * true_duration
        expected_work[assigned_model][1] += source.cpu * source.workers * true_duration
    checks.check(set(runs) == set(jobs), f"schedule IDs differ from source: {len(set(jobs) - set(runs))} missing")
    checks.check(schedule_rows == len(jobs), f"schedule has {schedule_rows} rows for {len(jobs)} source jobs")
    reserve_checks = {}
    if require_start_sequence:
        ordered_runs = sorted(runs.values(), key=lambda run: run.start_sequence)
        checks.check(all(first.start <= second.start for first, second in zip(ordered_runs, ordered_runs[1:])),
                     "start_sequence order disagrees with chronological job starts")
        checks.check(start_sequences == set(range(1, len(jobs) + 1)), "start_sequence must cover all job starts exactly once")
        reserve_checks = verify_migration_reserve(runs, jobs, nodes, donor_models, reserve_fraction, checks)

    workers_by_job = defaultdict(int)
    events_by_node = defaultdict(list)
    allocation_work = defaultdict(lambda: [ZERO, ZERO])
    allocation_rows = 0
    for line, row in rows(scenario_files[2], {"job_name", "node_name", "worker_count"}):
        allocation_rows += 1
        key, node_key = row["job_name"], row["node_name"]
        if not checks.check(key in runs and node_key in nodes, f"allocation line {line}: unknown job or node {key}/{node_key}"):
            continue
        source, run, node = jobs[key], runs[key], nodes[node_key]
        try:
            workers = integer(row["worker_count"])
        except (ValueError, InvalidOperation) as exc:
            checks.check(False, f"allocation line {line}: {exc}")
            continue
        if not checks.check(workers > 0, f"allocation line {line}: worker count must be positive integer"):
            continue
        start, end = run.start, run.end
        checks.check(run.model == node.model,
                     f"allocation job {key}, node {node_key}: incompatible assigned model")
        # A placement row covers the one uninterrupted interval of its unique
        # schedule record. It cannot represent worker-specific starts, slices,
        # or preemption. Verify redundant fields too if a producer adds them.
        for field, expected in (("start_time", start), ("end_time", end)):
            if field in row:
                checks.close_time(number(row[field]), expected, f"job {key}, node {node_key}: inconsistent {field}")
        if "assigned_model" in row:
            checks.check(row["assigned_model"] == run.model, f"job {key}, node {node_key}: inconsistent assigned model")
        gpu, cpu = workers * source.gpu, workers * source.cpu
        checks.check(source.gpu <= node.gpu and source.cpu <= node.cpu,
                     f"job {key}, node {node_key}: worker cannot fit on the node")
        workers_by_job[key] += workers
        events_by_node[node_key].extend([(start, gpu, cpu), (end, -gpu, -cpu)])
        allocation_work[run.model][0] += gpu * (end - start)
        allocation_work[run.model][1] += cpu * (end - start)
    for key, source in jobs.items():
        checks.check(workers_by_job[key] == source.workers,
                     f"job {key}: allocated {workers_by_job[key]} workers, expected {source.workers}")

    integrated_work = defaultdict(lambda: [ZERO, ZERO])
    node_peaks = {}
    all_events = 0
    for key, node in nodes.items():
        stats = sweep(events_by_node[key], node.gpu, node.cpu, f"node {key}", checks)
        integrated_work[node.model][0] += stats["gpu_work_seconds"]
        integrated_work[node.model][1] += stats["cpu_work_seconds"]
        all_events += stats["event_count"]
        node_peaks[key] = {"model": node.model, "peak_gpu": stats["peak_gpu"], "gpu_capacity": node.gpu,
                           "peak_cpu": str(stats["peak_cpu"]), "cpu_capacity": str(node.cpu)}
    work_totals = {}
    for model in sorted(set(expected_work) | set(integrated_work)):
        gpu_expected, cpu_expected = expected_work[model]
        gpu_allocated, cpu_allocated = allocation_work[model]
        gpu_integrated, cpu_integrated = integrated_work[model]
        for resource, expected, allocated, integrated in (
            ("GPU", gpu_expected, gpu_allocated, gpu_integrated),
            ("CPU", cpu_expected, cpu_allocated, cpu_integrated),
        ):
            checks.close_work(allocated, expected, f"{model}: allocated {resource} work differs from original job/scenario work")
            checks.close_work(integrated, allocated, f"{model}: event-integrated {resource} work differs from allocation work")
        work_totals[model] = {"expected_gpu_seconds": str(gpu_expected), "allocated_gpu_seconds": str(gpu_allocated),
                              "integrated_gpu_seconds": str(gpu_integrated), "expected_cpu_seconds": str(cpu_expected),
                              "allocated_cpu_seconds": str(cpu_allocated), "integrated_cpu_seconds": str(cpu_integrated)}

    donor_checks = {}
    if routing is not None:
        for model in sorted(donor_models):
            capacity_gpu = sum(node.gpu for node in nodes.values() if node.model == model)
            capacity_cpu = sum((node.cpu for node in nodes.values() if node.model == model), ZERO)
            effective_fraction = donor_caps.get(model, cap_fraction)
            gpu_cap = int((effective_fraction * capacity_gpu).to_integral_value(rounding=ROUND_FLOOR))
            cpu_cap = effective_fraction * capacity_cpu
            stats = sweep(migration_events[model], gpu_cap, cpu_cap, f"borrowed pool {model}", checks)
            donor_checks[model] = {"cap_fraction": str(effective_fraction), "gpu_cap": gpu_cap, "cpu_cap": str(cpu_cap), "peak_borrowed_gpu": stats["peak_gpu"],
                                   "peak_borrowed_cpu": str(stats["peak_cpu"])}
    return {
        "passed": checks.failure_count == 0,
        "comparisons": checks.comparisons,
        "failure_count": checks.failure_count,
        "failures": checks.failures,
        "source_job_count": len(jobs), "source_node_count": len(nodes),
        "schedule_rows": schedule_rows, "allocation_rows": allocation_rows,
        "migrated_jobs": migrated_count, "node_event_count": all_events,
        "routing_mode": routing_mode, "migration_min_wait_seconds": str(min_wait),
        "maximum_routed_original_duration_seconds": str(max_routed_duration) if max_routed_duration is not None else None,
        "donor_reserve_fraction": str(reserve_fraction), "donor_reserve_start_checks": reserve_checks,
        "work_by_assigned_model": work_totals,
        "donor_cap_checks": donor_checks,
        "node_resource_peaks": node_peaks,
        "file_sha256": {str(path): sha256(path) for path in source_files + scenario_files},
        "verifier_sha256": sha256(Path(__file__)),
        "method": "Independent standard-library CSV/Decimal verification; no scheduler imports; half-open node event sweep.",
        "numeric_tolerances": {"seconds_absolute": str(TIME_TOLERANCE), "resource_capacity": "0 (exact Decimal)",
                               "work_absolute_seconds": str(WORK_ABSOLUTE_TOLERANCE), "work_relative": str(WORK_RELATIVE_TOLERANCE)},
        "limitations": ["Feasibility and accounting verification does not prove optimality or queue-priority implementation.",
                        "Each allocation row occupies its unique job's entire start/end interval; workers share that non-preemptive interval by representation.",
                        "Compatibility and speedup in migration scenarios are declared assumptions, not trace observations."],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    args = parser.parse_args()
    output_path = args.scenario_dir / "independent_validation.json"
    try:
        report = verify_scenario(args.scenario_dir, args.data_dir)
    except (OSError, ValueError, KeyError, InvalidOperation, csv.Error) as exc:
        report = {"passed": False, "failure_count": 1, "failures": [f"Input/verification error: {exc}"]}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("passed", "failure_count")} | {"report": str(output_path)}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
