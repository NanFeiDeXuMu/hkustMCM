"""D033 fixed source-protection development, locked selection and new-seed validation."""
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import argparse
import json
import platform
import sys

import numpy as np
import pandas as pd

from analyze_routing_frontier import analyze_routing_frontier
from analyze_source_protection import analyze_source_harm
from evaluate_scheduling import evaluate
from experiment_store import ExperimentStore, canonical, digest, read_json, write_json
from reproduce_case import audit
from routing_admission import RuntimeAdmission
from scheduler import RoutingConfig
from simulation_contract import SimulationSpec
from source_guard_engine import run_simulation
from source_protection import SourceProtection

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT/'2026_MCM_Senior_GPU_Scheduling/2026_MCM_Senior_GPU_Scheduling'
BASE = ROOT/'outputs/question2/q2_star/schedule.csv'
A100, H800 = 'A100-SXM4-80GB', 'H800'
DEV_SEEDS = (20261030, 20261031, 20261032)
VAL_SEEDS = tuple(range(20261033, 20261043))
SPEEDS = (1., .8)
CODE = ('source_protection.py', 'source_guard_engine.py', 'analyze_source_protection.py',
        'run_v6_source_protection.py', 'experiment_store.py', 'reproduce_case.py', 'simulation_contract.py',
        'scheduler.py', 'experimental_scheduler.py', 'routing_admission.py',
        'evaluate_scheduling.py', 'analyze_routing_frontier.py')


def configurations():
    return {'current': {'protection': None, 'cap': .25},
            'shadow': {'protection': {'loan_scan': 0}, 'cap': .25},
            'loans128': {'protection': {'loan_scan': 128}, 'cap': .25},
            'loans512': {'protection': {'loan_scan': 512}, 'cap': .25},
            'loans128_cap15': {'protection': {'loan_scan': 128}, 'cap': .15},
            'loans128_cap10': {'protection': {'loan_scan': 128}, 'cap': .10}}


def declare(out):
    out.mkdir(parents=True, exist_ok=True)
    data_hash = {name: digest(DATA/name) for name in ('job_info_clean.csv', 'node_info_df.csv')}
    declaration = {'rule': 'D033', 'configurations': configurations(),
        'development_seeds': DEV_SEEDS, 'validation_seeds': VAL_SEEDS, 'speeds': SPEEDS,
        'source_sha256': data_hash, 'baseline_schedule_sha256': digest(BASE),
        'code_sha256': {name: digest(ROOT/'src'/name) for name in CODE},
        'verifier_sha256': digest(ROOT/'tests/verify_schedules.py'),
        'versions': {'python': platform.python_version(), 'numpy': np.__version__, 'pandas': pd.__version__},
        'thresholds': {'donor_mean_h': .5, 'donor_p95_h': 2., 'positive_a10_wait_loss_h': 0.},
        'scope': 'Same trace eligibility uncertainty; zero-wait-loss target does not imply zero completion harm'}
    declaration = json.loads(canonical(declaration))
    path = out/'protocol.json'
    if path.exists():
        previous = read_json(path)
        if any(previous.get(key) != value for key, value in declaration.items()):
            raise ValueError('Registered protocol differs')
        return previous
    history = {p.relative_to(ROOT).as_posix(): digest(p) for p in (ROOT/'outputs').rglob('*')
               if p.is_file() and not p.is_relative_to(out)}
    history.update({p.relative_to(ROOT).as_posix(): digest(p) for p in (ROOT/'src').glob('*.py')
                    if p.name not in CODE[:4]})
    history.update({p.relative_to(ROOT).as_posix(): digest(p) for p in ROOT.glob('TASK_*.md')})
    write_json(out/'historical_hashes.json', history)
    now = datetime.now(timezone.utc)
    declaration.update(registered_utc=now.isoformat(), deadline_utc=(now+timedelta(hours=2)).isoformat(),
                       historical_snapshot_sha256=digest(out/'historical_hashes.json'))
    write_json(path, declaration)
    return declaration


def read_inputs():
    return (pd.read_csv(DATA/'job_info_clean.csv', dtype={'job_name': str}, float_precision='round_trip'),
            pd.read_csv(DATA/'node_info_df.csv', dtype={'node_name': str}, float_precision='round_trip'),
            pd.read_csv(BASE, dtype={'job_name': str}, float_precision='round_trip'))


def check_budget(protocol):
    if datetime.now(timezone.utc) >= datetime.fromisoformat(protocol['deadline_utc']):
        raise TimeoutError('D033 budget expired before next simulation')


def run_task(task):
    output, protocol, name, phase, seed, speed, full = task
    out = Path(output)
    config = protocol['configurations'][name]
    route = RoutingConfig(mode='pull', donors=(A100, H800), seed=seed, speedup=speed,
        donor_cap_fraction=config['cap'], max_routed_duration_seconds=75*86400)
    admission = RuntimeAdmission(((A100, 75*86400.), (H800, 30*86400.)))
    protection = SourceProtection(**config['protection']) if config['protection'] is not None else None
    spec = SimulationSpec(routing=route, admission=admission)
    key = f'{phase}_{name}_s{seed}_v{speed:g}'
    request = {'protocol_sha256': digest(out/'protocol.json'), 'name': name, 'phase': phase,
               'spec': spec.to_dict(), 'source_protection': config['protection'], 'full': full}

    def produce(stage):
        check_budget(protocol)
        print(f'Start {key}', flush=True)
        jobs, nodes, base = read_inputs()
        schedule, allocation, diagnostics = run_simulation(jobs, nodes, policy='log_aging',
            log_aging_tau_seconds=604800, routing=route, admission=admission, source_protection=protection)
        metric = evaluate(schedule, nodes, stage)
        frontier = analyze_routing_frontier(base, schedule, nodes, route.donors, stage)
        harm = analyze_source_harm(base, schedule, stage)
        row = {'scenario': key, 'configuration': name, 'phase': phase, 'seed': seed, 'speedup': speed,
            'schedule_storage': 'full' if full else 'metrics_only', 'source_loan_jobs': diagnostics['source_loan_jobs'],
            **{k: v for k, v in (metric | frontier | harm).items() if not isinstance(v, (dict, list))}}
        row['donor_strict_pass'] = bool(row['max_donor_mean_wait_delta_hours'] <= .5
                                        and row['max_donor_p95_delta_hours'] <= 2.)
        row['source_zero_wait_harm'] = bool(row['a10_harmed_jobs'] == 0 and row['a10_positive_wait_job_hours'] == 0)
        row['joint_pass'] = bool(row['donor_strict_pass'] and row['source_zero_wait_harm']
            and row['a10_mean_wait_gain_hours'] > 0 and row['wait_hours_mean'] <= float(base.wait_time.mean()/3600))
        if protection is not None and not row['source_zero_wait_harm']:
            raise AssertionError('Online source protection violated its zero-wait-harm invariant')
        write_json(stage/'comparison_row.json', row)
        write_json(stage/'scenario.json', {'name': key, 'experiment_version': 'v6_source_protection',
            **spec.to_dict(), 'source_protection': config['protection'],
            'source_sha256': protocol['source_sha256'], 'code_sha256': protocol['code_sha256'],
            'baseline_schedule_sha256': protocol['baseline_schedule_sha256'],
            'schedule_storage': row['schedule_storage'], 'diagnostics': diagnostics})
        if full:
            schedule.to_csv(stage/'schedule.csv', index=False)
            allocation.to_csv(stage/'allocations.csv', index=False)
            audit(stage, DATA, spec)
        print(json.dumps({k: row[k] for k in ('scenario', 'wait_hours_mean', 'a10_harmed_fraction',
             'a10_positive_wait_job_hours', 'max_donor_mean_wait_delta_hours', 'max_donor_p95_delta_hours', 'joint_pass')}), flush=True)

    result = ExperimentStore(out).run(key, request, produce)
    return read_json(result.path/'comparison_row.json')


def choose(table):
    if set(table.phase) != {'development'}:
        raise ValueError('Selection accepts development only')
    scores = []
    expected = sorted((seed, speed) for seed in DEV_SEEDS for speed in SPEEDS)
    for name, group in table.groupby('configuration', sort=True):
        if sorted(zip(group.seed, group.speedup)) != expected:
            raise ValueError('Missing or duplicate development pairs')
        scores.append({'configuration': name, 'eligible': bool(group.joint_pass.all()),
                       'mean_wait_hours': float(group.wait_hours_mean.mean())})
    eligible = sorted((r for r in scores if r['eligible']), key=lambda r: (r['mean_wait_hours'], r['configuration']))
    return {'selected': eligible[0]['configuration'] if eligible else None, 'scores': scores,
            'rule': 'D033', 'validation_used_for_selection': False}


def regression(out, protocol):
    # The immutable verifier supplies exact frame comparison only; results are new.
    sys.path.insert(0, str(ROOT/'tests'))
    from verify_legacy_regression import LEGACY_SCENARIOS, compare_frame
    cases = (*LEGACY_SCENARIOS, 'question2/q2_star',
        'v5_architecture/cases/runtime75_h30_s20261030_v1',
        'v5_architecture/cases/runtime75_h30_s20261030_v0.8')
    jobs, nodes, _ = read_inputs()
    results = []
    for name in cases:
        check_budget(protocol)
        path = ROOT/'outputs'/name
        spec = SimulationSpec.from_manifest(read_json(path/'scenario.json'))
        s, a, _ = run_simulation(jobs, nodes, policy=spec.policy, routing=spec.routing, admission=spec.admission,
            log_aging_tau_seconds=spec.log_aging_tau_seconds, aging_alpha=spec.aging_alpha,
            max_backfill_scan=spec.max_backfill_scan)
        old_s = pd.read_csv(path/'schedule.csv', dtype={'job_name': str}, float_precision='round_trip')
        old_a = pd.read_csv(path/'allocations.csv', dtype={'job_name': str, 'node_name': str}, float_precision='round_trip')
        checks = {'schedule': compare_frame(old_s, s, ['job_name']),
                  'allocations': compare_frame(old_a, a, ['job_name', 'node_name', 'worker_count'])}
        passed = all(value['passed'] for value in checks.values())
        results.append({'scenario': name, 'passed': passed, **checks})
        write_json(out/'regression.json', {'passed': all(r['passed'] for r in results),
            'completed': len(results) == len(cases), 'results': results})
        print(json.dumps({'regression': name, 'passed': passed}), flush=True)
        if not passed:
            raise AssertionError('Disabled control differs from preserved reference')


def execute(tasks, workers):
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return pd.DataFrame(list(pool.map(run_task, tasks))).sort_values(['configuration', 'seed', 'speedup'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'outputs/v6_source_protection')
    parser.add_argument('--phase', choices=('all', 'regression', 'development', 'validation'), default='all')
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error('workers must be positive')
    out = args.output.resolve()
    protocol = declare(out)
    if args.phase in ('all', 'regression'):
        regression(out, protocol)
    if args.phase in ('all', 'development', 'validation'):
        evidence = read_json(out/'regression.json')
        if not evidence['passed'] or not evidence['completed']:
            raise ValueError('Complete regression required')
    if args.phase in ('all', 'development'):
        tasks = [(str(out), protocol, name, 'development', seed, speed, False)
                 for name in configurations() for seed in DEV_SEEDS for speed in SPEEDS]
        table = execute(tasks, args.workers)
        table.to_csv(out/'development.csv', index=False)
        decision = choose(table)
        choice_path = out/'development_selection.json'
        if choice_path.exists():
            previous = read_json(choice_path)
            if any(previous[k] != v for k, v in decision.items()):
                raise ValueError('Locked choice differs')
        else:
            decision['locked_utc'] = datetime.now(timezone.utc).isoformat()
            write_json(choice_path, decision)
        print(json.dumps(decision), flush=True)
    if args.phase in ('all', 'validation'):
        decision = read_json(out/'development_selection.json')
        names = ['current'] + ([decision['selected']] if decision['selected'] else [])
        tasks = [(str(out), protocol, name, 'validation', seed, speed, seed == VAL_SEEDS[0])
                 for name in names for seed in VAL_SEEDS for speed in SPEEDS]
        table = execute(tasks, args.workers)
        table.to_csv(out/'validation.csv', index=False)
        summary = table.groupby(['configuration', 'speedup']).agg(
            seeds=('seed', 'count'), donor_passed=('donor_strict_pass', 'sum'), joint_passed=('joint_pass', 'sum'),
            mean_wait_h=('wait_hours_mean', 'mean'), a10_harmed_fraction=('a10_harmed_fraction', 'mean'),
            a10_positive_wait_job_hours=('a10_positive_wait_job_hours', 'mean'),
            a10_worst_wait_increase_h=('a10_max_wait_increase_hours', 'max'),
            a10_completion_harmed_fraction=('a10_completion_harmed_fraction', 'mean'),
            donor_worst_mean_h=('max_donor_mean_wait_delta_hours', 'max'),
            donor_worst_p95_h=('max_donor_p95_delta_hours', 'max'),
            mean_last_completion_days=('last_completion_days', 'mean'))
        summary.to_csv(out/'validation_summary.csv')
    history = read_json(out/'historical_hashes.json')
    failures = [name for name, expected in history.items() if digest(ROOT/name) != expected]
    write_json(out/'history_check.json', {'passed': not failures, 'files': len(history), 'failures': failures})
    if failures:
        raise AssertionError('Historical evidence changed')


if __name__ == '__main__':
    main()
