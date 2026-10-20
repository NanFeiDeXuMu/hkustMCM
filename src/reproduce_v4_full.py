"""Rebuild the three locked v4 first-seed full cases in a fresh isolated directory.

Works from the data-free supplement once original inputs are supplied. Rebuilds
Q2* as a prerequisite, verifies its frozen hash, and never writes old evidence.
This is explicit reproduction, not a reopened selection or another seed search.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

import numpy as np
import pandas as pd

import run_v4_validation as experiment
from run_v2_experiments import digest, read_json, write_json
from scheduler import run_simulation


def main():
    root = experiment.ROOT
    evidence = experiment.OUT
    aliases = read_json(evidence/'validation_aliases.json')
    protocol = read_json(evidence/'protocol.json')
    configs = {name: experiment.routing_from_manifest(value) for name, value in protocol['configurations'].items()}
    seed = protocol['validation_seeds'][0]
    expected = {name: read_json(evidence/'validation'/f'{name}_s{seed}'/'comparison_row.json') for name in set(aliases.values())}
    original_manifest = read_json(evidence/'validation'/f'{next(iter(aliases.values()))}_s{seed}'/'scenario.json')
    for name, expected_hash in original_manifest['source_sha256'].items():
        if digest(experiment.DATA/name) != expected_hash:
            raise ValueError(f'Original data hash mismatch: {name}')
    for name, expected_hash in original_manifest['code_sha256'].items():
        if digest(root/'src'/name) != expected_hash:
            raise ValueError(f'Frozen implementation hash mismatch: {name}')
    now = datetime.now(timezone.utc)
    destination = root/'outputs/v4_reproduction'/now.strftime('%Y%m%dT%H%M%S%fZ')
    destination.mkdir(parents=True, exist_ok=False)
    jobs = pd.read_csv(experiment.DATA/'job_info_clean.csv', dtype={'job_name': str}, float_precision='round_trip')
    nodes = pd.read_csv(experiment.DATA/'node_info_df.csv', dtype={'node_name': str}, float_precision='round_trip')
    print('Rebuild frozen Q2* prerequisite', flush=True)
    baseline, _, _ = run_simulation(jobs, nodes, policy='log_aging', log_aging_tau_seconds=604800)
    baseline_path = destination/'baseline'/'schedule.csv'
    baseline_path.parent.mkdir()
    baseline.to_csv(baseline_path, index=False)
    if digest(baseline_path) != original_manifest['baseline_schedule_sha256']:
        raise ValueError('Rebuilt baseline differs from frozen Q2* CSV')
    # The single-process wrapper configures paths explicitly; no child inherits
    # the ordinary experiment directory or original expired experiment budget.
    experiment.OUT = destination
    experiment.BASE = baseline_path
    experiment.JOBS, experiment.NODES, experiment.BASELINE = jobs, nodes, baseline
    provenance = experiment.provenance()
    provenance['reproduction_driver_sha256'] = digest(Path(__file__))
    provenance['original_protocol_sha256'] = digest(evidence/'protocol.json')
    checks = []
    for name in dict.fromkeys(aliases.values()):
        route = replace(configs[name], seed=seed)
        row = experiment.run_case((f'{name}_s{seed}', name, 'reproduction', experiment.asdict(route), True,
                                   provenance, (now+timedelta(hours=3)).isoformat()))
        for field in ('wait_hours_mean', 'wait_hours_max', 'wait_hours_p99', 'completed_at_90d',
                      'last_completion_days', 'a10_mean_wait_gain_hours', 'max_donor_mean_wait_delta_hours',
                      'max_donor_p95_delta_hours', 'donor_native_job_hours_added', 'compensation_gpu_hours'):
            if not np.isclose(row[field], expected[name][field], rtol=1e-12, atol=1e-10):
                raise AssertionError(f'Reproduction differs: {name}/{field}')
        checks.append({'configuration': name, 'path': row['output_path'], 'scalar_comparisons': 10, 'passed': True})
    write_json(destination/'reproduction_check.json', {'passed': True, 'cases': checks,
               'baseline_matches_frozen_hash': True, 'scope': 'Three locked first-validation-seed cases only; no reselection',
               'next_step': 'Run tests/verify_schedules.py --scenario-dir for each case path'})
    print(json.dumps({'output': str(destination), 'passed': True, 'cases': len(checks)}), flush=True)


if __name__ == '__main__':
    main()
