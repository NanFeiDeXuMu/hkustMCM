"""Reproduce one declared D033 case, preserving its explicit source protection.

The frozen generic SimulationSpec does not contain source protection. This
adapter requires the D033 manifest rather than silently dropping that control.
"""
import argparse
from pathlib import Path

import pandas as pd

from analyze_routing_frontier import analyze_routing_frontier
from analyze_source_protection import analyze_source_harm
from evaluate_scheduling import evaluate
from experiment_store import ExperimentStore, contained, digest, read_json, write_json
from reproduce_case import audit, compare_evidence
from simulation_contract import SimulationSpec
from source_guard_engine import run_simulation
from source_protection import SourceProtection

ROOT = Path(__file__).resolve().parents[1]


def reproduce(manifest_path, output, data, baseline_path):
    manifest_path, data, baseline_path = (Path(p).resolve() for p in (manifest_path, data, baseline_path))
    manifest = read_json(manifest_path)
    if manifest.get('experiment_version') != 'v6_source_protection' or 'source_protection' not in manifest:
        raise ValueError('An explicit D033 source-protection manifest is required')
    spec = SimulationSpec.from_manifest(manifest)
    control = manifest['source_protection']
    protection = SourceProtection(**control) if control is not None else None
    pinned = {manifest_path: digest(manifest_path), baseline_path: manifest['baseline_schedule_sha256']}
    pinned.update({data/name: value for name, value in manifest['source_sha256'].items()})
    pinned.update({contained(ROOT/'src', ROOT/'src'/name): value for name, value in manifest['code_sha256'].items()})
    pinned[Path(__file__).resolve()] = digest(Path(__file__))
    pinned[ROOT/'tests/verify_schedules.py'] = digest(ROOT/'tests/verify_schedules.py')
    source = manifest_path.parent
    expected = {name: digest(source/name) for name in ('schedule.csv', 'allocations.csv', 'comparison_row.json')
                if (source/name).exists()}
    pinned.update({source/name: value for name, value in expected.items()})

    def verify_pins():
        if any(digest(path) != value for path, value in pinned.items()):
            raise ValueError('Pinned input, source evidence or implementation changed')

    verify_pins()
    request = {'purpose': 'reproduce_d033_case', 'spec': spec.to_dict(), 'source_protection': control,
               'pins': {str(path): value for path, value in pinned.items()}, 'expected_artifacts': expected}

    def produce(stage):
        jobs = pd.read_csv(data/'job_info_clean.csv', dtype={'job_name': str}, float_precision='round_trip')
        nodes = pd.read_csv(data/'node_info_df.csv', dtype={'node_name': str}, float_precision='round_trip')
        base = pd.read_csv(baseline_path, dtype={'job_name': str}, float_precision='round_trip')
        schedule, allocation, diagnostics = run_simulation(jobs, nodes, policy=spec.policy,
            routing=spec.routing, admission=spec.admission, source_protection=protection,
            log_aging_tau_seconds=spec.log_aging_tau_seconds, aging_alpha=spec.aging_alpha,
            max_backfill_scan=spec.max_backfill_scan)
        metrics = evaluate(schedule, nodes, stage)
        frontier = analyze_routing_frontier(base, schedule, nodes, spec.routing.donors, stage)
        harm = analyze_source_harm(base, schedule, stage)
        values = {k: v for k, v in (metrics | frontier | harm).items() if not isinstance(v, (dict, list))}
        if protection is not None and (harm['a10_harmed_jobs'] or harm['a10_positive_wait_job_hours']):
            raise AssertionError('Source protection failed during reproduction')
        schedule.to_csv(stage/'schedule.csv', index=False)
        allocation.to_csv(stage/'allocations.csv', index=False)
        write_json(stage/'comparison_row.json', values)
        write_json(stage/'scenario.json', {**manifest, 'diagnostics': diagnostics, 'schedule_storage': 'full'})
        compare_evidence(stage, source, expected, values)
        audit(stage, data, spec)
        verify_pins()

    return ExperimentStore(output).run(source.name, request, produce)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT/'outputs/v6_source_reproduction')
    parser.add_argument('--data-dir', type=Path, default=ROOT/'2026_MCM_Senior_GPU_Scheduling/2026_MCM_Senior_GPU_Scheduling')
    parser.add_argument('--baseline', type=Path, default=ROOT/'outputs/question2/q2_star/schedule.csv')
    args = parser.parse_args()
    result = reproduce(args.scenario, args.output, args.data_dir, args.baseline)
    print(f'passed=True cache_hit={result.cache_hit} output={result.path}')


if __name__ == '__main__':
    main()
