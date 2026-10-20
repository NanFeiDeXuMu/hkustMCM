"""Hand-calculated harm accounting and selection without validation leakage."""
from pathlib import Path
import sys
import unittest

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from analyze_source_protection import analyze_source_harm
from run_v6_source_protection import choose, DEV_SEEDS, SPEEDS


class SourceHarmAnalysisTests(unittest.TestCase):
    def pair(self):
        base = pd.DataFrame({
            'job_name': ['001', '002', '003'], 'requested_model': ['A10', 'A10', 'H800'],
            'original_duration': [3600., 7200., 3600.], 'submit_time': [0., 0., 0.],
            'gpu_request': [1, 1, 1], 'cpu_request': [0., .25, 1.], 'worker_num': [2, 1, 1],
            'start_time': [7200., 7200., 0.], 'end_time': [10800., 14400., 3600.],
            'migrated': [False, False, False]})
        new = base.copy()
        new.loc[0, ['start_time', 'end_time']] = [10800., 14400.]
        new.loc[1, ['start_time', 'end_time', 'migrated']] = [0., 18000., True]
        new.loc[2, ['start_time', 'end_time']] = [100000., 103600.]
        return base, new

    def test_positive_loss_does_not_cancel_against_other_jobs_gain(self):
        base, new = self.pair()
        result = analyze_source_harm(base, new)
        self.assertEqual(result['a10_jobs'], 2)
        self.assertEqual(result['a10_harmed_jobs'], 1)
        self.assertEqual(result['a10_harmed_fraction'], .5)
        self.assertEqual(result['a10_positive_wait_job_hours'], 1.)
        self.assertEqual(result['a10_positive_wait_gpu_hours'], 2.)
        self.assertEqual(result['a10_net_wait_job_hours_saved'], 1.)
        self.assertEqual(result['a10_max_wait_increase_hours'], 1.)
        self.assertEqual(result['a10_completion_harmed_jobs'], 2)
        self.assertEqual(result['a10_positive_completion_job_hours'], 2.)

    def test_zero_wait_harm_can_coexist_with_completion_harm(self):
        base, new = self.pair()
        new.loc[0, ['start_time', 'end_time']] = base.loc[0, ['start_time', 'end_time']]
        result = analyze_source_harm(base, new)
        self.assertEqual(result['a10_harmed_jobs'], 0)
        self.assertEqual(result['a10_positive_wait_job_hours'], 0.)
        self.assertEqual(result['a10_completion_harmed_jobs'], 1)

    def test_id_alignment_ignores_row_order_but_preserves_leading_zeroes(self):
        base, new = self.pair()
        self.assertEqual(analyze_source_harm(base, new), analyze_source_harm(base, new.iloc[::-1]))
        with self.assertRaisesRegex(ValueError, 'job sets'):
            analyze_source_harm(base, new.assign(job_name=['1', '2', '3']))

    def test_rejects_duplicate_ids_and_changed_original_jobs(self):
        base, new = self.pair()
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            analyze_source_harm(base, new.assign(job_name=['001', '001', '003']))
        with self.assertRaisesRegex(ValueError, 'Original job'):
            analyze_source_harm(base, new.assign(worker_num=[1, 1, 1]))


class SourceSelectionTests(unittest.TestCase):
    def table(self):
        return pd.DataFrame([
            {'configuration': name, 'phase': 'development', 'seed': seed, 'speedup': speed,
             'joint_pass': passed, 'wait_hours_mean': value}
            for name, passed, value in [('fast_fail', False, 1.), ('good_b', True, 2.), ('good_a', True, 2.)]
            for seed in DEV_SEEDS for speed in SPEEDS])

    def test_all_pairs_required_and_tie_break_predeclared(self):
        self.assertEqual(choose(self.table())['selected'], 'good_a')
        with self.assertRaisesRegex(ValueError, 'Missing or duplicate'):
            choose(self.table().iloc[:-1])
        with self.assertRaisesRegex(ValueError, 'Missing or duplicate'):
            choose(pd.concat([self.table(), self.table().iloc[:1]]))

    def test_validation_rejected_and_no_eligible_means_none(self):
        with self.assertRaisesRegex(ValueError, 'development only'):
            choose(self.table().assign(phase='validation'))
        self.assertIsNone(choose(self.table().assign(joint_pass=False))['selected'])


if __name__ == '__main__':
    unittest.main()
