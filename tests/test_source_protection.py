"""Hand-checkable shadow deadlines, node loans, online causality and no-harm properties."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from scheduler import RoutingConfig, _prepare, _Running, run_simulation as baseline_engine
from experimental_scheduler import run_simulation as old_engine
from source_guard_engine import run_simulation
from source_protection import SourceProtection, NodeLoans
from routing_admission import RuntimeAdmission
from test_scheduler import jobs, nodes, assert_schedule


class SourceProtectionTests(unittest.TestCase):
    def test_gang_borrower_must_fit_every_node_deadline(self):
        j = jobs(('owner_a', 'A10', 1, 0., 1, 0, 10),
                 ('owner_b', 'A10', 1, 0., 1, 0, 20),
                 ('borrower', 'A10', 1, .25, 2, 1, 5))
        n = nodes(('a', 'A10', 1, .5), ('b', 'A10', 1, 1.))
        prepared, pools = _prepare(j, n, 'log_aging', None, 0., 604800.)
        by_name = {job.name: job for job in prepared}
        loans = NodeLoans(2)
        loans.add(_Running(by_name['owner_a'], 10., [(0, 1)], False))
        loans.add(_Running(by_name['owner_b'], 20., [(1, 1)], False))
        self.assertIsNone(loans.placement(by_name['borrower'], pools['A10'], 6.))
        placement = loans.placement(by_name['borrower'], pools['A10'], 5.)
        self.assertEqual(placement, [(0, 1), (1, 1)])
        loans.reserve(by_name['borrower'], placement, 5.)
        with self.assertRaisesRegex(AssertionError, 'exceeds'):
            loans.expire(10.)
        loans.release(by_name['borrower'])
        loans.expire(10.)
        self.assertEqual(loans.owner[0], -1)
        self.assertEqual(loans.owner[1], by_name['owner_b'].index)

    def fixture(self, duration=1, submit=11, workers=1, cpu=.25):
        j = jobs(('a', 'A10', 1, 0., workers, 0, 10),
                 ('b', 'A10', 1, 0., workers, 1, 5),
                 ('c', 'A10', 1, cpu, workers, submit, duration),
                 ('native', 'H800', workers, 1., 1, 6, 100))
        n = nodes(*[(f'a{i}', 'A10', 1, 1.) for i in range(workers)], ('h', 'H800', workers, 2.))
        route = RoutingConfig(mode='pull', donors=('H800',), flexible_fraction=1.,
                              donor_cap_fraction=1., max_routed_duration_seconds=5.)
        return j, n, route

    def test_exact_shadow_without_loans_does_not_delay_any_source_job(self):
        j, n, route = self.fixture()
        base = baseline_engine(j, n, policy='log_aging', log_aging_tau_seconds=604800)[0].set_index('job_name')
        s, a, stats = run_simulation(j, n, policy='log_aging', log_aging_tau_seconds=604800,
                                    routing=route, source_protection=SourceProtection())
        assert_schedule(self, j, n, s, a, route)
        s = s.set_index('job_name')
        self.assertEqual(s.loc['b', 'assigned_model'], 'H800')
        self.assertEqual(s.loc['c', 'start_time'], base.loc['c', 'start_time'])
        self.assertEqual(stats['source_loan_jobs'], 0)

    def test_loan_borrows_idle_cpu_even_when_shadow_owner_requested_zero(self):
        j, n, route = self.fixture()
        s, a, stats = run_simulation(j, n, policy='log_aging', routing=route,
                                    source_protection=SourceProtection(128))
        assert_schedule(self, j, n, s, a, route)
        self.assertEqual(s.set_index('job_name').loc['c', 'start_time'], 11.)
        self.assertTrue(s.set_index('job_name').loc['c', 'backfilled'])
        self.assertEqual(stats['source_loan_jobs'], 1)

    def test_loan_must_finish_by_shadow_expiry_inclusive(self):
        for submit, expected_start in ((10, 10), (11, 15)):
            j, n, route = self.fixture(duration=5, submit=submit)
            s, a, _ = run_simulation(j, n, policy='log_aging', routing=route,
                                     source_protection=SourceProtection(128))
            assert_schedule(self, j, n, s, a, route)
            self.assertEqual(s.set_index('job_name').loc['c', 'start_time'], expected_start)

    def test_multiworker_gang_and_zero_fractional_cpu(self):
        for cpu in (0., .25):
            j, n, route = self.fixture(workers=2, cpu=cpu)
            s, a, _ = run_simulation(j, n, policy='log_aging', routing=route,
                                     source_protection=SourceProtection(128))
            assert_schedule(self, j, n, s, a, route)
            self.assertEqual(s.set_index('job_name').loc['c', 'start_time'], 11.)
            self.assertEqual(len(a[a.job_name.eq('c')]), 2)

    def test_no_future_arrivals_change_earlier_starts(self):
        j, n, route = self.fixture()
        future = jobs(('future', 'A10', 1, .5, 1, 12, 2))
        kwargs = dict(policy='log_aging', routing=route, source_protection=SourceProtection(128))
        before = run_simulation(j, n, **kwargs)[0]
        after = run_simulation(pd.concat([j, future], ignore_index=True), n, **kwargs)[0]
        pd.testing.assert_frame_equal(before[before.start_time < 12].reset_index(drop=True),
                                      after[after.start_time < 12].reset_index(drop=True), check_exact=True)

    def test_shadow_without_migration_reproduces_original_schedule(self):
        j, n, _ = self.fixture()
        old = baseline_engine(j, n, policy='log_aging')
        new = run_simulation(j, n, policy='log_aging', source_protection=SourceProtection(128))
        pd.testing.assert_frame_equal(old[0], new[0], check_exact=True)
        pd.testing.assert_frame_equal(old[1], new[1], check_exact=True)

    def test_disabled_control_preserves_v5_exactly(self):
        j, n, route = self.fixture()
        admission = RuntimeAdmission((('H800', 5.),))
        for policy in ('fcfs', 'sjf', 'backfill', 'aging', 'log_aging'):
            old = old_engine(j, n, policy=policy, routing=route, admission=admission)
            new = run_simulation(j, n, policy=policy, routing=route, admission=admission)
            pd.testing.assert_frame_equal(old[0], new[0], check_exact=True)
            pd.testing.assert_frame_equal(old[1], new[1], check_exact=True)

    def test_unsupported_source_geometry_and_mode_rejected(self):
        j, n, route = self.fixture()
        n.loc[n.gpu_model.eq('A10'), 'gpu_capacity_num'] = 2
        with self.assertRaises(ValueError):
            run_simulation(j, n, policy='log_aging', source_protection=SourceProtection())
        j, n, route = self.fixture()
        with self.assertRaises(ValueError):
            run_simulation(j, n, policy='sjf', source_protection=SourceProtection())
        with self.assertRaises(ValueError):
            run_simulation(j, n, policy='log_aging', routing=replace(route, mode='arrival'),
                           source_protection=SourceProtection())
        for scan in (-1, 1.5, True):
            with self.assertRaises(ValueError):
                SourceProtection(scan)

    def test_no_wait_harm_for_generated_arrivals_and_both_speeds(self):
        n = nodes(('a', 'A10', 1, 1.), ('b', 'A10', 1, .5), ('h', 'H800', 4, 4.))
        for seed in range(6):
            rng = np.random.default_rng(seed)
            records = [(str(i), 'A10' if i % 5 else 'H800', 1, (i % 3)/4, 1+(i % 2),
                        int(rng.integers(0, 90)), int(rng.integers(1, 60))) for i in range(60)]
            j = jobs(*records)
            base = baseline_engine(j, n, policy='log_aging', log_aging_tau_seconds=15.)[0].set_index('job_name')
            for speed in (1., .8):
                for scan in (0, 8):
                    route = RoutingConfig(mode='pull', donors=('H800',), speedup=speed, seed=seed)
                    s, a, _ = run_simulation(j, n, policy='log_aging', log_aging_tau_seconds=15.,
                        routing=route, source_protection=SourceProtection(scan))
                    assert_schedule(self, j, n, s, a, route)
                    source = s[s.requested_model.eq('A10')].set_index('job_name')
                    self.assertTrue((source.start_time <= base.loc[source.index, 'start_time']).all())


if __name__ == '__main__':
    unittest.main()
