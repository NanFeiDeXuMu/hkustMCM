"""Hand-computed evaluation cases independent of the scheduler."""
from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from evaluate_scheduling import HORIZON, evaluate, gpu_queue_idle_metrics, paired_comparison

TEST_TEMP = Path(__file__).resolve().parents[1] / "tmp/test_metrics"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


class MetricsTests(unittest.TestCase):
    def test_horizon_boundary_and_all_job_wait(self):
        jobs = pd.DataFrame([
            dict(job_name="a",requested_model="A10",assigned_model="A10",submit_time=0,start_time=0,end_time=HORIZON,
                 original_duration=HORIZON,run_duration=HORIZON,gpu_request=1,cpu_request=.5,worker_num=1),
            dict(job_name="b",requested_model="A10",assigned_model="A10",submit_time=1,start_time=HORIZON,end_time=HORIZON+10,
                 original_duration=10,run_duration=10,gpu_request=1,cpu_request=.5,worker_num=1),
        ])
        nodes = pd.DataFrame([dict(gpu_model="A10",gpu_capacity_num=1,cpu_num=1)])
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temporary:
            result = evaluate(jobs,nodes,Path(temporary))
        self.assertEqual(result["completed_at_90d"],1)
        self.assertEqual(result["running_at_90d"],1)
        self.assertEqual(result["waiting_at_90d"],0)
        self.assertAlmostEqual(result["gpu_allocation_rate_90d"],1)
        self.assertAlmostEqual(result["cpu_allocation_rate_90d"],.5)
        self.assertAlmostEqual(result["wait_hours_mean"],(HORIZON-1)/7200)
        self.assertAlmostEqual(result["a10_queue_nonempty_hours_90d"], (HORIZON-1)/3600)
        self.assertEqual(result["gpu_idle_while_native_queued_hours_90d"], 0)

    def test_pairing_uses_identifiers(self):
        base = pd.DataFrame([dict(job_name="a",requested_model="A10",submit_time=0,start_time=7200,end_time=7201),
                             dict(job_name="b",requested_model="A100-SXM4-80GB",submit_time=0,start_time=0,end_time=1)])
        new = pd.DataFrame([dict(job_name="b",requested_model="A100-SXM4-80GB",submit_time=0,start_time=3600,end_time=3601),
                            dict(job_name="a",requested_model="A10",submit_time=0,start_time=0,end_time=1)])
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temporary:
            result = paired_comparison(base,new,Path(temporary)).set_index("requested_model")
        self.assertEqual(result.loc["A10","wait_delta_mean_hours"],-2)
        self.assertEqual(result.loc["A100-SXM4-80GB","max_wait_increase_hours"],1)
        self.assertEqual(result.loc["ALL","wait_increased_fraction"],.5)

    @staticmethod
    def idle_timeline():
        """Six pools, with manually chosen events at hours 0,...,6.

        A10 queues: [1,2):1 GPU; [2,3):2; [3,4):1.
        Fixed-D idle during those intervals: 4, 3, 3 GPUs.
        Only A100 has its own queue while idle: 1 idle GPU for [1,2).
        """
        hour = 3600
        def job(name, requested, assigned, submit, start, end, workers=1):
            return dict(job_name=name, requested_model=requested, assigned_model=assigned,
                        submit_time=submit*hour, start_time=start*hour, end_time=end*hour,
                        gpu_request=1, worker_num=workers)
        jobs = pd.DataFrame([
            job("native_a10", "A10", "A10", 0, 0, 4, workers=2),
            job("migrant", "A10", "A100-SXM4-80GB", 1, 3, 5),
            job("queued_a10", "A10", "A10", 2, 4, 6),
            job("donor_running", "A100-SXM4-80GB", "A100-SXM4-80GB", 0, 0, 2),
            job("donor_queued", "A100-SXM4-80GB", "A100-SXM4-80GB", 1, 2, 4),
            job("h800_running", "H800", "H800", 2, 2, 3),
        ])
        nodes = pd.DataFrame([
            dict(gpu_model=model, gpu_capacity_num=capacity)
            for model, capacity in (("A10", 2), ("A100-SXM4-80GB", 2),
                                    ("A800-SXM4-80GB", 1), ("H800", 1),
                                    ("GPU-series-2", 1), ("GPU-series-1", 1))
        ])
        return jobs, nodes

    def test_exact_queue_idle_stranding_and_allocation_gaps(self):
        jobs, nodes = self.idle_timeline()
        metrics, table = gpu_queue_idle_metrics(jobs, nodes, {"window": 4*3600, "full": 6*3600})
        table = table.set_index("gpu_model")
        # Migrant waits in the requested A10 queue, not the assigned A100 queue.
        for suffix in ("window", "full"):
            self.assertEqual(metrics[f"a10_queue_nonempty_hours_{suffix}"], 3)
            self.assertEqual(metrics[f"gpu_stranded_upper_bound_hours_{suffix}"], 4)
            self.assertEqual(metrics[f"donor_idle_while_a10_queued_gpu_hours_{suffix}"], 10)
            self.assertEqual(metrics[f"gpu_idle_while_native_queued_hours_{suffix}"], 1)
            self.assertEqual(table.loc["A100-SXM4-80GB", f"own_queue_nonempty_hours_{suffix}"], 1)
            self.assertEqual(table.loc["A100-SXM4-80GB", f"own_queue_idle_gpu_hours_{suffix}"], 1)
            self.assertEqual(table.loc["A100-SXM4-80GB", f"own_queue_idle_fraction_{suffix}"], .5)
            self.assertEqual(table.loc["A100-SXM4-80GB", f"a10_queue_idle_gpu_hours_{suffix}"], 2)
            self.assertAlmostEqual(table.loc["A100-SXM4-80GB", f"a10_queue_idle_fraction_{suffix}"], 1/3)
        self.assertEqual(table.loc["A100-SXM4-80GB", "own_queue_nonempty_fraction_window"], .25)
        self.assertEqual(table.loc["A100-SXM4-80GB", "own_queue_idle_capacity_time_fraction_window"], 1/8)
        self.assertAlmostEqual(table.loc["A100-SXM4-80GB", "own_queue_idle_capacity_time_fraction_full"], 1/12)
        # Four fixed donor pools have capacity 5; series-1 is never a donor.
        self.assertEqual(metrics["gpu_allocation_rate_range_window"], 1)
        self.assertAlmostEqual(metrics["donor_gpu_allocation_rate_window"], 6/20)
        self.assertAlmostEqual(metrics["a10_minus_donor_gpu_allocation_rate_window"], .7)
        self.assertAlmostEqual(metrics["gpu_allocation_rate_range_full"], 5/6)
        self.assertAlmostEqual(metrics["donor_gpu_allocation_rate_full"], 7/30)
        self.assertAlmostEqual(metrics["a10_minus_donor_gpu_allocation_rate_full"], .6)
        self.assertFalse(table.loc["GPU-series-1", "in_fixed_donor_set"])
        self.assertEqual(table.loc["GPU-series-1", "a10_queue_idle_gpu_hours_window"], 3)

    def test_window_clips_an_event_interval_without_sampling(self):
        jobs, nodes = self.idle_timeline()
        metrics, table = gpu_queue_idle_metrics(jobs, nodes, {"cut": 2.5*3600})
        table = table.set_index("gpu_model")
        # [1,2) contributes 1 stranded GPU-hour, [2,2.5) contributes 1.
        self.assertEqual(metrics["gpu_stranded_upper_bound_hours_cut"], 2)
        self.assertEqual(metrics["donor_idle_while_a10_queued_gpu_hours_cut"], 5.5)
        self.assertEqual(metrics["a10_queue_nonempty_hours_cut"], 1.5)
        self.assertAlmostEqual(metrics["donor_gpu_allocation_rate_cut"], 3/12.5)
        self.assertAlmostEqual(metrics["a10_minus_donor_gpu_allocation_rate_cut"], .76)
        self.assertEqual(table.loc["A100-SXM4-80GB", "own_queue_idle_gpu_hours_cut"], 1)

    def test_stranding_is_limited_by_idle_capacity_not_queued_gpu_sum(self):
        jobs = pd.DataFrame([
            dict(requested_model="A10", assigned_model="A10", submit_time=0, start_time=0,
                 end_time=4*3600, gpu_request=1, worker_num=2),
            dict(requested_model="A10", assigned_model="A10", submit_time=3600, start_time=4*3600,
                 end_time=5*3600, gpu_request=1, worker_num=2),
        ])
        nodes = pd.DataFrame([dict(gpu_model="A10", gpu_capacity_num=2),
                              dict(gpu_model="H800", gpu_capacity_num=1)])
        metrics, _ = gpu_queue_idle_metrics(jobs, nodes, {"full": 5*3600})
        # Two GPUs queue for three hours, but only one donor GPU is idle.
        self.assertEqual(metrics["gpu_stranded_upper_bound_hours_full"], 3)
        self.assertEqual(metrics["donor_idle_while_a10_queued_gpu_hours_full"], 3)
        self.assertEqual(metrics["gpu_idle_while_native_queued_hours_full"], 0)

    def test_zero_wait_migration_has_no_queue_and_gap_can_be_negative(self):
        jobs = pd.DataFrame([dict(requested_model="A10", assigned_model="A100-SXM4-80GB",
                                 submit_time=.25, start_time=.25, end_time=1.25,
                                 gpu_request=1, worker_num=1)])
        nodes = pd.DataFrame([dict(gpu_model="A10", gpu_capacity_num=1),
                              dict(gpu_model="A100-SXM4-80GB", gpu_capacity_num=1)])
        metrics, table = gpu_queue_idle_metrics(jobs, nodes, {"cut": 1, "full": 1.25})
        self.assertEqual(metrics["a10_queue_nonempty_hours_cut"], 0)
        self.assertEqual(metrics["gpu_stranded_upper_bound_hours_cut"], 0)
        self.assertEqual(metrics["donor_idle_while_a10_queued_gpu_hours_full"], 0)
        self.assertEqual(metrics["gpu_allocation_rate_range_cut"], .75)
        self.assertEqual(metrics["a10_minus_donor_gpu_allocation_rate_cut"], -.75)
        self.assertEqual(metrics["a10_minus_donor_gpu_allocation_rate_full"], -.8)
        self.assertTrue(table.own_queue_idle_fraction_cut.isna().all())
        self.assertTrue(table.a10_queue_idle_fraction_cut.isna().all())

    def test_native_quantile_changes_are_not_quantiles_of_paired_delays(self):
        model = "A100-SXM4-80GB"
        base = pd.DataFrame([dict(job_name=name, requested_model=model, submit_time=0,
                                  start_time=wait*3600, end_time=wait*3600+1)
                             for name, wait in (("a", 0), ("b", 4), ("c", 10))])
        changed = pd.DataFrame([dict(job_name=name, requested_model=model, submit_time=0,
                                     start_time=wait*3600, end_time=wait*3600+1)
                                for name, wait in (("c", 8), ("a", 0), ("b", 8))])
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temporary:
            result = paired_comparison(base, changed, Path(temporary)).set_index("requested_model")
        row = result.loc[model]
        self.assertAlmostEqual(row.wait_mean_before_hours, 14/3)
        self.assertAlmostEqual(row.wait_mean_after_hours, 16/3)
        self.assertAlmostEqual(row.wait_delta_mean_hours, 2/3)
        self.assertAlmostEqual(row.wait_p95_before_hours, 9.4)
        self.assertEqual(row.wait_p95_after_hours, 8)
        self.assertAlmostEqual(row.wait_p95_delta_hours, -1.4)
        self.assertEqual(row.wait_max_before_hours, 10)
        self.assertEqual(row.wait_max_after_hours, 8)
        self.assertEqual(row.wait_max_delta_hours, -2)
        self.assertEqual(row.positive_wait_delta_p95_hours, 4)
        self.assertEqual(row.max_wait_increase_hours, 4)


if __name__ == "__main__":
    unittest.main()
