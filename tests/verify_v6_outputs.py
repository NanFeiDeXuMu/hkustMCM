"""Independent D033 receipt, selection, Decimal harm and donor checks.

No scheduler, selection function or evaluator imports. Full-case capacity audits
are bound to their input/output hashes; metrics-only cases remain so labelled.
"""
import argparse
from collections import defaultdict
from bisect import bisect_right
import csv
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def rows(path):
    with path.open(encoding='utf-8', newline='') as stream:
        return list(csv.DictReader(stream))


def quantile(values):
    values = sorted(values)
    index = Decimal(len(values)-1)*Decimal('.95')
    lower = int(index)
    fraction = index-lower
    return values[lower] if not fraction else values[lower]*(1-fraction)+values[lower+1]*fraction


def harm_metrics(records, baseline):
    delta = [Decimal(r['start_time'])-Decimal(baseline[r['job_name']]['start_time']) for r in records]
    completion = [Decimal(r['end_time'])-Decimal(baseline[r['job_name']]['end_time']) for r in records]
    n = len(records)
    return {'jobs': n, 'harmed_jobs': sum(d > 0 for d in delta),
        'harmed_fraction': Decimal(sum(d > 0 for d in delta))/n if n else Decimal(0),
        'positive_wait_job_hours': sum(max(Decimal(0), d) for d in delta)/3600,
        'positive_wait_gpu_hours': sum(max(Decimal(0), d)*Decimal(r['gpu_request'])*Decimal(r['worker_num'])
                                       for d, r in zip(delta, records))/3600,
        'max_wait_increase_hours': max([Decimal(0), *delta])/3600,
        'net_wait_job_hours_saved': -sum(delta)/3600,
        'completion_harmed_jobs': sum(d > 0 for d in completion),
        'completion_harmed_fraction': Decimal(sum(d > 0 for d in completion))/n if n else Decimal(0),
        'positive_completion_job_hours': sum(max(Decimal(0), d) for d in completion)/3600,
        'max_completion_increase_hours': max([Decimal(0), *completion])/3600}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'outputs/v6_source_protection')
    out = parser.parse_args().output.resolve()
    checks, failures = 0, []

    def check(condition, message):
        nonlocal checks
        checks += 1
        if not condition:
            failures.append(message)

    def close(actual, expected, message):
        check(abs(Decimal(str(actual))-Decimal(expected)) <= Decimal('0.00000001'), message)

    protocol = read(out/'protocol.json')
    decision = read(out/'development_selection.json')
    unit_path = out/'unit_tests.json'
    if unit_path.exists():
        unit = read(unit_path)
        check(unit['passed'] and unit['tests'] == 187, 'recorded unit-test completion')
        for filename, expected in unit['new_test_sha256'].items():
            check(sha(ROOT/filename) == expected, f'new test source unchanged: {filename}')
    check(protocol['development_seeds'] == [20261030, 20261031, 20261032], 'development seeds')
    check(protocol['validation_seeds'] == list(range(20261033, 20261043)), 'validation seeds')
    check(not set(protocol['development_seeds']) & set(protocol['validation_seeds']), 'disjoint seed sets')
    check(protocol['speeds'] == [1., .8], 'speed assumptions')
    check(len(protocol['configurations']) == 6, 'six declared configurations')
    data = ROOT/'2026_MCM_Senior_GPU_Scheduling/2026_MCM_Senior_GPU_Scheduling'
    for name, expected in protocol['source_sha256'].items():
        check(sha(data/name) == expected, f'input hash: {name}')
    for name, expected in protocol['code_sha256'].items():
        check(sha(ROOT/'src'/name) == expected, f'implementation hash: {name}')
    check(sha(ROOT/'tests/verify_schedules.py') == protocol['verifier_sha256'], 'frozen verifier hash')
    history = read(out/'historical_hashes.json')
    check(sha(out/'historical_hashes.json') == protocol['historical_snapshot_sha256'], 'history snapshot hash')
    for name, expected in history.items():
        check(sha(ROOT/name) == expected, f'historical hash: {name}')
    check(read(out/'history_check.json')['passed'], 'runner historical check')
    reg = read(out/'regression.json')
    check(reg['completed'] and reg['passed'] and len(reg['results']) == 16, 'sixteen complete regressions')
    compared_cells = 0
    for r in reg['results']:
        for kind in ('schedule', 'allocations'):
            check(r[kind]['passed'] and r[kind]['mismatched_cells'] == 0, f'exact regression: {r["scenario"]}/{kind}')
            compared_cells += r[kind]['stored_rows']*len(r[kind]['stored_columns'])
    base_path = ROOT/'outputs/question2/q2_star/schedule.csv'
    check(sha(base_path) == protocol['baseline_schedule_sha256'], 'baseline hash')
    baseline = {r['job_name']: r for r in rows(base_path)}
    base_allocations = rows(base_path.parent/'allocations.csv')
    baseline_mean = sum(Decimal(r['wait_time']) for r in baseline.values())/len(baseline)/3600
    nodes = rows(data/'node_info_df.csv')
    check(all(Decimal(r['gpu_capacity_num']) == 1 for r in nodes if r['gpu_model'] == 'A10'), 'one GPU per source node')
    expected_keys, development, validation = set(), [], []
    full_details, latest_completion = [], datetime.fromisoformat(protocol['registered_utc'])
    lock_time = datetime.fromisoformat(decision['locked_utc'])
    for phase, seeds, configs in (
        ('development', protocol['development_seeds'], protocol['configurations']),
        ('validation', protocol['validation_seeds'], ['current']+([decision['selected']] if decision['selected'] else []))):
        summary = rows(out/f'{phase}.csv')
        summary_by_key = {r['scenario']: r for r in summary}
        check(len(summary) == len(summary_by_key) == len(seeds)*len(configs)*2, f'{phase} unique expected case count')
        for config in configs:
            for seed in seeds:
                for speed in protocol['speeds']:
                    name = f'{phase}_{config}_s{seed}_v{speed:g}'
                    expected_keys.add(name)
                    path = out/'cases'/name
                    r, receipt, request, manifest = (read(path/f) for f in
                        ('comparison_row.json', 'complete.json', 'request.json', 'scenario.json'))
                    check(r['configuration'] == config and r['phase'] == phase and r['seed'] == seed
                          and r['speedup'] == speed, f'case identity: {name}')
                    check(name in summary_by_key, f'summary presence: {name}')
                    if name in summary_by_key:
                        for field, value in r.items():
                            cell = summary_by_key[name][field]
                            if isinstance(value, (int, float)) and not isinstance(value, bool):
                                close(cell, Decimal(str(value)), f'summary metric: {name}/{field}')
                            else:
                                check(cell == str(value), f'summary field: {name}/{field}')
                    fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(',', ':'),
                        ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()
                    check(fingerprint == receipt['request_sha256'], f'request receipt: {name}')
                    check(request['protocol_sha256'] == sha(out/'protocol.json'), f'protocol binding: {name}')
                    actual_files = {p.relative_to(path).as_posix() for p in path.rglob('*')
                                    if p.is_file() and p.name != 'complete.json'}
                    check(actual_files == set(receipt['artifacts']), f'complete artifact set: {name}')
                    for filename, expected in receipt['artifacts'].items():
                        check(sha(path/filename) == expected, f'artifact hash: {name}/{filename}')
                    check(manifest['code_sha256'] == protocol['code_sha256'], f'code binding: {name}')
                    check(manifest['source_sha256'] == protocol['source_sha256'], f'input binding: {name}')
                    check(manifest['source_protection'] == protocol['configurations'][config]['protection'], f'protection: {name}')
                    route = manifest['routing']
                    check(route['seed'] == seed and route['speedup'] == speed and route['flexible_fraction'] == .5
                          and route['donor_cap_fraction'] == protocol['configurations'][config]['cap'], f'route: {name}')
                    completed = datetime.fromisoformat(receipt['completed_utc'])
                    latest_completion = max(latest_completion, completed)
                    check(completed <= lock_time if phase == 'development' else completed > lock_time, f'selection timing: {name}')
                    donor_pass = r['max_donor_mean_wait_delta_hours'] <= .5 and r['max_donor_p95_delta_hours'] <= 2
                    zero = r['a10_harmed_jobs'] == 0 and r['a10_positive_wait_job_hours'] == 0
                    joint = donor_pass and zero and r['a10_mean_wait_gain_hours'] > 0 and Decimal(str(r['wait_hours_mean'])) <= baseline_mean
                    check(r['donor_strict_pass'] == donor_pass and r['source_zero_wait_harm'] == zero
                          and r['joint_pass'] == joint, f'pass classification: {name}')
                    if config != 'current':
                        check(zero, f'protected source has zero wait harm: {name}')
                    (development if phase == 'development' else validation).append(r)
                    full = phase == 'validation' and seed == protocol['validation_seeds'][0]
                    check(request['full'] == full, f'full request scope: {name}')
                    if not full:
                        check(r['schedule_storage'] == 'metrics_only' and not (path/'schedule.csv').exists(), f'metrics only: {name}')
                        continue
                    schedule = rows(path/'schedule.csv')
                    check(len(schedule) == len(baseline) == len({s['job_name'] for s in schedule})
                          and {s['job_name'] for s in schedule} == set(baseline), f'complete job set: {name}')
                    close(r['wait_hours_mean'], sum(Decimal(s['start_time'])-Decimal(s['submit_time']) for s in schedule)/len(schedule)/3600,
                          f'full mean: {name}')
                    source = [s for s in schedule if s['requested_model'] == 'A10']
                    metrics = harm_metrics(source, baseline)
                    for field, value in metrics.items():
                        close(r[f'a10_{field}'], value, f'Decimal source harm: {name}/{field}')
                    if config != 'current':
                        for s in source:
                            check(Decimal(s['start_time']) <= Decimal(baseline[s['job_name']]['start_time']), f'individual source start: {name}/{s["job_name"]}')
                    groups = {('all', 'A10'): source}
                    for label, low, high in [('le1h', 0, 3600), ('1h_1d', 3600, 86400), ('1d_7d', 86400, 604800), ('gt7d', 604800, Decimal('Infinity'))]:
                        groups['original_duration', label] = [s for s in source if low < Decimal(s['original_duration']) <= high]
                    for migrated in (False, True):
                        groups['realized_migration', 'migrated' if migrated else 'stayed'] = [s for s in source if (s['migrated'].lower() == 'true') == migrated]
                    for group in rows(path/'a10_harm_groups.csv'):
                        for field, value in harm_metrics(groups[group['grouping'], group['group']], baseline).items():
                            close(group[field], value, f'group harm: {name}/{group["group"]}/{field}')
                    means, p95s = [], []
                    for donor in ('A100-SXM4-80GB', 'H800'):
                        old = [Decimal(s['wait_time']) for s in baseline.values() if s['requested_model'] == donor]
                        new = [Decimal(s['start_time'])-Decimal(s['submit_time']) for s in schedule if s['requested_model'] == donor]
                        means.append((sum(new)-sum(old))/len(old)/3600)
                        p95s.append((quantile(new)-quantile(old))/3600)
                    close(r['max_donor_mean_wait_delta_hours'], max(means), f'Decimal donor mean: {name}')
                    close(r['max_donor_p95_delta_hours'], max(p95s), f'Decimal donor P95: {name}')
                    audit = read(path/'independent_validation.json')
                    check(audit['passed'] and audit['failure_count'] == 0, f'full capacity audit: {name}')
                    check(audit['verifier_sha256'] == protocol['verifier_sha256'], f'audit implementation: {name}')
                    audited_files = {filename.replace('\\', '/').rsplit('/', 1)[-1]: value
                                     for filename, value in audit['file_sha256'].items()}
                    for filename in ('schedule.csv', 'allocations.csv', 'scenario.json'):
                        check(audited_files[filename] == sha(path/filename), f'audit CSV binding: {name}/{filename}')
                    for filename, expected in protocol['source_sha256'].items():
                        check(audited_files[filename] == expected, f'audit input binding: {name}/{filename}')
                    loan_checks = 0
                    if config != 'current':
                        actual = {s['job_name']: s for s in schedule}
                        reclaimed = defaultdict(list)
                        for a in base_allocations:
                            b, s = baseline[a['job_name']], actual[a['job_name']]
                            if b['requested_model'] != 'A10':
                                continue
                            if s['migrated'].lower() == 'true' or Decimal(s['start_time']) < Decimal(b['start_time']):
                                reclaimed[a['node_name']].append((Decimal(b['start_time']), Decimal(b['end_time'])))
                        reclaimed = {node: sorted(intervals) for node, intervals in reclaimed.items()}
                        starts = {node: [start for start, _ in intervals] for node, intervals in reclaimed.items()}
                        loan_jobs = {s['job_name'] for s in source if s['backfilled'].lower() == 'true'}
                        check(len(loan_jobs) == r['source_loan_jobs'], f'loan count: {name}')
                        for a in rows(path/'allocations.csv'):
                            if a['job_name'] not in loan_jobs:
                                continue
                            s = actual[a['job_name']]
                            start, end = Decimal(s['start_time']), Decimal(s['end_time'])
                            node = a['node_name']
                            index = bisect_right(starts.get(node, []), start)-1
                            check(index >= 0 and end <= reclaimed[node][index][1],
                                  f'loan inside reclaimed baseline interval: {name}/{s["job_name"]}/{node}')
                            loan_checks += 1
                    full_details.append({'case': name, 'decimal_comparisons': audit['comparisons'],
                        'admission_checks': audit['donor_runtime_admission_checks'], 'a10_harmed_jobs': metrics['harmed_jobs'],
                        'independent_loan_interval_checks': loan_checks})
    check({p.name for p in (out/'cases').iterdir() if p.is_dir()} == expected_keys, 'exact registered cases')
    scores = []
    for config in protocol['configurations']:
        group = [r for r in development if r['configuration'] == config]
        scores.append((sum(Decimal(str(r['wait_hours_mean'])) for r in group)/len(group), config,
                       all(r['joint_pass'] for r in group)))
    eligible = sorted((score, name) for score, name, passed in scores if passed)
    check(decision['selected'] == (eligible[0][1] if eligible else None), 'independent development choice')
    check(decision['validation_used_for_selection'] is False, 'validation excluded from choice')
    for score, name, eligible in scores:
        saved = next(r for r in decision['scores'] if r['configuration'] == name)
        close(saved['mean_wait_hours'], score, f'development score: {name}')
        check(saved['eligible'] == eligible, f'development eligibility: {name}')
    check(latest_completion <= datetime.fromisoformat(protocol['deadline_utc']), 'all runs completed within timebox')
    for s in rows(out/'validation_summary.csv'):
        group = [r for r in validation if r['configuration'] == s['configuration'] and r['speedup'] == float(s['speedup'])]
        expected = {'seeds': len(group), 'donor_passed': sum(r['donor_strict_pass'] for r in group),
            'joint_passed': sum(r['joint_pass'] for r in group)}
        for target, field in [('mean_wait_h', 'wait_hours_mean'), ('a10_harmed_fraction', 'a10_harmed_fraction'),
            ('a10_positive_wait_job_hours', 'a10_positive_wait_job_hours'),
            ('a10_completion_harmed_fraction', 'a10_completion_harmed_fraction'),
            ('mean_last_completion_days', 'last_completion_days')]:
            expected[target] = sum(Decimal(str(r[field])) for r in group)/len(group)
        for target, field in [('a10_worst_wait_increase_h', 'a10_max_wait_increase_hours'),
            ('donor_worst_mean_h', 'max_donor_mean_wait_delta_hours'), ('donor_worst_p95_h', 'max_donor_p95_delta_hours')]:
            expected[target] = max(Decimal(str(r[field])) for r in group)
        for field, value in expected.items():
            close(s[field], value, f'validation summary: {s["configuration"]}/{s["speedup"]}/{field}')
    report = {'passed': not failures, 'checks': checks, 'failure_count': len(failures), 'failures': failures,
        'historical_files': len(history), 'exact_regression_cells': compared_cells,
        'development_cases': len(development), 'validation_cases': len(validation), 'selected': decision['selected'],
        'full_schedules': len(full_details), 'metrics_only': len(expected_keys)-len(full_details),
        'independent_decimal_comparisons': sum(r['decimal_comparisons'] for r in full_details),
        'donor_runtime_admission_checks': sum(r['admission_checks'] for r in full_details), 'full_details': full_details,
        'completed_utc': latest_completion.isoformat(), 'verifier_sha256': sha(Path(__file__)),
        'protocol_sha256': sha(out/'protocol.json'),
        'limitations': 'Same trace, assumed durations and compatibility. Zero source waiting harm is not zero completion harm. Metrics-only cases have no independent node audit.'}
    (out/'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('passed', 'checks', 'failure_count', 'selected', 'independent_decimal_comparisons')}))
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
