"""Independent v4 aggregation, provenance and full-schedule verification.

Uses CSV/Decimal and historical independent auditors. Does not import any
scheduler, experiment runner, or production metric implementation.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal as D
import json
import math
from pathlib import Path

from verify_schedules import Checks, DEFAULT_DATA, sha256, verify_scenario
from verify_all_schedules import preserved_report
from verify_v3_outputs import read, rows, close, boolean, paired_full_check

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/v4_validation'


def gate(row, compensation=False):
    positive = D(str(row['a10_mean_wait_gain_hours'])) > 0
    if compensation:
        exchange = boolean(row['exchange_ratio_infinite']) or D(str(row['exchange_ratio'])) >= 100
        return positive and exchange and D(str(row['max_donor_mean_wait_delta_hours'])) <= 1 and D(str(row['max_donor_native_wait_increased_fraction'])) <= D('.2')
    return positive and D(str(row['max_donor_mean_wait_delta_hours'])) <= D('.5') and D(str(row['max_donor_p95_delta_hours'])) <= 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit-schedules', action='store_true')
    args = parser.parse_args()
    checks = Checks()
    protocol = read(OUT/'protocol.json')
    history = read(OUT/'historical_hashes.json')
    for name, expected in history.items():
        checks.check((ROOT/name).is_file() and sha256(ROOT/name) == expected, f'Historical file changed: {name}')
    development = rows(OUT/'development.csv')
    validation = rows(OUT/'validation.csv')
    stress = rows(OUT/'speed_stress.csv')
    grouped = defaultdict(list)
    for row in development:
        grouped[row['configuration']].append(row)
    checks.check(set(grouped) == set(protocol['configurations']), 'Development grid mismatch')
    expected_choice = []
    for name, group in grouped.items():
        checks.check(sorted(int(r['seed']) for r in group) == protocol['development_seeds'], f'Development seed mismatch: {name}')
        if all(gate(r) for r in group):
            gain = sum(D(r['a10_mean_wait_gain_hours']) for r in group)/len(group)
            worst = max(D(r['max_donor_mean_wait_delta_hours']) for r in group)
            expected_choice.append((-gain, worst, name))
    chosen = min(expected_choice)[2] if expected_choice else None
    selection = read(OUT/'development_selection.json')
    checks.check(selection['selected_configuration'] == chosen, 'Independent development selection disagrees')
    checks.check(selection['validation_used_for_selection'] is False, 'Validation leaked into development selection')
    aliases = read(OUT/'validation_aliases.json')
    summaries = {r['label']: r for r in rows(OUT/'validation_summary.csv')}
    for label, name in aliases.items():
        group = [r for r in validation if r['configuration'] == name]
        checks.check(sorted(int(r['seed']) for r in group) == protocol['validation_seeds'], f'Validation seed mismatch: {name}')
        count = sum(gate(r, label == 'existing_compensation') for r in group)
        close(checks, summaries[label]['passed'], count, f'{label} pass count')
        close(checks, summaries[label]['total'], len(group), f'{label} total count')
        n = len(group)
        p, z = count/n, 1.959963984540054
        center = (p+z*z/(2*n))/(1+z*z/n)
        spread = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
        close(checks, summaries[label]['wilson95_low'], max(0., center-spread), f'{label} Wilson lower')
        close(checks, summaries[label]['wilson95_high'], min(1., center+spread), f'{label} Wilson upper')
        for field in ('wait_hours_mean', 'wait_hours_max', 'a10_mean_wait_gain_hours', 'max_donor_mean_wait_delta_hours',
                      'max_donor_p95_delta_hours', 'a10_wait_increased_fraction', 'max_donor_native_wait_increased_fraction'):
            values = [D(r[field]) for r in group]
            for stat, value in [('mean', sum(values)/n), ('min', min(values)), ('max', max(values))]:
                close(checks, summaries[label][f'{field}_{stat}'], value, f'{label}/{field}/{stat}')
    checks.check(len(validation) == len(set(aliases.values()))*10, 'Validation count mismatch')
    checks.check(len(stress) == 2 and {D(r['speedup']) for r in stress} == {D('.8'), D('1.25')}, 'Speed stress grid mismatch')
    replay_map = {'d30_t0h': 'v3_ah_d30', 'd30_t1h': 'v3_ah_d30_t1h',
                  'd60_t0h': 'v3_ah_d60', 'd60_t1h': 'v3_ah_d60_t1h',
                  'unlimited_cap10': 'v3_ah_cap10', 'unlimited_cap15': 'v3_ah_cap15',
                  'unlimited_cap25': 'v2_reference/v2_ah'}
    for row in development:
        if int(row['seed']) == 20261017 and row['configuration'] in replay_map:
            old = read(ROOT/'outputs/question3/v3'/replay_map[row['configuration']]/'metrics.json')
            for field in ('wait_hours_mean', 'wait_hours_max', 'wait_hours_p99', 'last_completion_days', 'completed_at_90d', 'migrated_jobs'):
                close(checks, row[field], old[field], f"{row['scenario']}: historical scalar replay/{field}")
    audits, skipped = [], 0
    for row in development+validation+stress:
        path = ROOT/row['output_path']
        meta = read(path/'scenario.json')
        checks.check(meta['phase'] == row['phase'], f'{path.name} phase')
        for filename, expected in meta['code_sha256'].items():
            checks.check(sha256(ROOT/'src'/filename) == expected, f'{path.name}/{filename}: code changed')
        for filename, expected in meta['source_sha256'].items():
            checks.check(sha256(DEFAULT_DATA/filename) == expected, f'{path.name}/{filename}: input changed')
        checks.check(sha256(ROOT/'outputs/question2/q2_star/schedule.csv') == meta['baseline_schedule_sha256'], f'{path.name} baseline changed')
        expected_config = dict(protocol['configurations'][row['configuration']])
        expected_config['seed'] = int(row['seed'])
        expected_config['speedup'] = float(row['speedup'])
        checks.check(meta['routing'] == expected_config, f'{path.name}: routing differs from preregistration')
        metric, ledger = read(path/'metrics.json'), read(path/'frontier_metrics.json')
        for source in (metric, ledger):
            for key, value in source.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    close(checks, row[key], value, f'{path.name}/{key}')
        checks.check(boolean(row['strict_feasible']) == (D(row['max_donor_mean_wait_delta_hours']) <= D('.5') and D(row['max_donor_p95_delta_hours']) <= 2), f'{path.name}: strict classification')
        checks.check(boolean(row['compensation_feasible']) == gate({**row, 'a10_mean_wait_gain_hours': 1}, True), f'{path.name}: compensation classification')
        if meta['schedule_storage'] == 'full':
            if (path/'independent_validation.json').exists():
                audit = preserved_report(path, DEFAULT_DATA)
            elif args.audit_schedules:
                audit = verify_scenario(path, DEFAULT_DATA)
                (path/'independent_validation.json').write_text(json.dumps(audit, indent=2)+'\n', encoding='utf-8')
            else:
                raise ValueError(f'Full schedule needs --audit-schedules: {path}')
            checks.check(audit['passed'], f'{path.name}: independent schedule audit')
            paired_full_check(checks, path, audit)
            audits.append({'path': path.relative_to(ROOT).as_posix(), 'comparisons': audit['comparisons'],
                           'passed': audit['passed'], 'audit_sha256': sha256(path/'independent_validation.json')})
        else:
            skipped += 1
    for label in ('existing_strict', 'existing_compensation'):
        path = OUT/'harm_diagnostics'/label
        summary = read(path/'summary.json')
        checks.check(summary['code_sha256'] == sha256(ROOT/'src/analyze_v4_harm.py'), f'{label}: diagnostic code hash')
        for filename, expected in summary['input_sha256'].items():
            checks.check(sha256(ROOT/filename) == expected, f'{label}: diagnostic input hash/{filename}')
        details = rows(path/'harmed_snapshots.csv')
        checks.check(len(details) == summary['harmed_jobs'], f'{label}: harmed snapshot population')
        counts = defaultdict(int)
        for row in details:
            counts[row['category']] += 1
            own_fit = int(row['job_available_worker_slots']) >= int(row['job_workers'])
            head_fit = int(row['head_available_worker_slots']) >= int(row['head_workers'])
            expected = ('not_placeable_on_realized_timeline' if not own_fit else
                        'feasible_behind_infeasible_head' if row['head_job_name'] != row['job_name'] and not head_fit else
                        'other_requires_investigation')
            checks.check(row['category'] == expected, f'{label}: inconsistent snapshot category')
        checks.check(dict(counts) == summary['category_counts'], f'{label}: category total')
        # Independently rebuild exact resource snapshots for the six published examples.
        examples = rows(path/'examples.csv')
        source_folder = ROOT/('outputs/question3/v3/v3_ah_d60' if label == 'existing_strict' else 'outputs/question3/v3/v2_reference/v2_ah')
        schedules = {r['job_name']: r for r in rows(source_folder/'schedule.csv')}
        allocations = rows(source_folder/'allocations.csv')
        node_rows = [r for r in rows(DEFAULT_DATA/'node_info_df.csv') if r['gpu_model'] == 'A10']
        for example in examples:
            t = D(example['baseline_start_seconds'])
            free = {r['node_name']: [int(r['gpu_capacity_num']), D(r['cpu_num'])] for r in node_rows}
            for allocation in allocations:
                node = allocation['node_name']
                if node not in free:
                    continue
                job = schedules[allocation['job_name']]
                if D(job['start_time']) <= t < D(job['end_time']):
                    workers = int(allocation['worker_count'])
                    free[node][0] -= int(job['gpu_request'])*workers
                    free[node][1] -= D(job['cpu_request'])*workers
            waiting = [r for r in schedules.values() if r['requested_model'] == 'A10' and D(r['submit_time']) <= t < D(r['start_time'])]
            head = min(waiting, key=lambda r: (math.log(float(r['original_duration']))+float(r['submit_time'])/604800, float(r['submit_time']), r['job_name']))
            checks.check(head['job_name'] == example['head_job_name'], f'{label}: independent example head')
            for who, field in [(example['job_name'], 'job_available_worker_slots'), (head['job_name'], 'head_available_worker_slots')]:
                job = schedules[who]
                g, c = int(job['gpu_request']), D(job['cpu_request'])
                count = sum(min(gpu//g, int(cpu//c)) if c else gpu//g for gpu, cpu in free.values())
                close(checks, example[field], count, f'{label}: Decimal example slots')
            close(checks, example['free_gpus'], sum(g for g, _ in free.values()), f'{label}: Decimal example free GPUs')
    result = {'passed': checks.failure_count == 0, 'comparisons': checks.comparisons,
              'failure_count': checks.failure_count, 'failures': checks.failures,
              'historical_files_unchanged': len(history), 'development_runs': len(development),
              'validation_runs': len(validation), 'speed_stress_runs': len(stress), 'selected_configuration': chosen,
              'full_schedule_audits': audits, 'full_schedule_comparisons': sum(a['comparisons'] for a in audits),
              'metrics_only_not_independently_audited': skipped,
              'snapshot_audit_scope': 'All category arithmetic and 6 examples per existing policy with exact Decimal occupancy; not all snapshots independently replayed',
              'verifier_sha256': sha256(Path(__file__))}
    (OUT/'verification.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result), flush=True)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
