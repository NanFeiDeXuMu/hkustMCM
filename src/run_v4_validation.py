"""D028: fixed development grid followed by untouched eligibility-seed validation.

Historical artifacts and the scheduler are read-only. This tests eligibility
uncertainty on one trace, not generalization across workloads.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import platform
import time

import numpy as np
import pandas as pd

from analyze_routing_frontier import analyze_routing_frontier
from evaluate_scheduling import evaluate
from run_v2_experiments import digest, flat, read_json, write_json
from run_v3_experiments import CORE_SHA, classify, routing_from_manifest, THRESHOLDS
from scheduler import RoutingConfig, run_simulation

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / '2026_MCM_Senior_GPU_Scheduling/2026_MCM_Senior_GPU_Scheduling'
OUT = ROOT / 'outputs/v4_validation'
BASE = ROOT / 'outputs/question2/q2_star/schedule.csv'
DEVELOPMENT_SEEDS = (20261017, 20261018, 20261019)
VALIDATION_SEEDS = tuple(range(20261020, 20261030))
CODE_FILES = ('scheduler.py', 'evaluate_scheduling.py', 'analyze_routing_frontier.py',
              'run_v2_experiments.py', 'run_v3_experiments.py', 'run_v4_validation.py')


def configurations():
    route = RoutingConfig(mode='pull', donors=('A100-SXM4-80GB', 'H800'))
    result = {f'd{days}_t{hours}h': replace(route, max_routed_duration_seconds=days*86400,
              min_wait_seconds=hours*3600) for days in (30, 45, 60, 75, 90) for hours in (0, 1)}
    result.update({f'unlimited_cap{int(cap*100)}': replace(route, donor_cap_fraction=cap)
                   for cap in (.1, .15, .25)})
    return result


def select_development(table):
    """Reject incomplete seed sets, including duplicates; no validation input."""
    if set(table.phase) != {'development'}:
        raise ValueError('Selection must use development rows only')
    table = classify(table)
    rows = []
    for name, group in table.groupby('configuration', sort=True):
        if sorted(group.seed.tolist()) != list(DEVELOPMENT_SEEDS):
            raise ValueError(f'Incomplete/duplicate development seed set: {name}')
        rows.append({'configuration': name,
                     'eligible': bool(group.strict_feasible.all() and (group.a10_mean_wait_gain_hours > 0).all()),
                     'mean_a10_gain_hours': float(group.a10_mean_wait_gain_hours.mean()),
                     'worst_donor_mean_delta_hours': float(group.max_donor_mean_wait_delta_hours.max())})
    scores = pd.DataFrame(rows)
    eligible = scores[scores.eligible].sort_values(
        ['mean_a10_gain_hours', 'worst_donor_mean_delta_hours', 'configuration'], ascending=[False, True, True])
    return {'selected_configuration': None if eligible.empty else str(eligible.iloc[0].configuration),
            'rule': 'D028', 'development_seeds': list(DEVELOPMENT_SEEDS),
            'scores': scores.to_dict('records'), 'validation_used_for_selection': False}


def wilson(successes, count, z=1.959963984540054):
    if count <= 0 or successes < 0 or successes > count:
        raise ValueError('Need 0 <= successes <= count and count > 0')
    p = successes / count
    denominator = 1 + z*z/count
    center = (p + z*z/(2*count))/denominator
    margin = z*math.sqrt(p*(1-p)/count + z*z/(4*count*count))/denominator
    return max(0., center-margin), min(1., center+margin)


def initialize_worker():
    global JOBS, NODES, BASELINE
    JOBS = pd.read_csv(DATA/'job_info_clean.csv', dtype={'job_name': str}, float_precision='round_trip')
    NODES = pd.read_csv(DATA/'node_info_df.csv', dtype={'node_name': str}, float_precision='round_trip')
    BASELINE = pd.read_csv(BASE, dtype={'job_name': str}, float_precision='round_trip')


def run_case(task):
    name, config_name, phase, route_values, full, provenance, deadline = task
    path = OUT/phase/name
    route = routing_from_manifest(route_values)
    manifest = {'name': name, 'experiment_version': 'v4', 'policy': 'log_aging',
                'log_aging_tau_seconds': 604800, 'routing': asdict(route), 'configuration': config_name,
                'phase': phase, 'baseline': 'q2_star', 'schedule_storage': 'full' if full else 'metrics_only',
                'selection_eligible': phase == 'development', **provenance}
    manifest = json.loads(json.dumps(manifest))
    if (path/'comparison_row.json').exists() and (path/'scenario.json').exists():
        old = read_json(path/'scenario.json')
        if all(old.get(k) == v for k, v in manifest.items()):
            if not full or all((path/f).exists() for f in ('schedule.csv', 'allocations.csv')):
                return read_json(path/'comparison_row.json')
        raise ValueError(f'Refusing to overwrite incompatible evidence: {path}')
    if datetime.now(timezone.utc) >= datetime.fromisoformat(deadline):
        raise TimeoutError('D028 budget expired before starting next simulation')
    if digest(ROOT/'src/scheduler.py') != CORE_SHA:
        raise ValueError('Frozen scheduler changed')
    print(f'Start {phase}/{name}', flush=True)
    began = time.perf_counter()
    schedule, allocations, diagnostics = run_simulation(JOBS, NODES, policy='log_aging',
        log_aging_tau_seconds=604800, routing=route)
    elapsed = time.perf_counter()-began
    metric = evaluate(schedule, NODES, path)
    frontier = analyze_routing_frontier(BASELINE, schedule, NODES, route.donors, path)
    if full:
        schedule.to_csv(path/'schedule.csv', index=False)
        allocations.to_csv(path/'allocations.csv', index=False)
    row = {'scenario': name, 'configuration': config_name, 'phase': phase, 'seed': route.seed,
           'speedup': route.speedup, 'output_path': path.relative_to(ROOT).as_posix(),
           'simulation_wall_seconds': elapsed, 'selection_eligible': phase == 'development',
           **flat(metric), **flat(frontier)}
    row = classify(pd.DataFrame([row])).iloc[0].to_dict()
    manifest.update(diagnostics=diagnostics, simulation_wall_seconds=elapsed)
    write_json(path/'scenario.json', manifest)
    write_json(path/'comparison_row.json', row)
    print(json.dumps({'finished': name, 'phase': phase, 'mean_h': row['wait_hours_mean'],
                      'donor_mean_h': row['max_donor_mean_wait_delta_hours'],
                      'strict_pass': row['strict_feasible']}), flush=True)
    return row


def register():
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT/'protocol.json'
    if not path.exists():
        now = datetime.now(timezone.utc)
        # Snapshot existing evidence before any v4 simulation; do not rewrite history.
        history = {p.relative_to(ROOT).as_posix(): digest(p) for p in (ROOT/'outputs').rglob('*')
                   if p.is_file() and not p.is_relative_to(OUT) and '__pycache__' not in p.parts}
        write_json(OUT/'historical_hashes.json', history)
        write_json(path, {'rule': 'D028', 'registered_utc': now.isoformat(),
                         'deadline_utc': (now+timedelta(hours=3)).isoformat(),
                         'development_seeds': list(DEVELOPMENT_SEEDS), 'validation_seeds': list(VALIDATION_SEEDS),
                         'configurations': {k: asdict(v) for k, v in configurations().items()},
                         'thresholds': THRESHOLDS, 'history_files': len(history),
                         'scope': 'Same trace; new eligibility assignments, not independent workloads'})
    protocol = read_json(path)
    current_configs = json.loads(json.dumps({k: asdict(v) for k, v in configurations().items()}))
    if protocol['configurations'] != current_configs or protocol['validation_seeds'] != list(VALIDATION_SEEDS):
        raise ValueError('Protocol differs from registered grid/seeds')
    return protocol


def provenance():
    return {'source_sha256': {name: digest(DATA/name) for name in ('job_info_clean.csv', 'node_info_df.csv')},
            'code_sha256': {name: digest(ROOT/'src'/name) for name in CODE_FILES},
            'baseline_schedule_sha256': digest(BASE),
            'versions': {'python': platform.python_version(), 'numpy': np.__version__, 'pandas': pd.__version__}}


def execute(tasks, workers):
    with ProcessPoolExecutor(max_workers=workers, initializer=initialize_worker) as pool:
        futures = [pool.submit(run_case, task) for task in tasks]
        rows = [future.result() for future in as_completed(futures)]
    return pd.DataFrame(rows).sort_values(['configuration', 'seed', 'speedup'])


def summarize_validation(table, aliases):
    rows = []
    for label, name in aliases.items():
        group = table[table.configuration == name]
        if sorted(group.seed.tolist()) != list(VALIDATION_SEEDS):
            raise ValueError(f'Incomplete validation: {label}')
        criterion = 'compensation_feasible' if label == 'existing_compensation' else 'strict_feasible'
        passed = group[criterion].astype(bool) & (group.a10_mean_wait_gain_hours > 0)
        count = int(passed.sum())
        low, high = wilson(count, len(group))
        row = {'label': label, 'configuration': name, 'criterion': criterion, 'passed': count, 'total': len(group),
               'wilson95_low': low, 'wilson95_high': high}
        for field in ('wait_hours_mean', 'wait_hours_max', 'a10_mean_wait_gain_hours',
                      'max_donor_mean_wait_delta_hours', 'max_donor_p95_delta_hours',
                      'a10_wait_increased_fraction', 'max_donor_native_wait_increased_fraction'):
            row.update({f'{field}_{stat}': float(getattr(group[field], stat)()) for stat in ('mean', 'min', 'max')})
        rows.append(row)
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('all', 'development', 'validation', 'stress'), default='all')
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()
    protocol = register()
    prov = provenance()
    configs = configurations()
    def task(name, configuration, phase, route, full=False):
        return (name, configuration, phase, asdict(route), full, prov, protocol['deadline_utc'])
    if args.phase in ('all', 'development'):
        tasks = [task(f'{name}_s{seed}', name, 'development', replace(route, seed=seed))
                 for name, route in configs.items() for seed in DEVELOPMENT_SEEDS]
        table = execute(tasks, args.workers)
        table.to_csv(OUT/'development.csv', index=False)
        choice = select_development(table)
        choice['locked_utc'] = datetime.now(timezone.utc).isoformat()
        write_json(OUT/'development_selection.json', choice)
        print(json.dumps(choice), flush=True)
    choice = read_json(OUT/'development_selection.json')
    aliases = {'existing_strict': 'd60_t0h', 'existing_compensation': 'unlimited_cap25'}
    if choice['selected_configuration'] is not None:
        aliases['new_candidate'] = choice['selected_configuration']
    write_json(OUT/'validation_aliases.json', aliases)
    if args.phase in ('all', 'validation'):
        tasks = [task(f'{name}_s{seed}', name, 'validation', replace(configs[name], seed=seed), seed == VALIDATION_SEEDS[0])
                 for name in dict.fromkeys(aliases.values()) for seed in VALIDATION_SEEDS]
        table = execute(tasks, args.workers)
        table.to_csv(OUT/'validation.csv', index=False)
        summarize_validation(table, aliases).to_csv(OUT/'validation_summary.csv', index=False)
    if args.phase in ('all', 'stress'):
        name = choice['selected_configuration'] or 'd60_t0h'
        tasks = [task(f'{name}_speed{speed}', name, 'speed_stress',
                      replace(configs[name], seed=VALIDATION_SEEDS[0], speedup=speed)) for speed in (.8, 1.25)]
        execute(tasks, args.workers).to_csv(OUT/'speed_stress.csv', index=False)
    print('Requested D028 phase complete.', flush=True)


if __name__ == '__main__':
    main()
