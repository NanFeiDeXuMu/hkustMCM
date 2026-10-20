"""D030: fixed paired architecture ablation, explicit context, immutable evidence."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import platform
import sys

import pandas as pd
import numpy as np

from analyze_routing_frontier import analyze_routing_frontier
from evaluate_scheduling import evaluate
from experimental_scheduler import run_simulation
from routing_admission import RuntimeAdmission
from scheduler import RoutingConfig
from run_v2_experiments import digest, flat, read_json, write_json
from run_v3_experiments import CORE_SHA, classify, routing_from_manifest, THRESHOLDS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tests'))
from verify_legacy_regression import LEGACY_SCENARIOS, compare_frame
from verify_schedules import verify_scenario

SEEDS = (20261030, 20261031, 20261032)
SPEEDS = (1., .8)
A100, H800 = 'A100-SXM4-80GB', 'H800'
CODE = ('scheduler.py', 'experimental_scheduler.py', 'routing_admission.py',
        'run_v5_architecture.py', 'evaluate_scheduling.py', 'analyze_routing_frontier.py',
        'run_v2_experiments.py', 'run_v3_experiments.py')


def policies():
    return {'reference_d75': None,
            'runtime75': RuntimeAdmission(((A100, 75*86400.), (H800, 75*86400.))),
            'runtime75_h30': RuntimeAdmission(((A100, 75*86400.), (H800, 30*86400.)))}


@dataclass(frozen=True)
class ExperimentContext:
    root: Path
    data: Path
    output: Path
    baseline: Path
    jobs: pd.DataFrame
    nodes: pd.DataFrame
    baseline_schedule: pd.DataFrame


def context(root, output):
    data = root/'2026_MCM_Senior_GPU_Scheduling/2026_MCM_Senior_GPU_Scheduling'
    baseline = root/'outputs/question2/q2_star/schedule.csv'
    return ExperimentContext(root, data, output, baseline,
        pd.read_csv(data/'job_info_clean.csv', dtype={'job_name': str}, float_precision='round_trip'),
        pd.read_csv(data/'node_info_df.csv', dtype={'node_name': str}, float_precision='round_trip'),
        pd.read_csv(baseline, dtype={'job_name': str}, float_precision='round_trip'))


def plain(value):
    return json.loads(json.dumps(value))


def register(ctx):
    if digest(ctx.root/'src/scheduler.py') != CORE_SHA:
        raise ValueError('Frozen scheduler differs')
    declaration = {'rule': 'D030', 'seeds': list(SEEDS), 'speeds': list(SPEEDS),
        'policies': {name: asdict(policy) if policy else None for name, policy in policies().items()},
        'routing': asdict(RoutingConfig(mode='pull', donors=(A100, H800), max_routed_duration_seconds=75*86400)),
        'thresholds': THRESHOLDS,
        'source_sha256': {name: digest(ctx.data/name) for name in ('job_info_clean.csv', 'node_info_df.csv')},
        'code_sha256': {name: digest(ctx.root/'src'/name) for name in CODE},
        'verifier_sha256': {name: digest(ctx.root/'tests'/name) for name in
                           ('verify_schedules.py', 'verify_legacy_regression.py')},
        'baseline_schedule_sha256': digest(ctx.baseline),
        'versions': {'python': platform.python_version(), 'numpy': np.__version__, 'pandas': pd.__version__},
        'scope': 'Fixed paired ablation; no selection, no adaptive tuning, same trace'}
    declaration = plain(declaration)
    path = ctx.output/'protocol.json'
    if path.exists():
        old = read_json(path)
        if any(old.get(key) != value for key, value in declaration.items()):
            raise ValueError('Immutable protocol differs; use a new evidence directory')
        return old
    now = datetime.now(timezone.utc)
    history = {p.relative_to(ctx.root).as_posix(): digest(p) for p in (ctx.root/'outputs').rglob('*')
               if p.is_file() and not p.is_relative_to(ctx.output) and '__pycache__' not in p.parts}
    write_json(ctx.output/'historical_hashes.json', history)
    declaration.update(registered_utc=now.isoformat(), deadline_utc=(now+timedelta(hours=1)).isoformat(),
                       historical_snapshot_sha256=digest(ctx.output/'historical_hashes.json'))
    write_json(path, declaration)
    return declaration


def check_budget(protocol):
    if datetime.now(timezone.utc) >= datetime.fromisoformat(protocol['deadline_utc']):
        raise TimeoutError('D030 budget expired before starting another simulation')


def regression(ctx, protocol):
    scenarios = (*LEGACY_SCENARIOS, 'question2/q2_star',
                 'v4_validation/validation/d75_t0h_s20261020')
    results = []
    for name in scenarios:
        check_budget(protocol)
        path = ctx.root/'outputs'/name
        manifest = read_json(path/'scenario.json')
        route = routing_from_manifest(manifest['routing']) if manifest.get('routing') else None
        fresh, allocation, _ = run_simulation(ctx.jobs, ctx.nodes, manifest['policy'], routing=route,
            max_backfill_scan=manifest.get('max_backfill_scan', 128),
            aging_alpha=manifest.get('aging_alpha') or 0.,
            log_aging_tau_seconds=manifest.get('log_aging_tau_seconds') or 86400.)
        stored = pd.read_csv(path/'schedule.csv', dtype={'job_name': str}, float_precision='round_trip')
        saved_allocation = pd.read_csv(path/'allocations.csv', dtype={'job_name': str, 'node_name': str},
                                      float_precision='round_trip')
        schedule_check = compare_frame(stored, fresh, ['job_name'])
        allocation_check = compare_frame(saved_allocation, allocation, ['job_name', 'node_name', 'worker_count'])
        result = {'scenario': name, 'schedule': schedule_check, 'allocations': allocation_check,
                  'passed': schedule_check['passed'] and allocation_check['passed']}
        results.append(result)
        write_json(ctx.output/'regression.json', {'passed': all(r['passed'] for r in results),
                   'completed': len(results) == len(scenarios), 'results': results})
        print(json.dumps({'regression': name, 'passed': result['passed']}), flush=True)
        if not result['passed']:
            raise AssertionError(f'Regression differs: {name}')


def run_case(ctx, protocol, name, admission, seed, speed):
    path = ctx.output/'cases'/f'{name}_s{seed}_v{speed:g}'
    full = seed == SEEDS[0]
    if (path/'complete.json').exists():
        receipt = read_json(path/'complete.json')
        if receipt['protocol_sha256'] != digest(ctx.output/'protocol.json'):
            raise ValueError('Cached case protocol differs')
        for filename, expected in receipt['artifacts'].items():
            if digest(path/filename) != expected:
                raise ValueError(f'Cached artifact differs: {path/filename}')
        return read_json(path/'comparison_row.json')
    if path.exists():
        raise ValueError(f'Incomplete evidence retained, refusing overwrite: {path}')
    check_budget(protocol)
    route = RoutingConfig(mode='pull', donors=(A100, H800), seed=seed, speedup=speed,
                          max_routed_duration_seconds=75*86400)
    schedule, allocations, stats = run_simulation(ctx.jobs, ctx.nodes, policy='log_aging',
        log_aging_tau_seconds=604800, routing=route, admission=admission)
    metric = evaluate(schedule, ctx.nodes, path)
    frontier = analyze_routing_frontier(ctx.baseline_schedule, schedule, ctx.nodes, route.donors, path)
    row = {'scenario': path.name, 'configuration': name, 'seed': seed, 'speedup': speed,
           'schedule_storage': 'full' if full else 'metrics_only', **flat(metric), **flat(frontier)}
    row = classify(pd.DataFrame([row])).iloc[0].to_dict()
    row['strict_with_positive_gain'] = bool(row['strict_feasible'] and row['a10_mean_wait_gain_hours'] > 0)
    write_json(path/'comparison_row.json', row)
    manifest = {'name': path.name, 'experiment_version': 'v5_architecture', 'policy': 'log_aging',
        'log_aging_tau_seconds': 604800, 'routing': asdict(route),
        'admission': asdict(admission) if admission else None, 'schedule_storage': row['schedule_storage'],
        'source_sha256': protocol['source_sha256'], 'code_sha256': protocol['code_sha256'],
        'baseline_schedule_sha256': protocol['baseline_schedule_sha256'], 'diagnostics': stats}
    write_json(path/'scenario.json', manifest)
    if full:
        schedule.to_csv(path/'schedule.csv', index=False)
        allocations.to_csv(path/'allocations.csv', index=False)
        audit = verify_scenario(path, ctx.data)
        # Check new policy independently with decimal CSV values, not policy code.
        from decimal import Decimal
        import csv
        limits = dict(admission.limits_seconds) if admission else {}
        count = 0
        with (path/'schedule.csv').open(encoding='utf-8', newline='') as stream:
            for record in csv.DictReader(stream):
                if record['migrated'].lower() == 'true' and admission is not None:
                    count += 1
                    if Decimal(record['run_duration']) > Decimal(str(limits[record['assigned_model']])):
                        raise AssertionError('Independent donor runtime ceiling violated')
        audit['donor_runtime_admission_checks'] = count
        write_json(path/'independent_validation.json', audit)
        if not audit['passed']:
            raise AssertionError(f'Independent audit failed: {path}')
    artifacts = {p.name: digest(p) for p in path.iterdir() if p.is_file()}
    write_json(path/'complete.json', {'protocol_sha256': digest(ctx.output/'protocol.json'), 'artifacts': artifacts})
    print(json.dumps({k: row[k] for k in ('scenario', 'wait_hours_mean', 'max_donor_mean_wait_delta_hours',
                                         'max_donor_p95_delta_hours', 'strict_with_positive_gain')}), flush=True)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'outputs/v5_architecture')
    parser.add_argument('--phase', choices=('all', 'register', 'regression', 'pilot'), default='all')
    args = parser.parse_args()
    ctx = context(ROOT, args.output.resolve())
    protocol = register(ctx)
    if args.phase in ('all', 'regression'):
        regression(ctx, protocol)
    if args.phase in ('all', 'pilot'):
        check = read_json(ctx.output/'regression.json')
        if not check['passed'] or not check['completed']:
            raise ValueError('Complete exact regression required before ablation')
        rows = [run_case(ctx, protocol, name, policy, seed, speed)
                for name, policy in policies().items() for seed in SEEDS for speed in SPEEDS]
        table = pd.DataFrame(rows)
        table.to_csv(ctx.output/'comparison.csv', index=False)
        summary = table.groupby(['configuration', 'speedup']).agg(
            seeds=('seed', 'count'), passed=('strict_with_positive_gain', 'sum'),
            mean_wait_hours=('wait_hours_mean', 'mean'),
            worst_donor_mean_hours=('max_donor_mean_wait_delta_hours', 'max'),
            worst_donor_p95_hours=('max_donor_p95_delta_hours', 'max'),
            mean_a10_harmed_fraction=('a10_wait_increased_fraction', 'mean'))
        summary.to_csv(ctx.output/'summary.csv')
    history = read_json(ctx.output/'historical_hashes.json')
    failures = [name for name, expected in history.items() if digest(ROOT/name) != expected]
    write_json(ctx.output/'history_check.json', {'files': len(history), 'passed': not failures, 'failures': failures})
    if failures:
        raise AssertionError('Historical artifacts changed')


if __name__ == '__main__':
    main()
