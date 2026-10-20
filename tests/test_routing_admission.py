"""Runtime units, donor isolation, bounded-prefix and reference equivalence."""
from dataclasses import replace
import math
from pathlib import Path
import sys
import unittest

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from experimental_scheduler import run_simulation
from routing_admission import RuntimeAdmission
from scheduler import RoutingConfig, run_simulation as reference
from test_scheduler import jobs, nodes, assert_schedule


class AdmissionTests(unittest.TestCase):
    def test_runtime_uses_donor_speed_and_inclusive_boundary(self):
        policy = RuntimeAdmission((('H800', 75.),))
        self.assertTrue(policy.allows(60., .8, 'H800'))
        self.assertFalse(policy.allows(60.0001, .8, 'H800'))
        self.assertFalse(policy.allows(75., .8, 'H800'))
        self.assertTrue(policy.allows(75., 1., 'H800'))

    def test_donor_limits_are_independent(self):
        policy = RuntimeAdmission((('A100', 75.), ('H800', 30.)))
        self.assertTrue(policy.allows(60., 1., 'A100'))
        self.assertFalse(policy.allows(60., 1., 'H800'))
        with self.assertRaises(ValueError):
            policy.allows(1., 1., 'other')

    def test_invalid_limits_and_speed_are_rejected(self):
        for limits in ((), (('H800', -1.),), (('H800', math.inf),),
                       (('H800', math.nan),), (('H800', 1.), ('H800', 2.))):
            with self.assertRaises(ValueError):
                RuntimeAdmission(limits)
        policy = RuntimeAdmission((('H800', 0.),))
        self.assertFalse(policy.allows(1., 1., 'H800'))
        for speed in (0., -1., math.inf, math.nan):
            with self.assertRaises(ValueError):
                policy.allows(1., speed, 'H800')

    def test_rejected_jobs_do_not_hide_eligible_jobs_in_prefix(self):
        j = jobs(('0', 'A10', 1, 0., 1, 0, 200),
                 ('1', 'A10', 1, 0., 1, 1, 100), ('2', 'A10', 1, 0., 1, 1, 10))
        n = nodes(('a', 'A10', 1, 1), ('h', 'H800', 2, 1))
        route = RoutingConfig(mode='pull', donors=('H800',), flexible_fraction=1.,
                              donor_cap_fraction=1., pull_scan_limit=1)
        policy = RuntimeAdmission((('H800', 20.),))
        s, a, _ = run_simulation(j, n, routing=route, admission=policy)
        assert_schedule(self, j, n, s, a, route)
        result = s.set_index('job_name')
        self.assertEqual(result.loc['1', 'assigned_model'], 'A10')
        self.assertEqual(result.loc['2', 'assigned_model'], 'H800')
        self.assertEqual(result.loc['2', 'start_time'], 1.)

    def test_donor_rejection_can_fall_through_to_next_donor(self):
        j = jobs(('0', 'A10', 1, 0., 1, 0, 200), ('1', 'A10', 1, .25, 2, 1, 40))
        n = nodes(('a', 'A10', 2, 1), ('h', 'H800', 2, 1), ('x', 'A100', 2, 1))
        route = RoutingConfig(mode='pull', donors=('H800', 'A100'), flexible_fraction=1.,
                              donor_cap_fraction=1.)
        policy = RuntimeAdmission((('H800', 30.), ('A100', 75.)))
        s, a, stats = run_simulation(j, n, routing=route, admission=policy)
        assert_schedule(self, j, n, s, a, route)
        self.assertEqual(s.set_index('job_name').loc['1', 'assigned_model'], 'A100')
        self.assertGreater(stats['runtime_admission_rejections'], 0)

    def test_no_future_arrival_information_in_admission(self):
        j = jobs(('0', 'A10', 1, 0., 1, 0, 200), ('1', 'A10', 1, 0., 1, 1, 40))
        n = nodes(('a', 'A10', 1, 1), ('h', 'H800', 2, 1))
        route = RoutingConfig(mode='pull', donors=('H800',), flexible_fraction=1., donor_cap_fraction=1.)
        policy = RuntimeAdmission((('H800', 75.),))
        s, _, _ = run_simulation(j, n, routing=route, admission=policy)
        future = jobs(('future', 'H800', 2, .5, 1, 20, 100))
        extended, _, _ = run_simulation(pd.concat([j, future]), n, routing=route, admission=policy)
        pd.testing.assert_frame_equal(s[s.start_time < 20].reset_index(drop=True),
            extended[(extended.start_time < 20) & extended.job_name.isin(s.job_name)].reset_index(drop=True))

    def test_unsupported_mode_or_missing_donor_is_rejected(self):
        j = jobs(('1', 'A10', 1, 0., 1, 0, 1))
        n = nodes(('a', 'A10', 1, 1), ('h', 'H800', 1, 1))
        policy = RuntimeAdmission((('H800', 1.),))
        for route in (None, RoutingConfig(donors=('H800',)),
                      RoutingConfig(mode='pull', donors=('H800', 'other'))):
            with self.assertRaises(ValueError):
                run_simulation(j, n, routing=route, admission=policy)

    def test_disabled_policy_exactly_preserves_event_semantics(self):
        j = jobs(*[(str(i), 'A10' if i % 3 else 'H800', 1, (i % 4)/4, 1+(i % 2),
                    i//3, 1+(i*7 % 23)) for i in range(45)])
        n = nodes(('a', 'A10', 4, 4), ('b', 'A10', 4, 4), ('h', 'H800', 8, 8))
        for mode in ('arrival', 'pull'):
            route = RoutingConfig(mode=mode, donors=('H800',), flexible_fraction=.7,
                                  min_wait_seconds=3., donor_cap_fraction=.5)
            for policy in ('fcfs', 'sjf', 'backfill', 'aging', 'log_aging'):
                for routing in (None, route):
                    kwargs = dict(policy=policy, routing=routing, aging_alpha=2., log_aging_tau_seconds=7.)
                    old = reference(j, n, **kwargs)
                    new = run_simulation(j, n, **kwargs)
                    pd.testing.assert_frame_equal(old[0], new[0], check_exact=True)
                    pd.testing.assert_frame_equal(old[1], new[1], check_exact=True)


if __name__ == '__main__':
    unittest.main()
