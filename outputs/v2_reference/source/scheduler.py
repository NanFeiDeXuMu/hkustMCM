"""Deterministic online, non-preemptive gang scheduling on physical GPU nodes.

Each event first releases completions, then admits all simultaneous arrivals and
runs native scheduling. Optional Q3 routing starts selected A10 jobs directly on
a compatible donor. Arrival routing only checks the submitting batch; pull
routing can revisit waiting jobs, including at explicit minimum-wait wakeups.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass
import hashlib
import heapq
from itertools import islice
import math
import time

import numpy as np
import pandas as pd

EPS = 1e-9


@dataclass(frozen=True)
class RoutingConfig:
    """Explicit hypothetical cross-model compatibility and speed assumptions."""

    flexible_fraction: float = 0.5
    speedup: float = 1.0
    donor_cap_fraction: float = 0.25
    seed: int = 20261017
    donors: tuple[str, ...] = ("A100-SXM4-80GB", "A800-SXM4-80GB")
    mode: str = "arrival"
    donor_caps: tuple[tuple[str, float], ...] = ()
    min_wait_seconds: float = 0.0
    pull_scan_limit: int = 256

    def __post_init__(self):
        if not 0 <= self.flexible_fraction <= 1:
            raise ValueError("flexible_fraction must be in [0, 1]")
        if not math.isfinite(self.speedup) or self.speedup <= 0:
            raise ValueError("speedup must be finite and positive")
        if not 0 <= self.donor_cap_fraction <= 1:
            raise ValueError("donor_cap_fraction must be in [0, 1]")
        if len(set(self.donors)) != len(self.donors) or "A10" in self.donors:
            raise ValueError("donors must be distinct alternatives to A10")
        if self.mode not in {"arrival", "pull"}:
            raise ValueError("routing mode must be arrival or pull")
        # These fields do not participate in the legacy arrival rule. Validate
        # and interpret them only when pull routing is actually requested.
        if self.mode == "pull":
            if not math.isfinite(self.min_wait_seconds) or self.min_wait_seconds < 0:
                raise ValueError("min_wait_seconds must be finite and nonnegative")
            if not isinstance(self.pull_scan_limit, int) or self.pull_scan_limit < 0:
                raise ValueError("pull_scan_limit must be a nonnegative integer")
            names = [model for model, _ in self.donor_caps]
            if len(set(names)) != len(names):
                raise ValueError("donor_caps must not repeat a model")
            if any(not math.isfinite(cap) or not 0 <= cap <= 1 for _, cap in self.donor_caps):
                raise ValueError("Each donor cap must be finite and in [0, 1]")


def is_flexible(job_name: str, config: RoutingConfig) -> bool:
    """Stable selection; does not use submission time, duration, or future jobs."""
    digest = hashlib.sha256(f"{config.seed}:{job_name}".encode("utf-8")).digest()
    draw = int.from_bytes(digest[:8], "big") / 2**64
    return draw < config.flexible_fraction


@dataclass
class _Job:
    index: int
    name: str
    requested_model: str
    gpu: int
    cpu: float
    workers: int
    submit: float
    duration: float
    flexible: bool = False

    @property
    def total_gpu(self) -> int:
        return self.gpu * self.workers

    @property
    def total_cpu(self) -> float:
        return self.cpu * self.workers


@dataclass
class _Running:
    job: _Job
    end: float
    placement: list[tuple[int, int]]
    migrated: bool


class _Pool:
    def __init__(self, model: str, nodes: pd.DataFrame, policy: str, aging_alpha: float):
        self.model = model
        ordered = nodes.sort_values("node_name", kind="stable")
        self.node_names = ordered.node_name.astype(str).to_numpy()
        self.gpu_capacity = ordered.gpu_capacity_num.to_numpy(dtype=np.int64)
        self.cpu_capacity = ordered.cpu_num.to_numpy(dtype=float)
        self.free_gpu = self.gpu_capacity.copy()
        self.free_cpu = self.cpu_capacity.copy()
        self.total_gpu = int(self.gpu_capacity.sum())
        self.total_cpu = float(self.cpu_capacity.sum())
        self.free_gpu_total = self.total_gpu
        self.free_cpu_total = self.total_cpu
        self.queue: OrderedDict[int, _Job] = OrderedDict()
        self.sjf_heap: list[tuple[float, float, str, int]] = []
        self.running: dict[int, _Running] = {}
        self.policy = policy
        self.aging_alpha = aging_alpha
        self.reservation_head: int | None = None
        self.reservation_time: float | None = None
        self.remaining_gpu_work = 0.0
        self.last_time = 0.0
        self.borrowed_gpus = 0
        self.peak_borrowed_gpus = 0
        self.borrowed_cpus = 0.0
        self.peak_borrowed_cpus = 0.0

    def advance(self, now: float) -> None:
        self.remaining_gpu_work -= (self.total_gpu - self.free_gpu_total) * (now - self.last_time)
        # Only workload accounting uses this float; placement uses exact integer
        # GPU counts and per-node CPU quantities. Tiny cancellation is harmless.
        self.remaining_gpu_work = max(0.0, self.remaining_gpu_work)
        self.last_time = now

    def enqueue(self, job: _Job) -> None:
        self.queue[job.index] = job
        self.remaining_gpu_work += job.total_gpu * job.duration
        if self.policy in {"sjf", "aging"}:
            # The common -alpha*now term cancels from all waiting priorities.
            # Preserve the original SJF key expression for exact regression.
            score = job.duration if self.policy == "sjf" else job.duration + self.aging_alpha * job.submit
            if not math.isfinite(score):
                raise ValueError("Aging priority overflowed; use a smaller finite alpha")
            heapq.heappush(self.sjf_heap, (score, job.submit, job.name, job.index))

    def head(self) -> _Job | None:
        if not self.queue:
            return None
        if self.policy in {"sjf", "aging"}:
            while self.sjf_heap and self.sjf_heap[0][3] not in self.queue:
                heapq.heappop(self.sjf_heap)
            return self.queue[self.sjf_heap[0][3]]
        return next(iter(self.queue.values()))

    def remove(self, job: _Job) -> None:
        self.queue.pop(job.index)
        if self.reservation_head == job.index:
            self.reservation_head = None
            self.reservation_time = None

    @staticmethod
    def worker_slots(job: _Job, gpu: np.ndarray, cpu: np.ndarray) -> np.ndarray:
        result = gpu // job.gpu
        if job.cpu > 0:
            result = np.minimum(result, np.floor((cpu + EPS) / job.cpu).astype(np.int64))
        return result

    def placement(self, job: _Job) -> list[tuple[int, int]] | None:
        if job.total_gpu > self.free_gpu_total or job.total_cpu > self.free_cpu_total + EPS:
            return None
        slots = self.worker_slots(job, self.free_gpu, self.free_cpu)
        available = np.flatnonzero(slots > 0)
        if int(slots[available].sum()) < job.workers:
            return None
        # Fill the least spacious feasible nodes first, preserving larger holes.
        order = np.lexsort((available, self.free_cpu[available], self.free_gpu[available], slots[available]))
        remaining = job.workers
        placement = []
        for node in available[order]:
            count = min(remaining, int(slots[node]))
            placement.append((int(node), count))
            remaining -= count
            if remaining == 0:
                return placement
        raise AssertionError("Feasibility and placement disagreed")

    def reserve(self, job: _Job) -> float:
        """Earliest feasible head start using only jobs already running.

        CPU/GPU releases update worker-slot counts only on affected nodes. A
        cached reservation remains protected because every backfill ends no
        later than it; the current head is nevertheless retried at each event.
        """
        if self.reservation_head == job.index:
            assert self.reservation_time is not None
            return self.reservation_time
        gpu = self.free_gpu.copy()
        cpu = self.free_cpu.copy()
        slots = self.worker_slots(job, gpu, cpu)
        total_slots = int(slots.sum())
        running = sorted(self.running.values(), key=lambda r: (r.end, r.job.name))
        position = 0
        while position < len(running):
            release_time = running[position].end
            changed = set()
            while position < len(running) and running[position].end == release_time:
                record = running[position]
                for node, workers in record.placement:
                    gpu[node] += workers * record.job.gpu
                    cpu[node] += workers * record.job.cpu
                    changed.add(node)
                position += 1
            indexes = np.fromiter(changed, dtype=np.int64)
            updated = self.worker_slots(job, gpu[indexes], cpu[indexes])
            total_slots += int(updated.sum()) - int(slots[indexes].sum())
            slots[indexes] = updated
            if total_slots >= job.workers:
                self.reservation_head = job.index
                self.reservation_time = release_time
                return release_time
        raise RuntimeError(f"No reservation possible for job {job.name}")


def _prepare(jobs: pd.DataFrame, nodes: pd.DataFrame, policy: str,
             routing: RoutingConfig | None, aging_alpha: float) -> tuple[list[_Job], dict[str, _Pool]]:
    job_columns = {"job_name", "gpu_model", "gpu_request", "cpu_request", "worker_num", "submit_time", "duration"}
    node_columns = {"node_name", "gpu_model", "gpu_capacity_num", "cpu_num"}
    if not job_columns.issubset(jobs.columns) or not node_columns.issubset(nodes.columns):
        raise ValueError("Missing required input columns")
    if nodes[list(node_columns)].isna().any().any() or jobs[list(job_columns)].isna().any().any():
        raise ValueError("Missing values in required inputs")
    nodes = nodes.copy()
    nodes["node_name"] = nodes.node_name.astype(str)
    if nodes.empty or nodes.node_name.duplicated().any() or jobs.job_name.astype(str).duplicated().any():
        raise ValueError("Need nonempty nodes and unique job/node identifiers")
    for row in nodes.itertuples(index=False):
        if not (math.isfinite(float(row.gpu_capacity_num)) and math.isfinite(float(row.cpu_num))
                and row.gpu_capacity_num > 0 and row.gpu_capacity_num == int(row.gpu_capacity_num)
                and row.cpu_num > 0):
            raise ValueError("Invalid node capacity")
    pools = {str(model): _Pool(str(model), group, policy, aging_alpha)
             for model, group in nodes.groupby("gpu_model", sort=True)}
    if routing is not None and "A10" not in pools:
        raise ValueError("Routing requires an A10 source pool")
    prepared = []
    feasible_cache = {}
    for index, row in enumerate(jobs.itertuples(index=False)):
        values = [row.gpu_request, row.cpu_request, row.worker_num, row.submit_time, row.duration]
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("Nonfinite job field")
        if (row.gpu_request <= 0 or row.gpu_request != int(row.gpu_request)
                or row.worker_num <= 0 or row.worker_num != int(row.worker_num)
                or row.cpu_request < 0 or row.submit_time < 0 or row.duration <= 0):
            raise ValueError("Invalid job resource, count, or time")
        job = _Job(index, str(row.job_name), str(row.gpu_model), int(row.gpu_request),
                   float(row.cpu_request), int(row.worker_num), float(row.submit_time), float(row.duration))
        if job.requested_model not in pools:
            raise ValueError(f"Unknown GPU model for job {job.name}")
        if routing is not None and job.requested_model == "A10":
            job.flexible = is_flexible(job.name, routing)
        key = (job.requested_model, job.gpu, job.cpu, job.workers)
        if key not in feasible_cache:
            pool = pools[job.requested_model]
            feasible_cache[key] = int(pool.worker_slots(job, pool.gpu_capacity, pool.cpu_capacity).sum()) >= job.workers
        if not feasible_cache[key]:
            raise ValueError(f"Job {job.name} cannot fit its empty native pool")
        prepared.append(job)
    prepared.sort(key=lambda job: (job.submit, job.name))
    return prepared, pools


def run_simulation(jobs: pd.DataFrame, nodes: pd.DataFrame, policy: str = "fcfs", *,
                   routing: RoutingConfig | None = None, max_backfill_scan: int = 128,
                   aging_alpha: float = 0.0
                   ) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Schedule every job to completion; no truncation at the arrival horizon.

    Policies: FCFS blocks behind its oldest job; SJF blocks behind its shortest
    waiting job; backfill protects the FCFS head's earliest start while scanning
    at most the next ``max_backfill_scan`` jobs, shortest eligible duration first.
    Aging blocks behind the smallest static key ``duration + alpha * submit``;
    alpha=0 exactly matches SJF. Aging does not require time-based heap updates.
    A worker's CPU and GPU allocations always occupy the same physical node.

    Routing is an optional Q3 scenario, not inferred hardware equivalence. Both
    modes require an empty donor native queue and immediate whole-job placement.
    Arrival mode checks only newly submitted A10 jobs after native dispatch. Pull
    mode revisits eligible waiting jobs and respects an optional minimum wait.
    """
    started_clock = time.perf_counter()
    if policy not in {"fcfs", "sjf", "backfill", "aging"}:
        raise ValueError("policy must be fcfs, sjf, backfill, or aging")
    if not math.isfinite(aging_alpha) or aging_alpha < 0:
        raise ValueError("aging_alpha must be finite and nonnegative")
    if not isinstance(max_backfill_scan, int) or max_backfill_scan < 0:
        raise ValueError("max_backfill_scan must be a nonnegative integer")
    arrivals, pools = _prepare(jobs, nodes, policy, routing, aging_alpha)
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
                if pull_mode and job.flexible:
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
                    if (donor.borrowed_gpus + job.total_gpu > gpu_limit
                            or donor.borrowed_cpus + job.total_cpu > cpu_limit + EPS):
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
