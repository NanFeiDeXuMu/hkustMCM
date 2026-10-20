"""Online A10 shadow scheduling and expiring physical-node loans.

The shadow sees only admitted arrivals and its running jobs. A loan occupies
a node that the shadow already reserves for an offloaded/early-started job.
Every borrower finishes before that reservation expires. On one-GPU A10 nodes
the entire node is unavailable to any other shadow GPU job during that period,
so its otherwise idle CPU may be used subject to physical capacity.
"""
from dataclasses import dataclass
import heapq
import math

import numpy as np

from scheduler import EPS, _Pool, _Running


@dataclass(frozen=True)
class SourceProtection:
    loan_scan: int = 0

    def __post_init__(self):
        if type(self.loan_scan) is not int or self.loan_scan < 0:
            raise ValueError('loan_scan must be a nonnegative integer')


class ShadowSource:
    def __init__(self, nodes, tau):
        self.pool = _Pool('A10', nodes, 'log_aging', 0., tau)
        if not np.all(self.pool.gpu_capacity == 1):
            raise ValueError('Source protection requires one GPU per A10 node')
        self.completions = []
        self.starts = 0

    @property
    def next_completion(self):
        return self.completions[0][0] if self.completions else math.inf

    def enqueue(self, job):
        self.pool.enqueue(job)

    def release(self, now):
        self.pool.advance(now)
        while self.completions and self.completions[0][0] <= now:
            _, _, index = heapq.heappop(self.completions)
            record = self.pool.running.pop(index)
            for node, workers in record.placement:
                self.pool.free_gpu[node] += workers*record.job.gpu
                self.pool.free_cpu[node] += workers*record.job.cpu
            self.pool.free_gpu_total += record.job.total_gpu
            self.pool.free_cpu_total += record.job.total_cpu

    def dispatch(self, now):
        started = []
        while self.pool.queue:
            job = self.pool.head()
            placement = self.pool.placement(job)
            if placement is None:
                break
            end = now+job.duration
            if not math.isfinite(end) or end <= now:
                raise ValueError('Shadow job needs a finite positive execution interval')
            self.pool.remove(job)
            for node, workers in placement:
                self.pool.free_gpu[node] -= workers*job.gpu
                self.pool.free_cpu[node] -= workers*job.cpu
            self.pool.free_gpu_total -= job.total_gpu
            self.pool.free_cpu_total -= job.total_cpu
            record = _Running(job, end, placement, False)
            self.pool.running[job.index] = record
            heapq.heappush(self.completions, (end, job.name, job.index))
            started.append(record)
            self.starts += 1
        return started


class NodeLoans:
    def __init__(self, count):
        self.expiry = np.full(count, -math.inf)
        self.owner = np.full(count, -1, dtype=np.int64)
        self.borrower = np.full(count, -1, dtype=np.int64)
        self.assignments = {}

    def add(self, record):
        for node, workers in record.placement:
            if workers != 1 or record.job.gpu != 1 or self.owner[node] != -1:
                raise AssertionError('Shadow node loan overlaps an existing reservation')
            self.owner[node] = record.job.index
            self.expiry[node] = record.end

    def expire(self, now):
        mask = (self.owner >= 0) & (self.expiry <= now)
        if np.any(self.borrower[mask] >= 0):
            raise AssertionError('Borrower exceeds protected shadow reservation')
        self.owner[mask] = -1
        self.expiry[mask] = -math.inf

    def release(self, job):
        indexes = self.assignments.pop(job.index, None)
        if indexes is not None:
            if not np.all(self.borrower[indexes] == job.index):
                raise AssertionError('Loan ownership changed before completion')
            self.borrower[indexes] = -1

    def available_until(self):
        mask = (self.owner >= 0) & (self.borrower < 0)
        return float(self.expiry[mask].max()) if np.any(mask) else -math.inf

    def placement(self, job, pool, now):
        if job.gpu != 1:
            raise AssertionError('One-GPU source feasibility was not enforced')
        mask = ((self.owner >= 0) & (self.borrower < 0) & (self.expiry >= now+job.duration)
                & (pool.free_gpu >= 1) & (pool.free_cpu+EPS >= job.cpu))
        available = np.flatnonzero(mask)
        if len(available) < job.workers:
            return None
        order = np.lexsort((available, pool.free_cpu[available]))
        return [(int(node), 1) for node in available[order[:job.workers]]]

    def reserve(self, job, placement, now):
        indexes = np.array([node for node, _ in placement], dtype=np.int64)
        if (len(indexes) != job.workers or len(set(indexes)) != len(indexes)
                or not np.all(self.owner[indexes] >= 0)
                or not np.all(self.borrower[indexes] < 0)
                or not np.all(self.expiry[indexes] >= now+job.duration)):
            raise AssertionError('Invalid protected loan allocation')
        self.borrower[indexes] = job.index
        self.assignments[job.index] = indexes
