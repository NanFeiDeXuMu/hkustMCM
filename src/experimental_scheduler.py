"""Versioned experimental engine with an explicit pull-admission boundary.

Event semantics are copied from frozen scheduler.py (SHA256
714e94bdc388abd63a9274e67c394157eb6e06bfb5a6ffba0a8a0320acb10966).
Shared preparation, queues and placement are imported from that reference.
With admission=None every schedule and allocation must match the reference.
The bounded fork preserves historical executable provenance while admitting
new policies; it is not a claim that the complete engine has been refactored.
"""
from collections import OrderedDict
from dataclasses import asdict
import heapq
from itertools import islice
import math
import time

import numpy as np
import pandas as pd

from scheduler import EPS, RoutingConfig, _Job, _Pool, _Running, _prepare
from routing_admission import RuntimeAdmission


def run_simulation(jobs: pd.DataFrame, nodes: pd.DataFrame, policy: str = "fcfs", *,
                   routing: RoutingConfig | None = None, max_backfill_scan: int = 128,
                   aging_alpha: float = 0.0, log_aging_tau_seconds: float = 86400.0,
                   admission: RuntimeAdmission | None = None
                   ) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Schedule every job to completion; no truncation at the arrival horizon.

    Policies: FCFS blocks behind its oldest job; SJF blocks behind its shortest
    waiting job; backfill protects the FCFS head's earliest start while scanning
    at most the next ``max_backfill_scan`` jobs, shortest eligible duration first.
    Aging blocks behind the smallest static key ``duration + alpha * submit``;
    alpha=0 exactly matches SJF. Aging does not require time-based heap updates.
    Log aging uses ``log(duration_seconds) + submit / log_aging_tau_seconds``
    with the same strict head blocking; its time scale must be finite and >0.
    A worker's CPU and GPU allocations always occupy the same physical node.

    Routing is an optional Q3 scenario, not inferred hardware equivalence. Both
    modes require an empty donor native queue and immediate whole-job placement.
    Arrival mode checks only newly submitted A10 jobs after native dispatch. Pull
    mode revisits eligible waiting jobs and respects an optional minimum wait,
    original-duration ceiling, and a pool-level CPU/GPU free-capacity reserve.
    Arrival mode ignores the pull-only controls, including the ceiling/reserve.
    """
    if admission is not None:
        if routing is None or routing.mode != "pull":
            raise ValueError("Runtime admission requires pull routing")
        admission.validate_donors(routing.donors)
    started_clock = time.perf_counter()
    if policy not in {"fcfs", "sjf", "backfill", "aging", "log_aging"}:
        raise ValueError("policy must be fcfs, sjf, backfill, aging, or log_aging")
    if not math.isfinite(aging_alpha) or aging_alpha < 0:
        raise ValueError("aging_alpha must be finite and nonnegative")
    if policy == "log_aging" and (not math.isfinite(log_aging_tau_seconds) or log_aging_tau_seconds <= 0):
        raise ValueError("log_aging_tau_seconds must be finite and positive")
    if not isinstance(max_backfill_scan, int) or max_backfill_scan < 0:
        raise ValueError("max_backfill_scan must be a nonnegative integer")
    arrivals, pools = _prepare(jobs, nodes, policy, routing, aging_alpha, log_aging_tau_seconds)
    pull_mode = routing is not None and routing.mode == "pull"
    flexible_waiting: OrderedDict[int, _Job] = OrderedDict()
    zero_cpu_flexible: set[int] = set()
    eligibility_events: list[tuple[float, str, int]] = []
    donor_cap_overrides = dict(routing.donor_caps) if pull_mode else {}
    pending_completions: list[tuple[float, str, str, int]] = []
    schedule_rows, allocation_rows = [], []
    stats = {"event_count": 0, "placement_checks": 0, "reservations_computed": 0,
             "backfilled_jobs": 0, "routing_candidates": 0, "migrated_jobs": 0,
             "pull_candidates_checked": 0, "eligibility_wakeups": 0,
             "max_backfill_scan": max_backfill_scan, "policy": policy,
             "aging_alpha": aging_alpha if policy == "aging" else None,
             "routing": asdict(routing) if routing else None}
    if policy == "log_aging":
        stats["log_aging_tau_seconds"] = log_aging_tau_seconds
    stats["admission"] = asdict(admission) if admission else None
    stats["runtime_admission_rejections"] = 0
    sequence = 0

    def start(pool: _Pool, job: _Job, now: float, placement: list[tuple[int, int]],
              duration: float | None = None, migrated: bool = False, backfilled: bool = False):
        nonlocal sequence
        duration = job.duration if duration is None else duration
        end = now + duration
        if not math.isfinite(end) or end <= now:
            raise ValueError("Runtime is too small/large for finite floating event time")
        if pull_mode and job.requested_model == "A10":
            flexible_waiting.pop(job.index, None)
            zero_cpu_flexible.discard(job.index)
        for node, workers in placement:
            pool.free_gpu[node] -= workers * job.gpu
            pool.free_cpu[node] -= workers * job.cpu
            if pool.free_gpu[node] < 0 or pool.free_cpu[node] < -EPS:
                raise AssertionError("Node resource capacity exceeded")
            allocation_rows.append({"job_name": job.name, "node_name": pool.node_names[node],
                                    "worker_count": workers})
        pool.free_gpu_total -= job.total_gpu
        pool.free_cpu_total -= job.total_cpu
        if migrated:
            pool.borrowed_gpus += job.total_gpu
            pool.peak_borrowed_gpus = max(pool.peak_borrowed_gpus, pool.borrowed_gpus)
            pool.borrowed_cpus += job.total_cpu
            pool.peak_borrowed_cpus = max(pool.peak_borrowed_cpus, pool.borrowed_cpus)
            stats["migrated_jobs"] += 1
        if backfilled:
            stats["backfilled_jobs"] += 1
        pool.running[job.index] = _Running(job, end, placement, migrated)
        heapq.heappush(pending_completions, (end, pool.model, job.name, job.index))
        sequence += 1
        schedule_rows.append({"job_name": job.name, "requested_model": job.requested_model,
                              "assigned_model": pool.model, "submit_time": job.submit,
                              "start_time": now, "end_time": end,
                              "original_duration": job.duration, "run_duration": duration,
                              "wait_time": now - job.submit, "gpu_request": job.gpu,
                              "cpu_request": job.cpu, "worker_num": job.workers,
                              "migrated": migrated, "flexible": job.flexible,
                              "backfilled": backfilled, "start_sequence": sequence})

    def dispatch(pool: _Pool, now: float):
        while pool.queue:
            head = pool.head()
            assert head is not None
            stats["placement_checks"] += 1
            placement = pool.placement(head)
            if placement is not None:
                pool.remove(head)
                start(pool, head, now, placement)
                continue
            if (pool.reservation_head == head.index and pool.reservation_time is not None
                    and pool.reservation_time <= now):
                raise AssertionError("Protected head cannot start at its reserved time")
            # No candidate requests zero GPUs: when none are free, computing a
            # reservation cannot lead to a backfill start at this event.
            if (policy != "backfill" or max_backfill_scan == 0
                    or len(pool.queue) == 1 or pool.free_gpu_total == 0):
                break
            if pool.reservation_head != head.index:
                stats["reservations_computed"] += 1
            reservation = pool.reserve(head)
            if reservation <= now:
                raise AssertionError("Protected head cannot start at its reserved time")
            # Preserve the first 128 FCFS successors as the admission horizon;
            # sorting never scans farther into the queue for a convenient job.
            candidates = []
            for position, job in enumerate(pool.queue.values()):
                if position == 0:
                    continue
                if position > max_backfill_scan:
                    break
                if now + job.duration <= reservation:
                    candidates.append(job)
            candidates.sort(key=lambda job: (job.duration, job.submit, job.name))
            for candidate in candidates:
                stats["placement_checks"] += 1
                placement = pool.placement(candidate)
                if placement is not None:
                    pool.remove(candidate)
                    start(pool, candidate, now, placement, backfilled=True)
            break

    arrival_index = 0
    while arrival_index < len(arrivals) or pending_completions or eligibility_events:
        # Stale wakeups must not extend a finished simulation or use any future
        # arrival information: they were created only after that job arrived.
        while eligibility_events and eligibility_events[0][2] not in flexible_waiting:
            heapq.heappop(eligibility_events)
        next_arrival = arrivals[arrival_index].submit if arrival_index < len(arrivals) else math.inf
        next_completion = pending_completions[0][0] if pending_completions else math.inf
        next_eligibility = eligibility_events[0][0] if eligibility_events else math.inf
        now = min(next_arrival, next_completion, next_eligibility)
        if not math.isfinite(now):
            break
        stats["event_count"] += 1
        for pool in pools.values():
            pool.advance(now)
        affected = set()
        # Every completion at this exact timestamp is released before arrivals.
        while pending_completions and pending_completions[0][0] <= now:
            _, model, _, index = heapq.heappop(pending_completions)
            pool = pools[model]
            record = pool.running.pop(index)
            for node, workers in record.placement:
                pool.free_gpu[node] += workers * record.job.gpu
                pool.free_cpu[node] += workers * record.job.cpu
                if pool.free_gpu[node] > pool.gpu_capacity[node] or pool.free_cpu[node] > pool.cpu_capacity[node] + EPS:
                    raise AssertionError("Release exceeded node capacity")
            pool.free_gpu_total += record.job.total_gpu
            pool.free_cpu_total += record.job.total_cpu
            if record.migrated:
                pool.borrowed_gpus -= record.job.total_gpu
                pool.borrowed_cpus -= record.job.total_cpu
            affected.add(model)
        new_a10 = []
        while arrival_index < len(arrivals) and arrivals[arrival_index].submit <= now:
            job = arrivals[arrival_index]
            pools[job.requested_model].enqueue(job)
            affected.add(job.requested_model)
            if job.requested_model == "A10":
                new_a10.append(job)
                # Duration-ineligible jobs retain the compatibility flag but
                # cannot occupy the bounded pull prefix or create wakeups.
                if (pull_mode and job.flexible
                        and (routing.max_routed_duration_seconds is None
                             or job.duration <= routing.max_routed_duration_seconds)
                        and (admission is None or any(
                            admission.allows(job.duration, routing.speedup, model)
                            for model in routing.donors if model in pools))):
                    flexible_waiting[job.index] = job
                    if job.cpu == 0:
                        zero_cpu_flexible.add(job.index)
            arrival_index += 1
        for model in sorted(affected):
            dispatch(pools[model], now)

        if pull_mode and routing.min_wait_seconds > 0:
            for job in new_a10:
                if job.index in flexible_waiting:
                    eligible_at = job.submit + routing.min_wait_seconds
                    if not math.isfinite(eligible_at) or eligible_at <= job.submit:
                        raise ValueError("Minimum wait is too small/large for finite event time")
                    heapq.heappush(eligibility_events, (eligible_at, job.name, job.index))
            while eligibility_events and eligibility_events[0][0] <= now:
                _, _, index = heapq.heappop(eligibility_events)
                if index in flexible_waiting:
                    stats["eligibility_wakeups"] += 1

        if routing is not None and routing.mode == "arrival" and new_a10:
            source = pools["A10"]
            for job in new_a10:
                if not job.flexible or job.index not in source.queue:
                    continue
                stats["routing_candidates"] += 1
                source_work_ahead = max(0.0, source.remaining_gpu_work - job.total_gpu * job.duration)
                predicted_native_finish = now + source_work_ahead / source.total_gpu + job.duration
                donor_duration = job.duration / routing.speedup
                best = None
                for donor_order, model in enumerate(routing.donors):
                    if model not in pools:
                        continue
                    donor = pools[model]
                    borrow_limit = math.floor(routing.donor_cap_fraction * donor.total_gpu)
                    cpu_borrow_limit = routing.donor_cap_fraction * donor.total_cpu
                    if (donor.queue or donor.borrowed_gpus + job.total_gpu > borrow_limit
                            or donor.borrowed_cpus + job.total_cpu > cpu_borrow_limit + EPS):
                        continue
                    predicted_donor_finish = now + donor_duration
                    if predicted_donor_finish >= predicted_native_finish:
                        continue
                    stats["placement_checks"] += 1
                    placement = donor.placement(job)
                    if placement is not None:
                        key = (predicted_donor_finish, donor_order)
                        if best is None or key < best[0]:
                            best = (key, donor, placement)
                if best is not None:
                    _, donor, placement = best
                    source.remove(job)
                    source.remaining_gpu_work = max(0.0, source.remaining_gpu_work - job.total_gpu * job.duration)
                    donor.remaining_gpu_work += job.total_gpu * donor_duration
                    start(donor, job, now, placement, duration=donor_duration, migrated=True)
            # Removing a source head can let the native queue advance now.
            dispatch(source, now)

        elif pull_mode and flexible_waiting:
            source = pools["A10"]
            moved = False
            for model in routing.donors:
                if model not in pools or not flexible_waiting:
                    continue
                donor = pools[model]
                cap_fraction = donor_cap_overrides.get(model, routing.donor_cap_fraction)
                gpu_limit = math.floor(cap_fraction * donor.total_gpu)
                cpu_limit = cap_fraction * donor.total_cpu
                if (donor.queue or donor.free_gpu_total == 0 or donor.borrowed_gpus >= gpu_limit
                        or (donor.borrowed_cpus >= cpu_limit - EPS and not zero_cpu_flexible)):
                    continue
                # Snapshot only the bounded prefix. Starts remove entries from
                # the live OrderedDict, so iterating that dict directly is unsafe.
                candidates = list(islice(flexible_waiting.values(), routing.pull_scan_limit))
                for job in candidates:
                    stats["pull_candidates_checked"] += 1
                    if now < job.submit + routing.min_wait_seconds:
                        break
                    if donor.free_gpu_total == 0 or donor.borrowed_gpus >= gpu_limit:
                        break
                    stats["routing_candidates"] += 1
                    if admission is not None and not admission.allows(job.duration, routing.speedup, model):
                        stats["runtime_admission_rejections"] += 1
                        continue
                    if (donor.borrowed_gpus + job.total_gpu > gpu_limit
                            or donor.borrowed_cpus + job.total_cpu > cpu_limit + EPS):
                        continue
                    # Reserve current physical idle capacity after this start.
                    # Native dispatch may consume it at later events. Skipping
                    # this check at R=0 preserves the original pull arithmetic.
                    reserve = routing.donor_reserve_fraction
                    if reserve > 0 and (
                            donor.free_gpu_total - job.total_gpu < reserve * donor.total_gpu
                            or donor.free_cpu_total - job.total_cpu + EPS < reserve * donor.total_cpu):
                        continue
                    stats["placement_checks"] += 1
                    placement = donor.placement(job)
                    if placement is None:
                        continue
                    source.remove(job)
                    source.remaining_gpu_work = max(0.0, source.remaining_gpu_work - job.total_gpu * job.duration)
                    donor_duration = job.duration / routing.speedup
                    donor.remaining_gpu_work += job.total_gpu * donor_duration
                    start(donor, job, now, placement, duration=donor_duration, migrated=True)
                    moved = True
            if moved:
                dispatch(source, now)

    if any(pool.queue or pool.running for pool in pools.values()) or len(schedule_rows) != len(arrivals):
        raise RuntimeError("Simulation ended with unscheduled or unfinished jobs")
    for pool in pools.values():
        if not np.array_equal(pool.free_gpu, pool.gpu_capacity) or not np.allclose(pool.free_cpu, pool.cpu_capacity, rtol=0, atol=EPS):
            raise AssertionError("Resources not fully restored after completion")
    schedule_columns = ["job_name", "requested_model", "assigned_model", "submit_time", "start_time", "end_time",
                        "original_duration", "run_duration", "wait_time", "gpu_request", "cpu_request", "worker_num",
                        "migrated", "flexible", "backfilled", "start_sequence"]
    schedule = pd.DataFrame(schedule_rows, columns=schedule_columns)
    if not schedule.empty:
        schedule = schedule.sort_values(["submit_time", "job_name"], kind="stable").reset_index(drop=True)
    allocation = pd.DataFrame(allocation_rows, columns=["job_name", "node_name", "worker_count"])
    stats.update({"jobs_scheduled": len(schedule), "allocation_rows": len(allocation),
                  "simulated_until": float(schedule.end_time.max()) if len(schedule) else 0.0,
                  "elapsed_seconds": time.perf_counter() - started_clock,
                  "peak_borrowed_gpus": {model: pool.peak_borrowed_gpus for model, pool in pools.items()},
                  "peak_borrowed_cpus": {model: pool.peak_borrowed_cpus for model, pool in pools.items()},
                  "routing_rule": ("native dispatch first; eligible waiting A10 prefix; immediate donor start; repeated pull"
                                   if pull_mode else
                                   "native dispatch first; newly arrived waiting A10 only; immediate donor start; no retries"),
                  "tie_breaking": "submission time then string job_name; node_name string order",
                  "backfill_rule": "first lookahead FCFS successors; finish by protected head reservation"})
    return schedule, allocation, stats
