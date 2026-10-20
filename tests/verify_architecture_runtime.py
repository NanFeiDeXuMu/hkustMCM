"""Verify D032 preservation, reproduction, publication receipts and audit binding."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT/'outputs/architecture_refactor_review'
CASES = ROOT/'outputs/common_runtime_reproduction/cases'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            result.update(block)
    return result.hexdigest()


def main():
    failures, details = [], []
    checks = 0

    def check(condition, message):
        nonlocal checks
        checks += 1
        if not condition:
            failures.append(message)

    historical = read(REVIEW/'historical_hashes.json')
    for name, expected in historical.items():
        check(digest(ROOT/name) == expected, f'Historical file changed: {name}')
    for name in read(REVIEW/'protocol.json')['cases']:
        path = CASES/name
        request, receipt = read(path/'request.json'), read(path/'complete.json')
        canonical = json.dumps(request, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        check(hashlib.sha256(canonical.encode()).hexdigest() == receipt['request_sha256'], f'Request receipt: {name}')
        files = {p.relative_to(path).as_posix(): digest(p) for p in path.rglob('*')
                 if p.is_file() and p != path/'complete.json'}
        check(files == receipt['artifacts'], f'Artifact receipt: {name}')
        for filename, expected in request['code_sha256'].items():
            check(digest(ROOT/'src'/filename) == expected, f'Current code differs: {filename}')
        check(digest(ROOT/'tests/verify_schedules.py') == request['verifier_sha256'], f'Verifier differs: {name}')
        comparison = read(path/'reproduction_check.json')
        check(comparison['passed'] and comparison['scalar_comparisons'] > 0, f'Reproduction comparison: {name}')
        original = ROOT/'outputs/v5_architecture/cases'/name
        for filename in comparison['exact_csv_comparisons']:
            check(digest(original/filename) == digest(path/filename), f'Exact CSV mismatch: {name}/{filename}')
        audit = read(path/'independent_validation.json')
        check(audit['passed'] and audit['failure_count'] == 0, f'Independent audit: {name}')
        audit_hashes = {Path(filename).name: value for filename, value in audit['file_sha256'].items()}
        for filename in ('schedule.csv', 'allocations.csv', 'scenario.json'):
            check(audit_hashes[filename] == digest(path/filename), f'Audit not bound to published file: {name}/{filename}')
        details.append({'case': name, 'scalar_comparisons': comparison['scalar_comparisons'],
            'exact_csv_comparisons': len(comparison['exact_csv_comparisons']),
            'decimal_comparisons': audit['comparisons'],
            'donor_runtime_admission_checks': audit['donor_runtime_admission_checks']})
    cache = read(REVIEW/'cache_replay.json')
    check(cache['passed'] and len(cache['cases']) == 3 and cache['staging_directories'] == 0, 'Cache replay')
    check(all(case['cache_hit'] for case in cache['cases']), 'All replay cases must be cached')
    report = {'passed': not failures, 'checks': checks, 'failure_count': len(failures), 'failures': failures,
        'historical_files_unchanged': len(historical), 'cases': details,
        'independent_decimal_comparisons': sum(row['decimal_comparisons'] for row in details),
        'donor_runtime_admission_checks': sum(row['donor_runtime_admission_checks'] for row in details),
        'cache_files_unchanged': cache['files_unchanged'], 'verifier_sha256': digest(Path(__file__)),
        'scope': 'Engineering reproduction of known cases; no additional scientific samples'}
    (REVIEW/'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('passed', 'checks', 'failure_count', 'independent_decimal_comparisons')}))
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
