"""Small, hand-checkable scheduler cases and independent resource assertions."""
from __future__ import annotations

from collections import defaultdict
import math
from pathlib import Path
import sys
import unittest

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from scheduler import RoutingConfig, is_flexible, run_simulation


def jobs(*records):
    return pd.DataFrame(records, columns=["job_name", "gpu_model", "gpu_request", "cpu_request",
                                          "worker_num", "submit_time", "duration"])


def nodes(*records):
    return pd.DataFrame(records, columns=["node_name", "gpu_model", "gpu_capacity_num", "cpu_num"])


def assert_schedule(test, source_jobs, source_nodes, schedule, allocation, routing=None):
    """Validate recorded allocations independently, using end-before-start events."""
    test.assertEqual(set(schedule.job_name), set(source_jobs.job_name.astype(str)))
    test.assertFalse(schedule.job_name.duplicated().any())
    by_job = schedule.set_index("job_name")
    by_node = source_nodes.assign(node_name=source_nodes.node_name.astype(str)).set_index("node_name")
    test.assertTrue((schedule.start_time >= schedule.submit_time).all())
    for row in schedule.itertuples(index=False):
        test.assertAlmostEqual(row.end_time - row.start_time, row.run_duration)
        test.assertEqual(int(allocation.loc[allocation.job_name == row.job_name, "worker_count"].sum()), row.worker_num)
    node_events = defaultdict(list)
    borrow_events = defaultdict(list)
    for allocation_row in allocation.itertuples(index=False):
        row = by_job.loc[allocation_row.job_name]
        test.assertEqual(row.assigned_model, by_node.loc[allocation_row.node_name].gpu_model)
        gpu = allocation_row.worker_count * row.gpu_request
        cpu = allocation_row.worker_count * row.cpu_request
        node_events[allocation_row.node_name].append((row.start_time, 1, gpu, cpu))
        node_events[allocation_row.node_name].append((row.end_time, 0, -gpu, -cpu))
    for node, events in node_events.items():
        gpu = cpu = 0.0
        for _, _, dg, dc in sorted(events):
            gpu += dg
            cpu += dc
            test.assertGreaterEqual(gpu, 0)
            test.assertGreaterEqual(cpu, -1e-8)
            test.assertLessEqual(gpu, by_node.loc[node].gpu_capacity_num)
            test.assertLessEqual(cpu, by_node.loc[node].cpu_num + 1e-8)
        test.assertAlmostEqual(gpu, 0)
        test.assertAlmostEqual(cpu, 0)
    if routing:
        capacity = source_nodes.groupby("gpu_model").agg(gpu=("gpu_capacity_num", "sum"), cpu=("cpu_num", "sum"))
        for row in schedule[schedule.migrated].itertuples(index=False):
            test.assertEqual(row.requested_model, "A10")
            test.assertTrue(row.flexible)
            if routing.mode == "arrival":
                test.assertEqual(row.start_time, row.submit_time)
            else:
                test.assertGreaterEqual(row.start_time, row.submit_time + routing.min_wait_seconds)
            test.assertAlmostEqual(row.run_duration, row.original_duration / routing.speedup)
            gpu = row.gpu_request * row.worker_num
            cpu = row.cpu_request * row.worker_num
            borrow_events[row.assigned_model].append((row.start_time, 1, gpu, cpu))
            borrow_events[row.assigned_model].append((row.end_time, 0, -gpu, -cpu))
        for model, events in borrow_events.items():
            cap = (dict(routing.donor_caps).get(model, routing.donor_cap_fraction)
                   if routing.mode == "pull" else routing.donor_cap_fraction)
            gpu = cpu = 0.0
            for _, _, dg, dc in sorted(events):
                gpu += dg
                cpu += dc
                test.assertLessEqual(gpu, int(cap * capacity.loc[model, "gpu"]))
                test.assertLessEqual(cpu, cap * capacity.loc[model, "cpu"] + 1e-8)


class SchedulerTests(unittest.TestCase):
    def simulate(self, job_data, node_data, policy="fcfs", routing=None, **kwargs):
        schedule, allocation, diagnostics = run_simulation(job_data, node_data, policy, routing=routing, **kwargs)
        assert_schedule(self, job_data, node_data, schedule, allocation, routing)
        return schedule.set_index("job_name"), allocation, diagnostics

    def test_fractional_zero_cpu_and_gang_placement(self):
        j = jobs(("a", "X", 1, .5, 2, 0, 5), ("b", "X", 1, 0, 2, 0, 3),
                 ("c", "X", 2, 1, 1, 3, 1))
        n = nodes(("n0", "X", 2, 1), ("n1", "X", 2, 1))
        s, a, _ = self.simulate(j, n)
        self.assertEqual(s.loc["a", "start_time"], 0)
        self.assertEqual(s.loc["b", "start_time"], 0)
        self.assertEqual(s.loc["c", "start_time"], 3)
        self.assertEqual(a.loc[a.job_name == "a", "worker_count"].tolist(), [2])

    def test_fragmented_empty_cluster_is_rejected(self):
        j = jobs(("a", "X", 2, 2, 1, 0, 1))
        n = nodes(("n0", "X", 2, 1), ("n1", "X", 1, 10))
        with self.assertRaisesRegex(ValueError, "cannot fit"):
            run_simulation(j, n)

    def test_complete_gang_waits_for_all_workers(self):
        j = jobs(("a", "X", 1, 1, 1, 0, 10), ("b", "X", 1, 1, 2, 1, 2))
        n = nodes(("n0", "X", 1, 1), ("n1", "X", 1, 1))
        s, a, _ = self.simulate(j, n)
        self.assertEqual(s.loc["b", "start_time"], 10)
        self.assertEqual(len(a[a.job_name == "b"]), 2)

    def test_simultaneous_release_and_arrival_and_no_preemption(self):
        j = jobs(("a", "X", 1, 1, 1, 0, 10), ("b", "X", 1, 1, 1, 1, 1),
                 ("c", "X", 1, 1, 1, 10, .5))
        n = nodes(("n", "X", 1, 1))
        s, _, _ = self.simulate(j, n, "sjf")
        self.assertEqual(s.loc["a", "end_time"], 10)
        self.assertEqual(s.loc["c", "start_time"], 10)
        self.assertEqual(s.loc["b", "start_time"], 10.5)

    def test_fcfs_and_sjf_head_selection(self):
        j = jobs(("a", "X", 1, 1, 1, 0, 10), ("b", "X", 2, 2, 1, 1, 20),
                 ("c", "X", 1, 1, 1, 1, 1))
        n = nodes(("n", "X", 2, 2))
        fcfs, _, _ = self.simulate(j, n, "fcfs")
        sjf, _, _ = self.simulate(j, n, "sjf")
        self.assertEqual(fcfs.loc["c", "start_time"], 30)
        self.assertEqual(sjf.loc["c", "start_time"], 1)
        self.assertEqual(sjf.loc["b", "start_time"], 10)

    def test_sjf_blocks_behind_shortest_infeasible_head(self):
        j = jobs(("a", "X", 1, 1, 1, 0, 10), ("b", "X", 2, 2, 1, 1, 1),
                 ("c", "X", 1, 1, 1, 1, 3))
        n = nodes(("n", "X", 2, 2))
        s, _, _ = self.simulate(j, n, "sjf")
        self.assertEqual(s.loc["b", "start_time"], 10)
        self.assertEqual(s.loc["c", "start_time"], 11)

    def test_backfill_preserves_head_and_allows_exact_boundary(self):
        j = jobs(("a", "X", 1, 1, 1, 0, 10), ("b", "X", 2, 2, 1, 1, 20),
                 ("c", "X", 1, 1, 1, 1, 9), ("d", "X", 1, 1, 1, 1, 10))
        n = nodes(("n", "X", 2, 2))
        s, _, stats = self.simulate(j, n, "backfill")
        self.assertEqual(s.loc["c", "start_time"], 1)
        self.assertEqual(s.loc["b", "start_time"], 10)
        self.assertEqual(s.loc["d", "start_time"], 30)
        self.assertEqual(stats["backfilled_jobs"], 1)

    def test_lookahead_counts_successors_before_feasibility_filter(self):
        j = jobs(("a", "X", 1, 1, 1, 0, 10), ("b", "X", 2, 2, 1, 1, 20),
                 ("c", "X", 1, 1, 1, 1, 15), ("d", "X", 1, 1, 1, 1, 1))
        n = nodes(("n", "X", 2, 2))
        s, _, _ = self.simulate(j, n, "backfill", max_backfill_scan=1)
        self.assertEqual(s.loc["d", "start_time"], 30)
        unrestricted, _, _ = self.simulate(j, n, "backfill", max_backfill_scan=2)
        self.assertEqual(unrestricted.loc["d", "start_time"], 1)

    def test_deterministic_input_order_independent_ties(self):
        j = jobs(("b", "X", 1, 1, 1, 0, 1), ("a", "X", 1, 1, 1, 0, 1))
        n = nodes(("z", "X", 1, 1), ("a", "X", 1, 1))
        first = run_simulation(j, n, "backfill")
        second = run_simulation(j.iloc[::-1], n.iloc[::-1], "backfill")
        pd.testing.assert_frame_equal(first[0], second[0])
        pd.testing.assert_frame_equal(first[1], second[1])

    def test_routing_fraction_zero_exactly_matches_native(self):
        j = jobs(("a", "A10", 1, 1, 1, 0, 10), ("b", "A10", 1, 1, 1, 1, 2))
        n = nodes(("a", "A10", 1, 1), ("d", "A100-SXM4-80GB", 4, 4))
        baseline = run_simulation(j, n, "backfill")
        routed = run_simulation(j, n, "backfill", routing=RoutingConfig(0, 1, 1))
        pd.testing.assert_frame_equal(baseline[0], routed[0])
        pd.testing.assert_frame_equal(baseline[1], routed[1])

    def test_routing_same_timestamp_native_priority_and_no_later_retry(self):
        j = jobs(("a", "A10", 1, 1, 1, 0, 10), ("b", "A10", 1, 1, 1, 1, 2),
                 ("z", "A100-SXM4-80GB", 1, 1, 1, 1, 2))
        n = nodes(("a", "A10", 1, 1), ("d", "A100-SXM4-80GB", 1, 1))
        s, _, _ = self.simulate(j, n, "backfill", routing=RoutingConfig(1, 1, 1))
        self.assertEqual(s.loc["z", "start_time"], 1)
        self.assertFalse(s.loc["b", "migrated"])
        self.assertEqual(s.loc["b", "start_time"], 10)

    def test_routing_immediate_start_speedup_and_double_resource_cap(self):
        j = jobs(("a", "A10", 1, 1, 1, 0, 10), ("b", "A10", 1, .75, 1, 1, 4),
                 ("c", "A10", 1, .75, 1, 1, 4), ("d", "A10", 1, .75, 1, 1, 4))
        n = nodes(("a", "A10", 1, 1), ("d", "A100-SXM4-80GB", 4, 2))
        config = RoutingConfig(1, 2, .5)
        s, _, stats = self.simulate(j, n, "backfill", routing=config)
        self.assertTrue(s.loc["b", "migrated"])
        self.assertEqual(s.loc["b", "end_time"], 3)
        # GPU cap permits two jobs, but CPU cap 1.0 forbids the second .75 job.
        self.assertFalse(s.loc["c", "migrated"])
        self.assertFalse(s.loc["d", "migrated"])
        self.assertEqual(stats["peak_borrowed_gpus"]["A100-SXM4-80GB"], 1)
        self.assertEqual(stats["peak_borrowed_cpus"]["A100-SXM4-80GB"], .75)

    def test_routing_falls_back_when_aggregate_fits_but_no_node_fits(self):
        j = jobs(("a", "A10", 2, 2, 1, 0, 10), ("b", "A10", 2, 2, 1, 1, 2))
        n = nodes(("a", "A10", 2, 2), ("d0", "A100-SXM4-80GB", 2, 1),
                  ("d1", "A100-SXM4-80GB", 1, 3))
        s, _, _ = self.simulate(j, n, "backfill", routing=RoutingConfig(1, 1, 1))
        self.assertFalse(s.loc["b", "migrated"])
        self.assertEqual(s.loc["b", "start_time"], 10)

    def test_flexible_selection_nested_and_deterministic(self):
        low = RoutingConfig(.25, 1, .25)
        high = RoutingConfig(.75, 1, .25)
        for name in map(str, range(100)):
            self.assertEqual(is_flexible(name, low), is_flexible(name, low))
            if is_flexible(name, low):
                self.assertTrue(is_flexible(name, high))

    def test_aging_zero_matches_sjf_exactly(self):
        j = jobs(("a", "X", 1, .5, 1, 0, 10), ("b", "X", 2, 1, 1, 1, 3),
                 ("c", "X", 1, 0, 1, 1, 2), ("d", "X", 1, .5, 1, 10, 1))
        n = nodes(("n", "X", 2, 1))
        sjf = run_simulation(j, n, "sjf")
        aging = run_simulation(j, n, "aging", aging_alpha=0)
        pd.testing.assert_frame_equal(sjf[0], aging[0])
        pd.testing.assert_frame_equal(sjf[1], aging[1])

    def test_aging_older_long_job_beats_newer_short_job(self):
        # old's key is 20 + 2*1 = 22; new's is 1 + 2*15 = 31.
        j = jobs(("block", "X", 1, 1, 1, 0, 100), ("old", "X", 1, 1, 1, 1, 20),
                 ("new", "X", 1, 1, 1, 15, 1))
        n = nodes(("n", "X", 1, 1))
        aging, _, _ = self.simulate(j, n, "aging", aging_alpha=2)
        sjf, _, _ = self.simulate(j, n, "sjf")
        self.assertEqual(aging.loc["old", "start_time"], 100)
        self.assertEqual(aging.loc["new", "start_time"], 120)
        self.assertEqual(sjf.loc["new", "start_time"], 100)

    def test_aging_large_alpha_fcfs_across_times_but_short_first_on_ties(self):
        j = jobs(("block", "X", 1, 1, 1, 0, 100), ("a_long", "X", 1, 1, 1, 1, 10),
                 ("z_short", "X", 1, 1, 1, 1, 2), ("later", "X", 1, 1, 1, 2, 1))
        n = nodes(("n", "X", 1, 1))
        s, _, _ = self.simulate(j, n, "aging", aging_alpha=1000)
        self.assertEqual(s.loc["z_short", "start_time"], 100)
        self.assertEqual(s.loc["a_long", "start_time"], 102)
        self.assertEqual(s.loc["later", "start_time"], 112)

    def test_aging_retains_strict_head_blocking(self):
        j = jobs(("a", "X", 1, 1, 1, 0, 10), ("b", "X", 2, 2, 1, 1, 5),
                 ("c", "X", 1, 1, 1, 5, 1))
        n = nodes(("n", "X", 2, 2))
        s, _, _ = self.simulate(j, n, "aging", aging_alpha=2)
        self.assertEqual(s.loc["b", "start_time"], 10)
        self.assertEqual(s.loc["c", "start_time"], 15)

    def test_aging_rejects_negative_or_nonfinite_alpha(self):
        j = jobs(("a", "X", 1, 1, 1, 0, 1))
        n = nodes(("n", "X", 1, 1))
        for alpha in (-1, float("inf"), float("nan")):
            with self.subTest(alpha=alpha), self.assertRaisesRegex(ValueError, "aging_alpha"):
                run_simulation(j, n, "aging", aging_alpha=alpha)

    def test_arrival_ignores_pull_only_fields(self):
        j = jobs(("a", "A10", 1, 1, 1, 0, 10), ("b", "A10", 1, 1, 1, 1, 2))
        n = nodes(("a", "A10", 1, 1), ("d", "A100-SXM4-80GB", 4, 4))
        baseline = run_simulation(j, n, "sjf", routing=RoutingConfig(1, 1, .5))
        ignored = RoutingConfig(1, 1, .5, donor_caps=(("A100-SXM4-80GB", 0),),
                                min_wait_seconds=1000, pull_scan_limit=0,
                                max_routed_duration_seconds=-1, donor_reserve_fraction=2)
        changed = run_simulation(j, n, "sjf", routing=ignored)
        pd.testing.assert_frame_equal(baseline[0], changed[0])
        pd.testing.assert_frame_equal(baseline[1], changed[1])
        self.assertTrue(changed[0].set_index("job_name").loc["b", "migrated"])

    def test_pull_revisits_waiting_job_after_donor_finishes(self):
        j = jobs(("a", "A10", 1, 1, 1, 0, 100), ("b", "A10", 1, 1, 1, 1, 4),
                 ("native", "H800", 1, 1, 1, 0, 10))
        n = nodes(("a", "A10", 1, 1), ("d", "H800", 1, 1))
        cfg = RoutingConfig(1, 2, 1, donors=("H800",), mode="pull")
        s, _, stats = self.simulate(j, n, "aging", routing=cfg, aging_alpha=1)
        self.assertTrue(s.loc["b", "migrated"])
        self.assertEqual(s.loc["b", "start_time"], 10)
        self.assertEqual(s.loc["b", "end_time"], 12)
        self.assertEqual(stats["migrated_jobs"], 1)

    def test_pull_minimum_wait_wakeup_without_external_event(self):
        j = jobs(("a", "A10", 1, 1, 1, 0, 100), ("b", "A10", 1, 1, 1, 1, 4))
        n = nodes(("a", "A10", 1, 1), ("d", "GPU-series-2", 1, 1))
        cfg = RoutingConfig(1, 1, 1, donors=("GPU-series-2",), mode="pull", min_wait_seconds=5)
        s, _, stats = self.simulate(j, n, "aging", routing=cfg, aging_alpha=1)
        self.assertEqual(s.loc["b", "start_time"], 6)
        self.assertEqual(stats["eligibility_wakeups"], 1)
        self.assertTrue(s.loc["b", "migrated"])

    def test_pull_stale_wakeup_cannot_restart_a_native_job(self):
        j = jobs(("a", "A10", 1, 1, 1, 0, 5), ("b", "A10", 1, 1, 1, 1, 2))
        n = nodes(("a", "A10", 1, 1), ("d", "H800", 1, 1))
        cfg = RoutingConfig(1, 1, 1, donors=("H800",), mode="pull", min_wait_seconds=100)
        s, _, stats = self.simulate(j, n, "sjf", routing=cfg)
        self.assertEqual(s.loc["b", "start_time"], 5)
        self.assertFalse(s.loc["b", "migrated"])
        self.assertEqual(stats["simulated_until"], 7)
        self.assertEqual(stats["event_count"], 4)
        self.assertEqual(stats["eligibility_wakeups"], 0)

    def test_pull_native_waiting_queue_prevents_transfer_even_with_space(self):
        j = jobs(("a", "A10", 1, 1, 1, 0, 100), ("b", "A10", 1, 1, 1, 1, 4),
                 ("native_run", "H800", 1, 1, 1, 0, 10),
                 ("native_head", "H800", 2, 2, 1, 1, 5))
        n = nodes(("a", "A10", 1, 1), ("d", "H800", 2, 2))
        cfg = RoutingConfig(1, 1, 1, donors=("H800",), mode="pull")
        s, _, _ = self.simulate(j, n, "fcfs", routing=cfg)
        self.assertEqual(s.loc["native_head", "start_time"], 10)
        self.assertEqual(s.loc["b", "start_time"], 15)

    def test_pull_per_donor_caps_and_cpu_limit(self):
        j = jobs(("a", "A10", 1, 1, 1, 0, 100), ("b", "A10", 1, .75, 1, 1, 5),
                 ("c", "A10", 1, .75, 1, 1, 5))
        n = nodes(("a", "A10", 1, 1), ("d0", "A800-SXM4-80GB", 4, 4),
                  ("d1", "H800", 4, 2))
        cfg = RoutingConfig(1, 1, 1, donors=("A800-SXM4-80GB", "H800"), mode="pull",
                            donor_caps=(("A800-SXM4-80GB", 0), ("H800", .5)))
        s, _, stats = self.simulate(j, n, "sjf", routing=cfg)
        self.assertEqual(s.loc["b", "assigned_model"], "H800")
        self.assertEqual(s.loc["b", "start_time"], 1)
        self.assertEqual(s.loc["c", "start_time"], 6)
        self.assertEqual(stats["peak_borrowed_gpus"]["H800"], 1)
        self.assertEqual(stats["peak_borrowed_cpus"]["H800"], .75)
        self.assertEqual(stats["peak_borrowed_gpus"]["A800-SXM4-80GB"], 0)

    def test_pull_oldest_first_and_bounded_prefix(self):
        # The first candidate needs two GPUs, exceeding the donor cap of one.
        # L=1 cannot skip it to consider the next, feasible one-GPU job.
        j = jobs(("a", "A10", 2, 2, 1, 0, 100), ("b", "A10", 2, 2, 1, 1, 5),
                 ("c", "A10", 1, 1, 1, 2, 1))
        n = nodes(("a", "A10", 2, 2), ("d", "H800", 2, 2))
        limited = RoutingConfig(1, 1, .5, donors=("H800",), mode="pull", pull_scan_limit=1)
        s, _, _ = self.simulate(j, n, "fcfs", routing=limited)
        self.assertEqual(s.loc["c", "start_time"], 100)
        wider = RoutingConfig(1, 1, .5, donors=("H800",), mode="pull", pull_scan_limit=2)
        other, _, _ = self.simulate(j, n, "fcfs", routing=wider)
        self.assertEqual(other.loc["c", "start_time"], 2)

    def test_pull_zero_cpu_job_can_use_remaining_gpu_at_cpu_borrow_limit(self):
        j = jobs(("a", "A10", 1, 1, 1, 0, 100), ("b", "A10", 1, 1, 1, 1, 10),
                 ("c", "A10", 1, 0, 1, 2, 1))
        n = nodes(("a", "A10", 1, 1), ("d", "H800", 4, 2))
        cfg = RoutingConfig(1, 1, .5, donors=("H800",), mode="pull")
        s, _, stats = self.simulate(j, n, "fcfs", routing=cfg)
        self.assertEqual(s.loc["b", "start_time"], 1)
        self.assertEqual(s.loc["c", "start_time"], 2)
        self.assertEqual(stats["peak_borrowed_gpus"]["H800"], 2)
        self.assertEqual(stats["peak_borrowed_cpus"]["H800"], 1)

    def test_pull_rejects_invalid_mode_specific_configuration(self):
        for kwargs in ({"min_wait_seconds": -1}, {"min_wait_seconds": float("nan")},
                       {"pull_scan_limit": -1}, {"pull_scan_limit": 1.5},
                       {"donor_caps": (("H800", 1.1),)},
                       {"donor_caps": (("H800", .1), ("H800", .2))},
                       {"max_routed_duration_seconds": -1},
                       {"max_routed_duration_seconds": float("inf")},
                       {"max_routed_duration_seconds": float("nan")},
                       {"donor_reserve_fraction": -.1},
                       {"donor_reserve_fraction": 1.1},
                       {"donor_reserve_fraction": float("nan")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                RoutingConfig(mode="pull", **kwargs)

    def test_log_aging_large_time_scale_matches_sjf_on_small_case(self):
        j = jobs(("block", "X", 1, 1, 1, 0, 100), ("old", "X", 1, 1, 1, 1, 10),
                 ("new", "X", 1, 1, 1, 5, 1))
        n = nodes(("n", "X", 1, 1))
        reference = run_simulation(j, n, "sjf")
        changed = run_simulation(j, n, "log_aging", log_aging_tau_seconds=1e30)
        pd.testing.assert_frame_equal(reference[0], changed[0])
        pd.testing.assert_frame_equal(reference[1], changed[1])

    def test_log_aging_small_time_scale_prefers_old_arrivals(self):
        j = jobs(("block", "X", 1, 1, 1, 0, 100), ("a_long", "X", 1, 1, 1, 1, 10),
                 ("z_short", "X", 1, 1, 1, 1, 2), ("later", "X", 1, 1, 1, 2, 1))
        n = nodes(("n", "X", 1, 1))
        s, _, _ = self.simulate(j, n, "log_aging", log_aging_tau_seconds=.001)
        self.assertEqual(s.loc["z_short", "start_time"], 100)
        self.assertEqual(s.loc["a_long", "start_time"], 102)
        self.assertEqual(s.loc["later", "start_time"], 112)

    def test_log_aging_hand_calculated_arrival_gap_crossover(self):
        # At T=10 seconds, an old 100-second job beats a later 1-second
        # arrival iff their arrival gap exceeds 10*ln(100) ~= 46.0517.
        n = nodes(("n", "X", 1, 1))
        gap = 10 * math.log(100)
        for adjustment, first in ((-1, "short"), (1, "long")):
            with self.subTest(adjustment=adjustment):
                j = jobs(("block", "X", 1, 1, 1, 0, 100),
                         ("long", "X", 1, 1, 1, 1, 100),
                         ("short", "X", 1, 1, 1, 1 + gap + adjustment, 1))
                s, _, _ = self.simulate(j, n, "log_aging", log_aging_tau_seconds=10)
                self.assertEqual(s.loc[first, "start_time"], 100)

    def test_log_aging_strict_infeasible_head_blocks_feasible_successor(self):
        j = jobs(("run", "X", 1, 1, 1, 0, 10), ("head", "X", 2, 2, 1, 1, 5),
                 ("later", "X", 1, 1, 1, 5, 1))
        n = nodes(("n", "X", 2, 2))
        s, _, _ = self.simulate(j, n, "log_aging", log_aging_tau_seconds=1)
        self.assertEqual(s.loc["head", "start_time"], 10)
        self.assertEqual(s.loc["later", "start_time"], 15)

    def test_log_time_scale_validation_applies_only_to_log_policy(self):
        j = jobs(("a", "X", 1, 1, 1, 0, 1))
        n = nodes(("n", "X", 1, 1))
        baseline = run_simulation(j, n, "sjf")
        for tau in (0, -1, float("inf"), float("nan")):
            with self.subTest(tau=tau):
                with self.assertRaisesRegex(ValueError, "log_aging_tau_seconds"):
                    run_simulation(j, n, "log_aging", log_aging_tau_seconds=tau)
                ignored = run_simulation(j, n, "sjf", log_aging_tau_seconds=tau)
                pd.testing.assert_frame_equal(baseline[0], ignored[0])
                pd.testing.assert_frame_equal(baseline[1], ignored[1])

    def test_pull_duration_filter_does_not_fill_bounded_scan_with_long_jobs(self):
        records = [("block", "A10", 1, 1, 1, 0, 10000)]
        records += [(f"long_{i:03d}", "A10", 1, 1, 1, 1, 100) for i in range(260)]
        records += [("short", "A10", 1, 1, 1, 2, 10)]
        j = jobs(*records)
        n = nodes(("a", "A10", 1, 1), ("d", "H800", 1, 1))
        cfg = RoutingConfig(1, 1, 1, donors=("H800",), mode="pull",
                            max_routed_duration_seconds=10, pull_scan_limit=256)
        s, _, stats = self.simulate(j, n, "sjf", routing=cfg)
        self.assertTrue(s.loc["short", "migrated"])
        self.assertEqual(s.loc["short", "start_time"], 2)
        long = s.loc[s.index.str.startswith("long_")]
        self.assertTrue(long.flexible.all())
        self.assertFalse(long.migrated.any())
        self.assertEqual(stats["migrated_jobs"], 1)

    def test_pull_duration_ceiling_uses_original_duration_and_accepts_equality(self):
        j = jobs(("block", "A10", 1, 1, 1, 0, 100), ("long", "A10", 1, 1, 1, 1, 20),
                 ("boundary", "A10", 1, 1, 1, 2, 10))
        n = nodes(("a", "A10", 1, 1), ("d", "H800", 1, 1))
        cfg = RoutingConfig(1, 2, 1, donors=("H800",), mode="pull", max_routed_duration_seconds=10)
        s, _, _ = self.simulate(j, n, "sjf", routing=cfg)
        self.assertTrue(s.loc["long", "flexible"])
        self.assertFalse(s.loc["long", "migrated"])
        self.assertTrue(s.loc["boundary", "migrated"])
        self.assertEqual(s.loc["boundary", "start_time"], 2)
        self.assertEqual(s.loc["boundary", "run_duration"], 5)

    def test_pull_gpu_reserve_boundary_and_recheck_after_each_start(self):
        j = jobs(("block", "A10", 2, 2, 1, 0, 100), ("b", "A10", 1, 1, 1, 1, 5),
                 ("c", "A10", 1, 1, 1, 1, 5), ("native", "H800", 1, 1, 1, 0, 50))
        n = nodes(("a", "A10", 2, 2), ("d", "H800", 4, 8))
        cfg = RoutingConfig(1, 1, 1, donors=("H800",), mode="pull", donor_reserve_fraction=.5)
        s, _, _ = self.simulate(j, n, routing=cfg)
        # Native plus b leave exactly two of four GPUs. c must wait even
        # though physical placement and borrowing cap both permit it at t=1.
        self.assertEqual(s.loc["b", "start_time"], 1)
        self.assertEqual(s.loc["c", "start_time"], 6)

    def test_pull_cpu_reserve_boundary_blocks_cpu_only_violation(self):
        j = jobs(("block", "A10", 2, 2, 1, 0, 100), ("b", "A10", 1, 1, 1, 1, 5),
                 ("c", "A10", 1, .5, 1, 1, 5), ("native", "H800", 1, 1, 1, 0, 50))
        n = nodes(("a", "A10", 2, 2), ("d", "H800", 8, 4))
        cfg = RoutingConfig(1, 1, 1, donors=("H800",), mode="pull", donor_reserve_fraction=.5)
        s, _, _ = self.simulate(j, n, routing=cfg)
        # b leaves exactly two of four CPUs; c would leave enough GPUs but
        # only 1.5 CPUs, so it can start only after b completes.
        self.assertEqual(s.loc["b", "start_time"], 1)
        self.assertEqual(s.loc["c", "start_time"], 6)

    def test_pull_default_harm_controls_equal_omitted_arguments(self):
        j = jobs(("a", "A10", 1, 1, 1, 0, 100), ("b", "A10", 1, .5, 1, 1, 4),
                 ("native", "H800", 1, 1, 1, 0, 10))
        n = nodes(("a", "A10", 1, 1), ("d", "H800", 1, 1))
        kwargs = dict(flexible_fraction=1, donors=("H800",), donor_cap_fraction=1, mode="pull")
        omitted = run_simulation(j, n, "aging", routing=RoutingConfig(**kwargs), aging_alpha=1)
        explicit = run_simulation(j, n, "aging", routing=RoutingConfig(
            **kwargs, max_routed_duration_seconds=None, donor_reserve_fraction=0), aging_alpha=1)
        pd.testing.assert_frame_equal(omitted[0], explicit[0])
        pd.testing.assert_frame_equal(omitted[1], explicit[1])


if __name__ == "__main__":
    unittest.main()
