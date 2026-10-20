"""Independent D030 evidence/aggregate checks; no scheduler or evaluator imports."""
import argparse
import csv
from decimal import Decimal
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def csv_rows(path):
    with path.open(encoding='utf-8', newline='') as stream:
        return list(csv.DictReader(stream))


def quantile(values, numerator=95, denominator=100):
    values = sorted(values)
    index = Decimal(len(values)-1)*numerator/denominator
    lower = int(index)
    fraction = index-lower
    return values[lower] if not fraction else values[lower]*(1-fraction)+values[lower+1]*fraction


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'outputs/v5_architecture')
    args = parser.parse_args()
    out = args.output
    failures = []
    checks = 0

    def check(condition, message):
        nonlocal checks
        checks += 1
        if not condition:
            failures.append(message)

    def close(observed, expected, message):
        check(abs(Decimal(str(observed))-expected) <= Decimal('0.00000001'), message)

    protocol = read(out/'protocol.json')
    check(protocol['seeds'] == [20261030, 20261031, 20261032], 'seed declaration')
    check(protocol['speeds'] == [1., .8], 'speed declaration')
    check(set(protocol['policies']) == {'reference_d75', 'runtime75', 'runtime75_h30'}, 'policy declaration')
    data = ROOT/'2026_MCM_Senior_GPU_Scheduling/2026_MCM_Senior_GPU_Scheduling'
    for name, expected in protocol['source_sha256'].items():
        check(sha(data/name) == expected, f'source changed: {name}')
    for name, expected in protocol['code_sha256'].items():
        check(sha(ROOT/'src'/name) == expected, f'code changed: {name}')
    history = read(out/'historical_hashes.json')
    check(sha(out/'historical_hashes.json') == protocol['historical_snapshot_sha256'], 'history ledger changed')
    for name, expected in history.items():
        check(sha(ROOT/name) == expected, f'history changed: {name}')
    regression = read(out/'regression.json')
    check(regression['completed'] and regression['passed'], 'complete regression required')
    check(len(regression['results']) == 15, '15 regression cases required')
    for record in regression['results']:
        check(record['schedule']['mismatched_cells'] == record['allocations']['mismatched_cells'] == 0,
              f'regression mismatch: {record["scenario"]}')
    baseline_path = ROOT/'outputs/question2/q2_star/schedule.csv'
    check(sha(baseline_path) == protocol['baseline_schedule_sha256'], 'baseline changed')
    baseline = {row['job_name']: row for row in csv_rows(baseline_path)}
    all_cases = {row['scenario']: row for row in csv_rows(out/'comparison.csv')}
    check(len(all_cases) == 18, '18 unique cases required')
    full_count = 0
    decimal_audits = 0
    full_details = []
    for policy_name, policy in protocol['policies'].items():
        for seed in protocol['seeds']:
            for speed in protocol['speeds']:
                name = f'{policy_name}_s{seed}_v{speed:g}'
                path = out/'cases'/name
                row = read(path/'comparison_row.json')
                check(name in all_cases, f'comparison missing {name}')
                check(row['seed'] == seed and row['speedup'] == speed, f'case parameters: {name}')
                receipt = read(path/'complete.json')
                check(receipt['protocol_sha256'] == sha(out/'protocol.json'), f'protocol receipt: {name}')
                for filename, expected in receipt['artifacts'].items():
                    check(sha(path/filename) == expected, f'case file changed: {name}/{filename}')
                expected_pass = (row['max_donor_mean_wait_delta_hours'] <= .5
                    and row['max_donor_p95_delta_hours'] <= 2 and row['a10_mean_wait_gain_hours'] > 0)
                check(row['strict_with_positive_gain'] == expected_pass, f'classification: {name}')
                if seed != protocol['seeds'][0]:
                    check(row['schedule_storage'] == 'metrics_only' and not (path/'schedule.csv').exists(),
                          f'metrics-only scope: {name}')
                    continue
                full_count += 1
                schedule = csv_rows(path/'schedule.csv')
                check(len(schedule) == len(baseline) == len({r['job_name'] for r in schedule}), f'job set: {name}')
                check({r['job_name'] for r in schedule} == set(baseline), f'job IDs: {name}')
                waits = {r['job_name']: Decimal(r['start_time'])-Decimal(r['submit_time']) for r in schedule}
                close(row['wait_hours_mean'], sum(waits.values())/len(waits)/3600, f'mean wait: {name}')
                limits = dict(policy['limits_seconds']) if policy else {}
                for record in schedule:
                    if record['migrated'].lower() == 'true' and policy is not None:
                        runtime = Decimal(record['original_duration'])/Decimal(str(speed))
                        check(runtime <= Decimal(str(limits[record['assigned_model']])), f'donor runtime: {name}')
                donor_means, donor_p95 = [], []
                for donor in ('A100-SXM4-80GB', 'H800'):
                    old = [Decimal(r['wait_time']) for r in baseline.values() if r['requested_model'] == donor]
                    new = [waits[r['job_name']] for r in schedule if r['requested_model'] == donor]
                    donor_means.append((sum(new)-sum(old))/len(old)/3600)
                    donor_p95.append((quantile(new)-quantile(old))/3600)
                close(row['max_donor_mean_wait_delta_hours'], max(donor_means), f'donor mean: {name}')
                close(row['max_donor_p95_delta_hours'], max(donor_p95), f'donor P95: {name}')
                a10 = [r for r in schedule if r['requested_model'] == 'A10']
                harmed = sum(waits[r['job_name']] > Decimal(baseline[r['job_name']]['wait_time']) for r in a10)
                close(row['a10_wait_increased_fraction'], Decimal(harmed)/len(a10), f'A10 harm: {name}')
                audit = read(path/'independent_validation.json')
                check(audit['passed'] and audit['failure_count'] == 0, f'Decimal audit: {name}')
                decimal_audits += audit['comparisons']
                full_details.append({'case': name, 'independent_decimal_comparisons': audit['comparisons']})
    for filename in ('schedule.csv', 'allocations.csv'):
        check(sha(out/'cases/reference_d75_s20261030_v1'/filename)
              == sha(out/'cases/runtime75_s20261030_v1'/filename), f'equal-speed identity: {filename}')
    for seed in protocol['seeds']:
        left = read(out/f'cases/reference_d75_s{seed}_v1/comparison_row.json')
        right = read(out/f'cases/runtime75_s{seed}_v1/comparison_row.json')
        for field in ('wait_hours_mean', 'max_donor_mean_wait_delta_hours',
                      'max_donor_p95_delta_hours', 'a10_wait_increased_fraction'):
            check(left[field] == right[field], f'equal-speed metric identity: {seed}/{field}')
    check(full_count == 6, 'six complete schedules required')
    report = {'passed': not failures, 'checks': checks, 'failure_count': len(failures), 'failures': failures,
        'historical_files': len(history), 'full_schedules': full_count, 'metrics_only': 12,
        'independent_decimal_comparisons': decimal_audits, 'full_details': full_details,
        'verifier_sha256': sha(Path(__file__)), 'protocol_sha256': sha(out/'protocol.json'),
        'limitations': 'No optimality or out-of-trace generalization claim; twelve metrics-only cases are not independently node-audited.'}
    (out/'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('passed', 'checks', 'failure_count', 'independent_decimal_comparisons')}))
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
