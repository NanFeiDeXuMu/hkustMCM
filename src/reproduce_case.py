"""Reproduce one frozen scenario through the common engine and artifact contracts.

No grid search or parameter selection. Reads a historical scenario.json and
publishes only after output comparisons and an independent full audit succeed.
Failed/interrupted attempts remain in staging/; completed cases are reusable.
"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal
import json
from pathlib import Path
import platform
import subprocess
import sys

import numpy as np
import pandas as pd

from analyze_routing_frontier import analyze_routing_frontier
from evaluate_scheduling import evaluate
from experiment_store import ExperimentStore, contained, digest, read_json, write_json
from simulation_contract import SimulationSpec, simulate

ROOT = Path(__file__).resolve().parents[1]
CODE = ('scheduler.py', 'experimental_scheduler.py', 'routing_admission.py',
        'simulation_contract.py', 'experiment_store.py', 'reproduce_case.py',
        'evaluate_scheduling.py', 'analyze_routing_frontier.py')


def audit(stage, data_dir, spec):
    """Run the frozen independent verifier as a process, not a Python dependency."""
    completed = subprocess.run([sys.executable, str(ROOT/'tests/verify_schedules.py'),
        '--scenario-dir', str(stage), '--data-dir', str(data_dir)],
        capture_output=True, text=True, check=False)
    (stage/'audit_stdout.txt').write_text(completed.stdout, encoding='utf-8')
    (stage/'audit_stderr.txt').write_text(completed.stderr, encoding='utf-8')
    if completed.returncode:
        raise RuntimeError(f'Independent schedule audit failed with exit code {completed.returncode}')
    report = read_json(stage/'independent_validation.json')
    if not report['passed'] or report['failure_count']:
        raise AssertionError('Independent schedule audit reported failures')
    admission_checks = 0
    if spec.admission is not None:
        limits = dict(spec.admission.limits_seconds)
        with (stage/'schedule.csv').open(encoding='utf-8', newline='') as stream:
            for row in csv.DictReader(stream):
                if row['migrated'].lower() == 'true':
                    admission_checks += 1
                    runtime = Decimal(row['original_duration'])/Decimal(str(spec.routing.speedup))
                    if runtime > Decimal(str(limits[row['assigned_model']])):
                        raise AssertionError('Independent donor runtime admission check failed')
    report['donor_runtime_admission_checks'] = admission_checks
    report['publication_note'] = 'Audited in staging before atomic publication; file basenames and hashes are preserved.'
    write_json(stage/'independent_validation.json', report)


def compare_evidence(stage, source, expected_hashes, values):
    comparisons = []
    for name in ('schedule.csv', 'allocations.csv'):
        if name in expected_hashes:
            if digest(stage/name) != expected_hashes[name]:
                raise AssertionError(f'Reproduction differs byte-for-byte: {name}')
            comparisons.append(name)
    scalars = 0
    if 'comparison_row.json' in expected_hashes:
        expected = read_json(source/'comparison_row.json')
        for field, actual in values.items():
            if field not in expected or isinstance(actual, (dict, list)):
                continue
            old = expected[field]
            if isinstance(actual, (int, float)) and not isinstance(actual, bool):
                if old is None or not np.isclose(actual, old, rtol=1e-12, atol=1e-10):
                    raise AssertionError(f'Reproduction metric differs: {field}')
            elif actual != old:
                raise AssertionError(f'Reproduction metric differs: {field}')
            scalars += 1
    if not comparisons and not scalars:
        raise ValueError('Source needs a full schedule or comparable metrics as reproduction evidence')
    write_json(stage/'reproduction_check.json', {'passed': True,
        'exact_csv_comparisons': comparisons, 'scalar_comparisons': scalars,
        'scope': 'Identical scenario reproduction; no new scientific experiment or reselection'})


def reproduce(source_manifest, output, data_dir, baseline_path):
    source_manifest = Path(source_manifest).resolve()
    source = source_manifest.parent
    data_dir, baseline_path = Path(data_dir).resolve(), Path(baseline_path).resolve()
    manifest = read_json(source_manifest)
    spec = SimulationSpec.from_manifest(manifest)
    inputs = {name: digest(data_dir/name) for name in ('job_info_clean.csv', 'node_info_df.csv')}
    if inputs != manifest['source_sha256']:
        raise ValueError('Source input hashes differ from historical evidence')
    for name, expected in manifest['code_sha256'].items():
        if digest(contained(ROOT/'src', ROOT/'src'/name)) != expected:
            raise ValueError(f'Historical implementation changed: {name}')
    baseline_hash = digest(baseline_path)
    if manifest.get('baseline_schedule_sha256', baseline_hash) != baseline_hash:
        raise ValueError('Paired baseline differs from historical evidence')
    expected_hashes = {name: digest(source/name) for name in
        ('schedule.csv', 'allocations.csv', 'comparison_row.json') if (source/name).exists()}
    code_hashes = {name: digest(ROOT/'src'/name) for name in CODE}
    verifier_hash = digest(ROOT/'tests/verify_schedules.py')
    request = {'schema_version': 1, 'purpose': 'reproduce_frozen_scenario', 'spec': spec.to_dict(),
        'source_manifest_sha256': digest(source_manifest), 'source_sha256': inputs,
        'baseline_schedule_sha256': baseline_hash, 'code_sha256': code_hashes,
        'verifier_sha256': verifier_hash, 'expected_artifacts': expected_hashes,
        'versions': {'python': platform.python_version(), 'numpy': np.__version__, 'pandas': pd.__version__}}

    def produce(stage):
        jobs = pd.read_csv(data_dir/'job_info_clean.csv', dtype={'job_name': str}, float_precision='round_trip')
        nodes = pd.read_csv(data_dir/'node_info_df.csv', dtype={'node_name': str}, float_precision='round_trip')
        baseline = pd.read_csv(baseline_path, dtype={'job_name': str}, float_precision='round_trip')
        if (baseline.job_name.duplicated().any() or set(baseline.job_name) != set(jobs.job_name)):
            raise ValueError('Baseline must contain exactly the same unique string job IDs')
        schedule, allocation, diagnostics = simulate(jobs, nodes, spec)
        metrics = evaluate(schedule, nodes, stage)
        frontier = (analyze_routing_frontier(baseline, schedule, nodes, spec.routing.donors, stage)
                    if spec.routing else {})
        values = {key: value for key, value in (metrics | frontier).items() if not isinstance(value, (list, dict))}
        write_json(stage/'comparison_row.json', values)
        write_json(stage/'scenario.json', {'name': source.name, 'experiment_version': 'common_runtime_reproduction',
            **spec.to_dict(), 'source_sha256': inputs, 'code_sha256': code_hashes,
            'baseline_schedule_sha256': baseline_hash, 'schedule_storage': 'full', 'diagnostics': diagnostics,
            'source_manifest_sha256': request['source_manifest_sha256']})
        schedule.to_csv(stage/'schedule.csv', index=False)
        allocation.to_csv(stage/'allocations.csv', index=False)
        compare_evidence(stage, source, expected_hashes, values)
        audit(stage, data_dir, spec)
        # Reject a live edit of any source or implementation during execution.
        pinned = {source_manifest: request['source_manifest_sha256'], baseline_path: baseline_hash,
                  ROOT/'tests/verify_schedules.py': verifier_hash}
        pinned.update({data_dir/name: value for name, value in inputs.items()})
        pinned.update({ROOT/'src'/name: value for name, value in code_hashes.items()})
        pinned.update({source/name: value for name, value in expected_hashes.items()})
        if any(digest(path) != expected for path, expected in pinned.items()):
            raise ValueError('Pinned input, reference, or implementation changed during execution')

    return ExperimentStore(output).run(source.name, request, produce)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', required=True, type=Path, help='Historical scenario.json')
    parser.add_argument('--output', type=Path, default=ROOT/'outputs/common_runtime_reproduction')
    parser.add_argument('--data-dir', type=Path,
        default=ROOT/'2026_MCM_Senior_GPU_Scheduling/2026_MCM_Senior_GPU_Scheduling')
    parser.add_argument('--baseline', type=Path, default=ROOT/'outputs/question2/q2_star/schedule.csv')
    args = parser.parse_args()
    result = reproduce(args.scenario, args.output, args.data_dir, args.baseline)
    print(json.dumps({'output': str(result.path), 'cache_hit': result.cache_hit, 'passed': True}))


if __name__ == '__main__':
    main()
