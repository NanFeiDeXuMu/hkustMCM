"""Hand-calculated routing exchange, weighted compensation, and peak cases."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from analyze_routing_frontier import analyze_routing_frontier


def job(name, model, wait_hours, *, gpu=1, cpu=1, workers=1, submit=0,
        duration=3600, assigned=None):
    assigned = model if assigned is None else assigned
    start = submit + wait_hours * 3600
    return {"job_name": name, "requested_model": model, "assigned_model": assigned,
            "submit_time": submit, "start_time": start, "end_time": start + duration,
            "original_duration": duration, "run_duration": duration, "wait_time": start - submit,
            "gpu_request": gpu, "cpu_request": cpu, "worker_num": workers,
            "migrated": assigned != model}


def node_table():
    return pd.DataFrame([
        {"node_name": "a", "gpu_model": "A10", "gpu_capacity_num": 16, "cpu_num": 16},
        {"node_name": "h", "gpu_model": "H800", "gpu_capacity_num": 16, "cpu_num": 4},
        {"node_name": "d", "gpu_model": "A100", "gpu_capacity_num": 16, "cpu_num": 16},
    ])


def paired_case():
    base = pd.DataFrame([job("a1", "A10", 10, gpu=2, workers=2), job("a2", "A10", 2),
                         job("h1", "H800", 1, gpu=3), job("h2", "H800", 5),
                         job("d1", "A100", 2)])
    new = pd.DataFrame([job("a1", "A10", 3, gpu=2, workers=2), job("a2", "A10", 5),
                        job("h1", "H800", 3, gpu=3), job("h2", "H800", 4),
                        job("d1", "A100", 2)])
    return base, new


class RoutingFrontierMetricsTests(unittest.TestCase):
    def test_hand_calculated_net_savings_positive_cost_and_gpu_weights(self):
        base, new = paired_case()
        result = analyze_routing_frontier(base, new, node_table(), ("H800", "A100"))
        # Net A10 job-hours: (10-3)+(2-5)=4. Native positive cost: 2,
        # without subtracting h2's one-hour gain. S=7*4-3*1=25; C=2*3=6.
        self.assertEqual(result["a10_job_hours_saved"], 4)
        self.assertEqual(result["donor_native_job_hours_added"], 2)
        self.assertEqual(result["exchange_ratio"], 2)
        self.assertFalse(result["exchange_ratio_infinite"])
        self.assertEqual(result["a10_gpu_wait_hours_saved"], 25)
        self.assertEqual(result["compensation_gpu_hours"], 6)
        self.assertEqual(result["compensation_share"], .24)
        self.assertFalse(result["compensation_share_undefined"])
        self.assertEqual(result["a10_wait_increased_fraction"], .5)
        self.assertEqual(result["a10_max_wait_increase_hours"], 3)

    def test_per_donor_and_pooled_harm_have_different_denominators(self):
        base, new = paired_case()
        result = analyze_routing_frontier(base, new, node_table(), ("H800", "A100"))
        rows = {row["donor_model"]: row for row in result["native_harm_by_donor"]}
        self.assertEqual(result["native_wait_increased_jobs"], 1)
        self.assertEqual(result["native_wait_increased_fraction"], 1 / 3)
        self.assertEqual(result["max_donor_native_wait_increased_fraction"], .5)
        self.assertEqual(rows["H800"]["native_wait_increased_fraction"], .5)
        self.assertEqual(rows["A100"]["native_wait_increased_fraction"], 0)
        self.assertEqual(rows["H800"]["native_wait_mean_delta_hours"], .5)
        self.assertAlmostEqual(result["native_wait_mean_delta_hours"], 1 / 3)

    def test_p95_is_marginal_quantile_difference_not_quantile_of_changes(self):
        base, new = paired_case()
        result = analyze_routing_frontier(base, new, node_table(), ("H800", "A100"))
        h = result["native_harm_by_donor"][0]
        # H800 baseline P95=4.8h; new P95=3.95h. Paired deltas {2,-1}
        # have P95=1.85h, which is intentionally a different metric.
        self.assertAlmostEqual(h["native_wait_p95_delta_hours"], -.85)
        self.assertAlmostEqual(result["native_wait_p95_delta_hours"], -.8)

    def test_zero_cost_uses_null_and_explicit_infinite_marker(self):
        base = pd.DataFrame([job("a", "A10", 2), job("h", "H800", 0)])
        new = pd.DataFrame([job("a", "A10", 1), job("h", "H800", 0)])
        result = analyze_routing_frontier(base, new, node_table(), ("H800",))
        self.assertIsNone(result["exchange_ratio"])
        self.assertTrue(result["exchange_ratio_infinite"])
        self.assertEqual(result["compensation_share"], 0)
        self.assertEqual(result["a10_max_wait_increase_hours"], 0)
        self.assertNotIn("Infinity", json.dumps(result, allow_nan=False))

    def test_negative_net_benefit_and_nonpositive_weighted_savings(self):
        base = pd.DataFrame([job("a", "A10", 1, gpu=2), job("h", "H800", 0)])
        new = pd.DataFrame([job("a", "A10", 3, gpu=2), job("h", "H800", 1)])
        result = analyze_routing_frontier(base, new, node_table(), ("H800",))
        self.assertEqual(result["a10_job_hours_saved"], -2)
        self.assertEqual(result["exchange_ratio"], -2)
        self.assertEqual(result["a10_gpu_wait_hours_saved"], -4)
        self.assertIsNone(result["compensation_share"])
        self.assertTrue(result["compensation_share_undefined"])

    def test_positive_job_hour_gain_can_have_negative_gpu_weighted_gain(self):
        base = pd.DataFrame([job("a", "A10", 10), job("b", "A10", 0, gpu=8),
                             job("h", "H800", 0)])
        new = pd.DataFrame([job("a", "A10", 0), job("b", "A10", 2, gpu=8),
                            job("h", "H800", 1)])
        result = analyze_routing_frontier(base, new, node_table(), ("H800",))
        self.assertEqual(result["a10_job_hours_saved"], 8)
        self.assertEqual(result["a10_gpu_wait_hours_saved"], -6)
        self.assertIsNone(result["compensation_share"])
        self.assertTrue(result["compensation_share_undefined"])

    def test_zero_over_zero_is_explicit_and_cannot_imply_positive_gain(self):
        base = pd.DataFrame([job("a", "A10", 1), job("h", "H800", 0)])
        result = analyze_routing_frontier(base, base, node_table(), ("H800",))
        self.assertEqual(result["a10_job_hours_saved"], 0)
        self.assertIsNone(result["exchange_ratio"])
        self.assertTrue(result["exchange_ratio_infinite"])
        self.assertIsNone(result["compensation_share"])
        self.assertTrue(result["compensation_share_undefined"])

    def test_borrowing_peaks_release_before_same_time_starts_and_count_workers(self):
        specifications = [("a", 2, .1, 2, 0, 10), ("b", 1, .15, 2, 10, 10),
                          ("c", 1, 0, 1, 10, 5), ("d", 1, .25, 1, 12, 6)]
        base, new = [], []
        for name, gpu, cpu, workers, start, duration in specifications:
            kwargs = dict(gpu=gpu, cpu=cpu, workers=workers, duration=duration)
            base.append(job(name, "A10", 0, **kwargs))
            new.append(job(name, "A10", start / 3600, assigned="H800", **kwargs))
        result = analyze_routing_frontier(pd.DataFrame(base), pd.DataFrame(new), node_table(), ("H800", "A100"))
        peaks = {row["donor_model"]: row for row in result["borrowing_peaks"]}
        self.assertEqual(peaks["H800"]["migrated_jobs"], 4)
        self.assertEqual(peaks["H800"]["peak_borrowed_gpus"], 4)
        self.assertEqual(peaks["H800"]["peak_borrowed_cpus"], .55)
        self.assertEqual(peaks["A100"]["peak_borrowed_gpus"], 0)
        self.assertEqual(peaks["A100"]["peak_borrowed_cpus"], 0)
        self.assertEqual(result["donor_native_jobs"], 0)
        self.assertIsNone(result["native_wait_increased_fraction"])
        self.assertEqual(result["max_donor_native_wait_increased_fraction"], 0)

    def test_exact_id_pairing_rejects_missing_extra_and_duplicate_jobs(self):
        base, new = paired_case()
        bad = [new.iloc[:-1], pd.concat([new, new.iloc[[0]]], ignore_index=True),
               new.assign(job_name=["other", *new.job_name.iloc[1:]])]
        for changed in bad:
            with self.subTest(rows=len(changed)), self.assertRaises(ValueError):
                analyze_routing_frontier(base, changed, node_table(), ("H800", "A100"))

    def test_pairing_rejects_changed_original_attributes(self):
        base, new = paired_case()
        for field in ("gpu_request", "cpu_request", "worker_num"):
            changed = new.copy()
            changed.loc[0, field] += 1
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "Original job attributes"):
                analyze_routing_frontier(base, changed, node_table(), ("H800", "A100"))

    def test_rejects_routed_baseline_and_non_a10_migration(self):
        base, new = paired_case()
        changed_base = base.copy()
        changed_base.loc[0, ["assigned_model", "migrated"]] = ["H800", True]
        with self.assertRaisesRegex(ValueError, "baseline must have no routing"):
            analyze_routing_frontier(changed_base, new, node_table(), ("H800", "A100"))
        changed_new = new.copy()
        changed_new.loc[2, ["assigned_model", "migrated"]] = ["A100", True]
        with self.assertRaisesRegex(ValueError, "Only A10"):
            analyze_routing_frontier(base, changed_new, node_table(), ("H800", "A100"))

    def test_rejects_invalid_timing_unknown_donors_and_flag_mismatch(self):
        base, new = paired_case()
        changed = new.copy()
        changed.loc[0, "wait_time"] += 1
        with self.assertRaisesRegex(ValueError, "wait_time"):
            analyze_routing_frontier(base, changed, node_table(), ("H800",))
        changed = new.copy()
        changed.loc[0, "migrated"] = True
        with self.assertRaisesRegex(ValueError, "migrated flag"):
            analyze_routing_frontier(base, changed, node_table(), ("H800",))
        for donors in (("missing",), ("H800", "H800"), ("A10",)):
            with self.subTest(donors=donors), self.assertRaises(ValueError):
                analyze_routing_frontier(base, new, node_table(), donors)

    def test_outputs_are_json_safe_and_inputs_are_not_mutated(self):
        base, new = paired_case()
        before_base, before_new = base.copy(deep=True), new.copy(deep=True)
        test_root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(dir=test_root, prefix="_frontier_test_") as directory:
            # Keep the automatically removed test directory inside the project.
            self.assertTrue(Path(directory).resolve().is_relative_to(test_root))
            result = analyze_routing_frontier(base, new.iloc[::-1], node_table(), ("H800", "A100"), directory)
            stored = json.loads((Path(directory) / "frontier_metrics.json").read_text(encoding="utf-8"))
            self.assertEqual(stored, result)
            harm = pd.read_csv(Path(directory) / "native_harm_by_donor.csv")
            peak = pd.read_csv(Path(directory) / "borrowing_peaks.csv")
            self.assertEqual(harm.donor_model.tolist(), ["H800", "A100"])
            self.assertEqual(peak.donor_model.tolist(), ["H800", "A100"])
        pd.testing.assert_frame_equal(base, before_base)
        pd.testing.assert_frame_equal(new, before_new)


if __name__ == "__main__":
    unittest.main()
