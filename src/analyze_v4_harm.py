"""Reconstruct local head-blocking evidence from existing schedules, without simulation.

At each harmed A10 job's baseline start time, inspect the changed schedule after
all events at that time. A feasible victim behind an infeasible higher-priority
head witnesses instantaneous head blocking, not causation of its entire delay.
"""
from __future__ import annotations

import heapq
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from run_v2_experiments import digest, write_json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/v4_validation/harm_diagnostics'
DATA = ROOT/'2026_MCM_Senior_GPU_Scheduling/2026_MCM_Senior_GPU_Scheduling'
T = 604800.


def slots(gpu, cpu, request_gpu, request_cpu):
    available = gpu // request_gpu
    if request_cpu > 0:
        available = np.minimum(available, np.floor((cpu+1e-9)/request_cpu).astype(np.int64))
    return int(available.sum())


def diagnose(base, changed, allocations, nodes):
    base = base.set_index('job_name')
    changed = changed.set_index('job_name')
    if base.index.has_duplicates or changed.index.has_duplicates or set(base.index) != set(changed.index):
        raise ValueError('Need identical unique job ID sets')
    for field in ('requested_model', 'submit_time', 'original_duration', 'gpu_request', 'cpu_request', 'worker_num'):
        if not changed[field].eq(base.loc[changed.index, field]).all():
            raise ValueError(f'Original attribute mismatch: {field}')
    source = changed[changed.requested_model.eq('A10')].copy()
    source['base_start'] = base.loc[source.index, 'start_time']
    source['delta_hours'] = (source.start_time-source.base_start)/3600
    harmed = source[source.delta_hours > 0].sort_values(['base_start'], kind='stable')
    pool = nodes[nodes.gpu_model.eq('A10')].sort_values('node_name')
    index = {name: i for i, name in enumerate(pool.node_name)}
    free_gpu = pool.gpu_capacity_num.to_numpy(dtype=np.int64).copy()
    free_cpu = pool.cpu_num.to_numpy(dtype=float).copy()
    local = allocations[allocations.node_name.isin(index)].join(
        changed[['start_time', 'end_time', 'gpu_request', 'cpu_request']], on='job_name')
    event_time = np.r_[local.end_time.to_numpy(), local.start_time.to_numpy()]
    event_node = np.tile(local.node_name.map(index).to_numpy(dtype=int), 2)
    gpu_units = (local.worker_count*local.gpu_request).to_numpy(dtype=np.int64)
    cpu_units = (local.worker_count*local.cpu_request).to_numpy(dtype=float)
    event_gpu = np.r_[gpu_units, -gpu_units]
    event_cpu = np.r_[cpu_units, -cpu_units]
    # Stable sort releases before starts. Queries are after every event at t.
    order = np.argsort(event_time, kind='stable')
    event_time, event_node = event_time[order], event_node[order]
    event_gpu, event_cpu = event_gpu[order], event_cpu[order]
    arrivals = sorted((float(r.submit_time), str(name), r) for name, r in source.iterrows())
    cursor = arrival_cursor = 0
    queue = []
    rows = []
    current_time = None
    for name, job in harmed.iterrows():
        now = float(job.base_start)
        if current_time != now:
            stop = int(np.searchsorted(event_time, now, side='right'))
            np.add.at(free_gpu, event_node[cursor:stop], event_gpu[cursor:stop])
            np.add.at(free_cpu, event_node[cursor:stop], event_cpu[cursor:stop])
            cursor = stop
            while arrival_cursor < len(arrivals) and arrivals[arrival_cursor][0] <= now:
                submit, identifier, arriving = arrivals[arrival_cursor]
                heapq.heappush(queue, (math.log(arriving.original_duration)+submit/T, submit, identifier))
                arrival_cursor += 1
            while queue and source.loc[queue[0][2], 'start_time'] <= now:
                heapq.heappop(queue)
            if not queue:
                raise AssertionError('Harmed job should still be waiting')
            head_name = queue[0][2]
            head = source.loc[head_name]
            head_slots = slots(free_gpu, free_cpu, int(head.gpu_request), float(head.cpu_request))
            if (free_gpu < 0).any() or (free_cpu < -1e-7).any():
                raise AssertionError('Negative reconstructed source capacity')
            current_time = now
        job_slots = slots(free_gpu, free_cpu, int(job.gpu_request), float(job.cpu_request))
        feasible = job_slots >= int(job.worker_num)
        head_feasible = head_slots >= int(head.worker_num)
        if not feasible:
            category = 'not_placeable_on_realized_timeline'
        elif head_name != name and not head_feasible:
            category = 'feasible_behind_infeasible_head'
        else:
            category = 'other_requires_investigation'
        rows.append({'job_name': name, 'baseline_start_seconds': now, 'new_start_seconds': job.start_time,
                     'wait_increase_hours': job.delta_hours, 'assigned_model': job.assigned_model,
                     'job_gpus': int(job.gpu_request*job.worker_num), 'job_workers': int(job.worker_num),
                     'original_duration_days': job.original_duration/86400,
                     'job_available_worker_slots': job_slots, 'head_job_name': head_name,
                     'head_workers': int(head.worker_num), 'head_job_gpus': int(head.gpu_request*head.worker_num),
                     'head_available_worker_slots': head_slots, 'free_gpus': int(free_gpu.sum()),
                     'category': category})
    details = pd.DataFrame(rows)
    source['migration_group'] = np.where(source.assigned_model.eq('A10'), 'stayed_A10', 'migrated')
    source['duration_group'] = pd.cut(source.original_duration, [0, 3600, 86400, 7*86400, np.inf],
                                    labels=['<=1h', '1h-1d', '1d-7d', '>7d'])
    source['gpu_group'] = pd.cut(source.gpu_request*source.worker_num, [0, 1, 8, np.inf], labels=['1', '2-8', '>8'])
    groups = []
    for field in ('migration_group', 'duration_group', 'gpu_group'):
        for label, group in source.groupby(field, observed=True):
            delta = group.delta_hours.to_numpy()
            positive = delta[delta > 0]
            groups.append({'dimension': field, 'group': str(label), 'jobs': len(group),
                           'harmed_jobs': int((delta > 0).sum()), 'harmed_fraction': float((delta > 0).mean()),
                           'net_job_hours_saved': float(-math.fsum(delta)),
                           'positive_job_hours_added': float(math.fsum(positive)),
                           'conditional_harm_mean_hours': float(positive.mean()) if len(positive) else 0.})
    counts = details.category.value_counts().to_dict() if len(details) else {}
    return details, pd.DataFrame(groups), {
        'source_jobs': len(source), 'harmed_jobs': len(details), 'category_counts': counts,
        'category_fractions_of_harmed': {k: v/len(details) for k, v in counts.items()},
        'interpretation': 'Snapshots after events at baseline start times on the realized changed timeline; no causal attribution of full delay',
        'cpu_slot_tolerance': 1e-9, 'time_unit': 'seconds; delta in hours', 'queue_key': 'log(original_duration_seconds)+submit_time/604800'}


def main():
    base_path = ROOT/'outputs/question2/q2_star/schedule.csv'
    base = pd.read_csv(base_path, dtype={'job_name': str}, float_precision='round_trip')
    nodes = pd.read_csv(DATA/'node_info_df.csv', dtype={'node_name': str}, float_precision='round_trip')
    for label, folder in [('existing_strict', 'outputs/question3/v3/v3_ah_d60'),
                          ('existing_compensation', 'outputs/question3/v3/v2_reference/v2_ah')]:
        path = ROOT/folder
        changed = pd.read_csv(path/'schedule.csv', dtype={'job_name': str}, float_precision='round_trip')
        alloc = pd.read_csv(path/'allocations.csv', dtype={'job_name': str, 'node_name': str}, float_precision='round_trip')
        details, groups, summary = diagnose(base, changed, alloc, nodes)
        destination = OUT/label
        destination.mkdir(parents=True, exist_ok=True)
        details.to_csv(destination/'harmed_snapshots.csv', index=False)
        groups.to_csv(destination/'harm_groups.csv', index=False)
        details.sort_values(['wait_increase_hours', 'job_name'], ascending=[False, True]).groupby(
            'category', sort=True).head(3).to_csv(destination/'examples.csv', index=False)
        summary['input_sha256'] = {p.relative_to(ROOT).as_posix(): digest(p) for p in
                                  (base_path, path/'schedule.csv', path/'allocations.csv', DATA/'node_info_df.csv')}
        summary['code_sha256'] = digest(Path(__file__))
        write_json(destination/'summary.json', summary)
        print(json.dumps({'scenario': label, **summary}), flush=True)


if __name__ == '__main__':
    main()
