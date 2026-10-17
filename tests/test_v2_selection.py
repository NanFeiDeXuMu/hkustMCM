"""Independent hand-calculated tests of D023/D024 selection and pairing."""
from contextlib import redirect_stdout
import io
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
import run_v2_experiments as runner


def q2_row(name, mean=1, maximum=10, groups=(1, 2, 3, 4)):
    return {"scenario": name, "wait_hours_mean": mean, "wait_hours_max": maximum,
            **{f"group_{key}_mean_wait_hours": value for (key, _), value in zip(runner.GROUPS, groups)}}


FCFS_GROUPS = {label: value for (_, label), value in zip(runner.GROUPS, (1, 2, 3, 4))}


def q3_row(name, gain=1, *, anonymous=False, feasible=True, eligible=True):
    return {"scenario": name, "a10_mean_wait_gain_hours": gain, "includes_anonymous_donor": anonymous,
            "management_feasible": feasible, "selection_eligible": eligible}


class Q2SelectionV2Tests(unittest.TestCase):
    def test_every_duration_group_must_pass_individually(self):
        rows = [q2_row("valid", maximum=100)]
        for index in range(4):
            groups = [0, 0, 0, 0]
            groups[index] = index + 1.001
            rows.append(q2_row(f"violates_group_{index}", maximum=1, groups=groups))
        result = runner.select_q2(pd.DataFrame(rows), FCFS_GROUPS, sjf_mean=1)
        self.assertEqual(result["selected_scenario"], "valid")
        self.assertEqual(result["feasible_candidates"], ["valid"])
        self.assertTrue(result["c1_c2_checks"][0]["c1_passed"])
        self.assertTrue(all(not row["c1_passed"] for row in result["c1_c2_checks"][1:]))

    def test_c2_uses_unrounded_sjf_bound_with_equality(self):
        sjf_mean = 16.56762345
        exact_bound = 2 * sjf_mean
        table = pd.DataFrame([q2_row("at_exact_bound", exact_bound, 10),
                              q2_row("just_above", np.nextafter(exact_bound, np.inf), 1)])
        result = runner.select_q2(table, FCFS_GROUPS, sjf_mean)
        self.assertEqual(result["selected_scenario"], "at_exact_bound")
        self.assertEqual(result["c2_mean_bound_hours"], exact_bound)
        self.assertEqual([row["c2_passed"] for row in result["c1_c2_checks"]], [True, False])

    def test_objective_order_is_maximum_then_mean_then_name(self):
        table = pd.DataFrame([q2_row("zeta", 8, 9), q2_row("first_name", 10, 9),
                              q2_row("low_mean_wrong_max", 1, 10), q2_row("alpha", 8, 9)])
        result = runner.select_q2(table, FCFS_GROUPS, sjf_mean=10)
        self.assertEqual(result["selected_scenario"], "alpha")
        self.assertFalse(result["fallback"])

    def test_no_feasible_candidate_falls_back_to_sjf_and_reports_failure(self):
        table = pd.DataFrame([q2_row("sjf", groups=(2, 2, 3, 4)), q2_row("other", mean=3)])
        result = runner.select_q2(table, FCFS_GROUPS, sjf_mean=1)
        self.assertEqual(result["selected_scenario"], "sjf")
        self.assertTrue(result["fallback"])
        self.assertEqual(result["feasible_candidates"], [])


class Q3SelectionV2Tests(unittest.TestCase):
    def test_named_primary_and_anonymous_conditional_are_selected_separately(self):
        table = pd.DataFrame([q3_row("named", 2), q3_row("anonymous", 100, anonymous=True)])
        selected = runner.select_q3(table)
        self.assertEqual(selected["primary_selected_scenario"], "named")
        self.assertEqual(selected["conditional_selected_scenario"], "anonymous")

    def test_anonymous_winner_never_replaces_missing_named_recommendation(self):
        table = pd.DataFrame([q3_row("bad_named", 2, feasible=False), q3_row("anonymous", 100, anonymous=True)])
        selected = runner.select_q3(table)
        self.assertEqual(selected["primary_selected_scenario"], "v2_base")
        self.assertEqual(selected["conditional_selected_scenario"], "anonymous")

    def test_sensitivities_are_excluded_even_when_their_gain_is_best(self):
        table = pd.DataFrame([q3_row("main_named", 2), q3_row("main_anonymous", 3, anonymous=True),
                              q3_row("v2_speed2", 1000, eligible=False),
                              q3_row("v2_seed18", 1000, anonymous=True, eligible=False),
                              q3_row("v2_sjf_bridge", 2000, eligible=False)])
        selected = runner.select_q3(table)
        self.assertEqual(selected["primary_selected_scenario"], "main_named")
        self.assertEqual(selected["conditional_selected_scenario"], "main_anonymous")

    def test_no_positive_gain_returns_base_in_both_categories(self):
        table = pd.DataFrame([q3_row("zero", 0), q3_row("negative", -1),
                              q3_row("anonymous_zero", 0, anonymous=True)])
        selected = runner.select_q3(table)
        self.assertEqual(selected["primary_selected_scenario"], "v2_base")
        self.assertEqual(selected["conditional_selected_scenario"], "v2_base")

    def test_equal_gains_are_resolved_by_name(self):
        table = pd.DataFrame([q3_row("zeta", 2), q3_row("alpha", 2),
                              q3_row("zeta_anon", 2, anonymous=True), q3_row("alpha_anon", 2, anonymous=True)])
        selected = runner.select_q3(table)
        self.assertEqual(selected["primary_selected_scenario"], "alpha")
        self.assertEqual(selected["conditional_selected_scenario"], "alpha_anon")


class Q3V2AggregationTests(unittest.TestCase):
    def test_real_aggregation_uses_each_donor_boundary_and_correct_paired_baselines(self):
        """Use real aggregation/selection, replacing only costly simulations."""
        (ROOT / "tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temporary:
            root = Path(temporary)
            q2, q3 = root / "outputs/question2", root / "outputs/question3/v2"
            q2.mkdir(parents=True)
            chosen_baseline = pd.DataFrame({"job_name": ["same_job"], "start_time": [10.]})
            sjf_baseline = pd.DataFrame({"job_name": ["same_job"], "start_time": [20.]})
            metric = {"wait_hours_mean": 1.}
            calls = []
            experiment = runner.ExperimentV2.__new__(runner.ExperimentV2)
            experiment.args = SimpleNamespace(force=False)
            experiment.provenance = {}
            experiment.nodes = pd.DataFrame()
            experiment.read_schedule = lambda directory: sjf_baseline

            def fake_run(directory, name, config, **kwargs):
                directory.mkdir(parents=True, exist_ok=True)
                if name == "q2_star":
                    return metric, chosen_baseline
                calls.append((name, config, kwargs))
                gain = 1000. if name in {"v2_speed2", "v2_sjf_bridge"} else 10. if name in {"v2_ah", "v2_ahs"} else 1.
                rows = [{"requested_model": "A10", "wait_delta_mean_hours": -gain, "wait_p95_delta_hours": 0}]
                for model in (runner.A100, runner.H800, runner.A800, runner.S2):
                    if name in {"v2_ah", "v2_ahs", "v2_speed2", "v2_sjf_bridge"}:
                        mean, p95 = .5, 2.  # Equal to each management boundary.
                    else:
                        mean, p95 = 1., 3.  # Every other main row fails.
                    # A named candidate with attractive gain still fails if a
                    # single donor has an excessive marginal P95 cost.
                    if name == "v2_ah_d1":
                        mean, p95 = 0., 2.000001 if model == runner.H800 else 0.
                    if name == "v2_ah_d7":
                        mean, p95 = (.500001 if model == runner.H800 else 0.), 0.
                    rows.append({"requested_model": model, "wait_delta_mean_hours": mean, "wait_p95_delta_hours": p95})
                pd.DataFrame(rows).to_csv(directory / "paired_wait_changes.csv", index=False)
                return metric, None

            experiment.run = fake_run
            config = {"policy": "aging", "aging_alpha": 1.}
            with patch.object(runner, "ROOT", root), patch.object(runner, "Q2", q2), patch.object(runner, "Q3", q3), \
                    patch.object(runner, "evaluate", return_value=metric), patch.object(runner, "paired_comparison"), \
                    redirect_stdout(io.StringIO()):
                experiment.q3(config)
            selection = json.loads((q3 / "selection.json").read_text(encoding="utf-8"))
            table = pd.read_csv(q3 / "comparison.csv").set_index("scenario")
            self.assertEqual(selection["primary_selected_scenario"], "v2_ah")
            self.assertEqual(selection["conditional_selected_scenario"], "v2_ahs")
            self.assertTrue(table.loc["v2_ah", "management_feasible"])
            self.assertFalse(table.loc["v2_ah_d1", "management_feasible"])
            self.assertFalse(table.loc["v2_ah_d7", "management_feasible"])
            self.assertFalse(table.loc[table.phase == "sensitivity", "selection_eligible"].any())
            self.assertEqual(runner.select_q3(table.reset_index())["primary_selected_scenario"], "v2_ah")
            self.assertTrue(selection["primary_and_sjf_bridge_both_feasible"])
            for name, used_config, kwargs in calls:
                if name == "v2_sjf_bridge":
                    expected, expected_name = sjf_baseline, "sjf"
                    self.assertEqual(used_config["policy"], "sjf")
                else:
                    expected, expected_name = chosen_baseline, "q2_star"
                    self.assertEqual(used_config["policy"], "aging")
                self.assertIs(kwargs["baseline"], expected)
                self.assertEqual(kwargs["baseline_name"], expected_name)
                self.assertEqual(kwargs["baseline_sha"], runner.frame_digest(expected))


if __name__ == "__main__":
    unittest.main()
