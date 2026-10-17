"""Hand-calculated edge cases for accounting used in Question 1."""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from analyze_question1 import DAY, concentration, empty_cluster_slots, gini, offered_load


class QuestionOneAccountingTests(unittest.TestCase):
    def test_empty_pool_respects_whole_workers_cpu_and_zero_cpu(self):
        nodes = pd.DataFrame({"gpu_model": ["A", "A"], "gpu_capacity_num": [2, 2], "cpu_num": [1.5, 1.5]})
        jobs = pd.DataFrame({"gpu_model": ["A"] * 3, "gpu_request": [3, 1, 1], "cpu_request": [0, .75, 0]})
        # Four GPUs in aggregate cannot accommodate even one three-GPU worker.
        np.testing.assert_array_equal(empty_cluster_slots(jobs, nodes), [0, 4, 4])

    def test_half_open_events_window_clip_and_tail(self):
        jobs = pd.DataFrame({"submit_time": [0., DAY / 2], "duration": [2 * DAY, DAY / 2],
                             "job_gpu": [2., 1.], "job_cpu": [1., 3.]})
        daily, stats = offered_load(jobs, gpu_capacity=2, cpu_capacity=2, days=1)
        self.assertAlmostEqual(daily.gpu_hours.iloc[0], 60.)
        self.assertAlmostEqual(stats["gpu_work_after_window_hours"], 48.)
        self.assertAlmostEqual(stats["peak_gpu_demand_to_capacity"], 1.5)
        self.assertAlmostEqual(stats["fraction_window_gpu_over_capacity"], .5)
        self.assertAlmostEqual(stats["peak_cpu_demand_to_capacity"], 2.)
        self.assertEqual(stats["jobs_finishing_after_window_if_immediate"], 1)

    def test_simultaneous_end_and_start_do_not_double_count(self):
        jobs = pd.DataFrame({"submit_time": [0., DAY / 2], "duration": [DAY / 2, DAY / 2],
                             "job_gpu": [1., 1.], "job_cpu": [0., 0.]})
        daily, stats = offered_load(jobs, gpu_capacity=1, cpu_capacity=1, days=1)
        self.assertAlmostEqual(daily.gpu_hours.iloc[0], 24.)
        self.assertAlmostEqual(stats["peak_gpu_demand_to_capacity"], 1.)
        self.assertAlmostEqual(stats["fraction_window_gpu_over_capacity"], 0.)
        self.assertAlmostEqual(stats["cpu_work_in_window_hours"], 0.)
        self.assertEqual(stats["jobs_finishing_after_window_if_immediate"], 0)

    def test_concentration_uses_ceiling_and_work_ranking(self):
        jobs = pd.DataFrame({"gpu_model": ["A"] * 101, "gpu_work_seconds": [100.] + [1.] * 100})
        table = concentration(jobs)
        row = table[(table.gpu_model == "ALL") & (table.top_fraction == .01)].iloc[0]
        self.assertEqual(row.jobs_selected, 2)
        self.assertAlmostEqual(row.gpu_work_share, 101 / 200)
        self.assertAlmostEqual(gini(np.array([1., 1., 1.])), 0.)
        self.assertAlmostEqual(gini(np.array([0., 0., 3.])), 2 / 3)


if __name__ == "__main__":
    unittest.main()
