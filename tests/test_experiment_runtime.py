"""Case publication, recovery, corruption rejection and engine-contract semantics."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from experiment_store import ExperimentStore, canonical, read_json
from simulation_contract import SimulationSpec, simulate
from scheduler import RoutingConfig, run_simulation
from routing_admission import RuntimeAdmission
from experimental_scheduler import run_simulation as admission_engine
from test_scheduler import jobs, nodes


class StoreTests(unittest.TestCase):
    def setUp(self):
        # Keep temporary fixtures inside the explicitly writable workspace.
        self.temporary = tempfile.TemporaryDirectory(prefix='.runtime-test-', dir=ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.store = ExperimentStore(Path(self.temporary.name)/'results')

    @staticmethod
    def produce(stage):
        (stage/'answer.txt').write_text('verified result', encoding='utf-8')

    def test_success_is_reused_without_rerunning_producer(self):
        first = self.store.run('case', {'seed': 1}, self.produce)
        receipt = (first.path/'complete.json').read_bytes()
        second = self.store.run('case', {'seed': 1}, lambda _: self.fail('Cache reran producer'))
        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertEqual(receipt, (second.path/'complete.json').read_bytes())

    def test_failure_is_preserved_and_retry_uses_new_stage(self):
        def failed(stage):
            (stage/'partial.txt').write_text('incomplete', encoding='utf-8')
            raise ValueError('simulated audit failure')
        with self.assertRaisesRegex(ValueError, 'audit failure'):
            self.store.run('case', {}, failed)
        self.assertFalse((self.store.root/'cases/case').exists())
        old_stage = next((self.store.root/'staging').iterdir())
        self.assertEqual(read_json(old_stage/'failure.json')['type'], 'ValueError')
        result = self.store.run('case', {}, self.produce)
        self.assertTrue((result.path/'complete.json').exists())
        self.assertTrue((old_stage/'partial.txt').exists())
        self.assertFalse((result.path/'partial.txt').exists())

    def test_configuration_or_provenance_change_cannot_reuse_case(self):
        self.store.run('case', {'seed': 1, 'input_hash': 'first'}, self.produce)
        for request in ({'seed': 2, 'input_hash': 'first'}, {'seed': 1, 'input_hash': 'changed'}):
            with self.assertRaisesRegex(ValueError, 'different configuration'):
                self.store.run('case', request, self.produce)

    def test_corrupted_missing_and_extra_artifacts_are_rejected(self):
        for key, action in (
            ('changed', lambda p: (p/'answer.txt').write_text('changed')),
            ('missing', lambda p: (p/'answer.txt').unlink()),
            ('added', lambda p: (p/'unexpected.txt').write_text('new'))):
            result = self.store.run(key, {}, self.produce)
            action(result.path)
            with self.assertRaisesRegex(ValueError, 'missing, added, or changed'):
                self.store.run(key, {}, self.produce)

    def test_case_ids_cannot_escape_or_use_reserved_windows_names(self):
        for key in ('../outside', 'a/b', 'a\\b', 'C:escape', '', 'CON', 'NUL.txt', 'ends.'):
            with self.assertRaises(ValueError):
                self.store.run(key, {}, self.produce)

    def test_nonfinite_provenance_is_rejected_before_start(self):
        with self.assertRaises(ValueError):
            self.store.run('case', {'value': float('nan')}, self.produce)
        self.assertFalse(list((self.store.root/'staging').iterdir()))

    def test_producer_cannot_replace_request_metadata(self):
        def wrong(stage):
            (stage/'request.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'reserved lifecycle'):
            self.store.run('case', {'seed': 1}, wrong)
        self.assertFalse((self.store.root/'cases/case').exists())

    def test_process_interruption_releases_lock_and_preserves_partial_evidence(self):
        code = '''
from pathlib import Path
import sys, time
sys.path.insert(0, sys.argv[1])
from experiment_store import ExperimentStore
def produce(stage):
    (stage/'started').write_text('partial')
    time.sleep(60)
ExperimentStore(sys.argv[2]).run('case', {}, produce)
'''
        child = subprocess.Popen([sys.executable, '-c', code, str(ROOT/'src'), str(self.store.root)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic()+10
            while not list((self.store.root/'staging').glob('*/started')) and time.monotonic() < deadline:
                if child.poll() is not None:
                    self.fail(child.communicate()[1].decode())
                time.sleep(.02)
            self.assertTrue(list((self.store.root/'staging').glob('*/started')))
            with self.assertRaisesRegex(RuntimeError, 'Another writer'):
                self.store.run('case', {}, self.produce)
            child.terminate()
            child.wait(timeout=10)
            result = self.store.run('case', {}, self.produce)
            self.assertFalse(result.cache_hit)
            self.assertTrue(list((self.store.root/'staging').glob('*/started')))
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)
            child.communicate()


class ContractTests(unittest.TestCase):
    def test_json_round_trip_retains_routing_and_donor_limits(self):
        spec = SimulationSpec(routing=RoutingConfig(mode='pull', donors=('H800',), donor_caps=(('H800', .1),)),
                              admission=RuntimeAdmission((('H800', 30.),)))
        restored = SimulationSpec.from_dict(json.loads(canonical(spec.to_dict())))
        self.assertEqual(spec, restored)
        self.assertEqual(restored.routing.donor_caps, (('H800', .1),))

    def test_unknown_config_and_incompatible_strategy_fail_early(self):
        with self.assertRaises(TypeError):
            SimulationSpec.from_dict({'polciy': 'sjf'})
        with self.assertRaises(ValueError):
            SimulationSpec(admission=RuntimeAdmission((('H800', 1.),)))
        for values in ({'max_backfill_scan': True}, {'aging_alpha': -1},
                       {'log_aging_tau_seconds': 0}, {'policy': 'unregistered'}):
            with self.assertRaises(ValueError):
                SimulationSpec(**values)

    def test_legacy_manifest_defaults_match_original_engine(self):
        spec = SimulationSpec.from_manifest({'policy': 'sjf', 'name': 'historical', 'routing': None})
        self.assertEqual(spec.log_aging_tau_seconds, 86400.)
        with self.assertRaises(ValueError):
            SimulationSpec.from_manifest({'name': 'missing policy'})

    def test_contract_preserves_reference_and_admission_engine_results(self):
        job_data = jobs(('0001', 'A10', 1, 0., 1, 0, 100),
                        ('0002', 'A10', 1, .25, 1, 1, 40), ('0003', 'H800', 2, .5, 1, 5, 2))
        node_data = nodes(('a', 'A10', 1, 1), ('h', 'H800', 2, 2))
        original_jobs, original_nodes = job_data.copy(deep=True), node_data.copy(deep=True)
        route = RoutingConfig(mode='pull', donors=('H800',), flexible_fraction=1., donor_cap_fraction=1.)
        for policy in ('fcfs', 'sjf', 'backfill', 'aging', 'log_aging'):
            for admission in (None, RuntimeAdmission((('H800', 30.),))):
                spec = SimulationSpec(policy=policy, routing=route, admission=admission,
                                      aging_alpha=2., log_aging_tau_seconds=7., max_backfill_scan=1)
                kwargs = dict(policy=policy, routing=route, aging_alpha=2., log_aging_tau_seconds=7., max_backfill_scan=1)
                expected = (admission_engine(job_data, node_data, admission=admission, **kwargs)
                            if admission else run_simulation(job_data, node_data, **kwargs))
                actual = simulate(job_data, node_data, spec)
                pd.testing.assert_frame_equal(actual[0], expected[0], check_exact=True)
                pd.testing.assert_frame_equal(actual[1], expected[1], check_exact=True)
        pd.testing.assert_frame_equal(job_data, original_jobs)
        pd.testing.assert_frame_equal(node_data, original_nodes)


if __name__ == '__main__':
    unittest.main()
