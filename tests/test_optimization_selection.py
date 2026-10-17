"""Hand-calculated decision rules and management-boundary integration checks."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import run_optimization_experiments as optimization


def aging_table(rows):
    return pd.DataFrame(rows, columns=["alpha", "wait_hours_mean", "wait_hours_max", "last_completion_days"])


class AgingSelectionTests(unittest.TestCase):
    def test_both_fcfs_bounds_apply_and_equality_is_feasible(self):
        table = aging_table([
            [.01, 10, 101, 99],   # Better mean, but violates maximum wait.
            [.03, 15, 99, 101],   # Better mean, but violates last completion.
            [.1, 20, 100, 100],   # Exactly on both declared bounds.
            [.3, 25, 90, 90],
        ])
        result = optimization.select_aging(table, {"wait_hours_max": 100, "last_completion_days": 100})
        self.assertEqual(result["selected_alpha"], .1)
        self.assertEqual(result["fcfs_bounds_feasible_count"], 2)
        self.assertEqual(result["method"], "minimum_mean_subject_to_fcfs_bounds")

    def test_feasible_equal_means_choose_smaller_alpha(self):
        table = aging_table([[3, 20, 90, 90], [.3, 20, 95, 95]])
        result = optimization.select_aging(table, {"wait_hours_max": 100, "last_completion_days": 100})
        self.assertEqual(result["selected_alpha"], .3)

    def test_no_feasible_point_uses_three_objective_pareto_compromise(self):
        table = aging_table([[0, 0, 100, 100], [1, 50, 60, 60], [2, 100, 30, 30], [3, 100, 100, 100]])
        result = optimization.select_aging(table, {"wait_hours_max": 20, "last_completion_days": 20})
        self.assertEqual(result["selected_alpha"], 1)
        self.assertEqual(result["fcfs_bounds_feasible_count"], 0)
        self.assertEqual(result["method"], "pareto_normalized_ideal_distance_compromise")
        self.assertAlmostEqual(result["ideal_distance"], np.sqrt(.5**2 + 2 * (3/7)**2))

    def test_zero_range_objective_does_not_divide_by_zero(self):
        table = aging_table([[0, 1, 50, 5], [1, 3, 50, 3], [2, 5, 50, 1]])
        result = optimization.select_aging(table, {"wait_hours_max": 10, "last_completion_days": 10})
        self.assertEqual(result["selected_alpha"], 1)
        self.assertAlmostEqual(result["ideal_distance"], np.sqrt(.5))

    def test_compromise_distance_ties_choose_smaller_alpha(self):
        table = aging_table([[.3, 0, 2, 2], [.1, 2, 0, 2]])
        result = optimization.select_aging(table, {"wait_hours_max": 1, "last_completion_days": 1})
        self.assertEqual(result["selected_alpha"], .1)


class RoutingManagementSelectionTests(unittest.TestCase):
    """Run the real routing aggregation/selection with hand-written costs.

    Only costly simulations/evaluation are replaced. The tested path reads the
    per-donor paired metrics, applies management thresholds, labels sensitivity
    cases, makes both recommendations, and writes the actual selection manifest.
    """

    def setUp(self):
        (ROOT / "tmp").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / "tmp")
        self.root = Path(self.temporary.name)
        self.baseline = pd.DataFrame({"job_name": ["example"]})
        path = self.root / "outputs/question2/sjf"
        path.mkdir(parents=True)
        self.baseline.to_csv(path / "schedule.csv", index=False)

    def tearDown(self):
        self.temporary.cleanup()

    def run_routing_selection(self, cases):
        experiment = optimization.Experiment.__new__(optimization.Experiment)
        experiment.args = SimpleNamespace(save_schedules="key", force=False)
        experiment.provenance = {}
        experiment.nodes = pd.DataFrame()
        minimal_metric = {"wait_hours_mean": 1.0}

        def fake_run(directory, name, **kwargs):
            directory.mkdir(parents=True, exist_ok=True)
            if name == "aging_best":
                return minimal_metric, self.baseline
            case = cases.get(name, {})
            native_costs = case.get("native", {})
            # Unless specified, each donor fails the mean-wait constraint.
            rows = [{"requested_model": "A10", "wait_delta_mean_hours": -case.get("gain", 1), "wait_p95_delta_hours": 0}]
            for model in (optimization.A100, optimization.A800, optimization.H800, optimization.SERIES2):
                mean, p95 = native_costs.get(model, (1, 3))
                rows.append({"requested_model": model, "wait_delta_mean_hours": mean, "wait_p95_delta_hours": p95})
            pd.DataFrame(rows).to_csv(directory / "paired_wait_changes.csv", index=False)
            (directory / "scenario.json").write_text(json.dumps({"simulation_wall_seconds": 0.01}), encoding="utf-8")
            return minimal_metric, None

        experiment.run = fake_run
        with patch.object(optimization, "ROOT", self.root), \
                patch.object(optimization, "evaluate", return_value=minimal_metric), \
                patch.object(optimization, "paired_comparison"):
            experiment.routing(.3)
        directory = self.root / "outputs/question3/improved"
        selected = json.loads((directory / "selection.json").read_text(encoding="utf-8"))
        return selected, pd.read_csv(directory / "comparison.csv").set_index("scenario")

    @staticmethod
    def all_donor_costs(mean=0, p95=0):
        return {model: (mean, p95) for model in (optimization.A100, optimization.A800, optimization.H800, optimization.SERIES2)}

    def test_real_boundary_is_inclusive_and_each_donor_must_pass(self):
        accepted = self.all_donor_costs()
        accepted[optimization.A100] = (.5, 2)
        mean_failure = self.all_donor_costs()
        mean_failure[optimization.A800] = (.500001, 0)
        tail_failure = self.all_donor_costs()
        tail_failure[optimization.SERIES2] = (0, 2.000001)
        selected, table = self.run_routing_selection({
            "q3_pull_ahs": {"gain": 5, "native": accepted},
            "q3_pull_aa": {"gain": 50, "native": mean_failure},
            "q3_pull_all": {"gain": 100, "native": tail_failure},
        })
        self.assertTrue(table.loc["q3_pull_ahs", "management_feasible"])
        self.assertFalse(table.loc["q3_pull_aa", "management_feasible"])
        self.assertFalse(table.loc["q3_pull_all", "management_feasible"])
        self.assertEqual(selected["selected_scenario"], "q3_pull_ahs")
        self.assertEqual(selected["named_only_selected_scenario"], "q3_base")

    def test_no_management_feasible_scenario_returns_no_routing(self):
        selected, _ = self.run_routing_selection({})
        self.assertEqual(selected["selected_scenario"], "q3_base")
        self.assertEqual(selected["named_only_selected_scenario"], "q3_base")

    def test_speed_seed_and_bridge_cases_cannot_win_main_selection(self):
        excluded = ["q3_speed2", "q3_seed18", "q3_seed19", "q3_sjf_bridge"]
        cases = {name: {"gain": 100, "native": self.all_donor_costs()} for name in excluded}
        cases["q3_noanon"] = {"gain": 2, "native": self.all_donor_costs()}
        selected, table = self.run_routing_selection(cases)
        self.assertEqual(selected["selected_scenario"], "q3_noanon")
        self.assertTrue(table.loc[excluded, "management_feasible"].all())
        self.assertFalse(table.loc[excluded, "selection_eligible"].any())

    def test_named_only_recommendation_is_independently_selected(self):
        selected, _ = self.run_routing_selection({
            "q3_pull_ahs": {"gain": 10, "native": self.all_donor_costs()},
            "q3_noanon": {"gain": 3, "native": self.all_donor_costs()},
        })
        self.assertEqual(selected["selected_scenario"], "q3_pull_ahs")
        self.assertEqual(selected["named_only_selected_scenario"], "q3_noanon")

    def test_zero_or_negative_gain_does_not_trigger_routing(self):
        selected, table = self.run_routing_selection({
            "q3_noanon": {"gain": 0, "native": self.all_donor_costs()},
            "q3_pull_ahs": {"gain": -1, "native": self.all_donor_costs()},
        })
        self.assertTrue(table.loc[["q3_noanon", "q3_pull_ahs"], "management_feasible"].all())
        self.assertEqual(selected["selected_scenario"], "q3_base")

    def test_equal_benefit_uses_declared_name_tiebreak(self):
        selected, _ = self.run_routing_selection({
            "q3_pull_aa": {"gain": 3, "native": self.all_donor_costs()},
            "q3_noanon": {"gain": 3, "native": self.all_donor_costs()},
        })
        self.assertEqual(selected["selected_scenario"], "q3_noanon")


if __name__ == "__main__":
    unittest.main()
