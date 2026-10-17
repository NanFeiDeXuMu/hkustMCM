"""Hand-calculated management-boundary and frozen-choice checks for D026."""
from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from run_v3_experiments import c2_sensitivity, select_recommendations, main_configs


def candidate(name, gain=1., mean=0., p95=0., fraction=0., ratio=None, infinite=True, eligible=True, anonymous=False):
    return {"scenario": name, "a10_mean_wait_gain_hours": gain,
            "max_donor_mean_wait_delta_hours": mean, "max_donor_p95_delta_hours": p95,
            "max_donor_native_wait_increased_fraction": fraction, "exchange_ratio": ratio,
            "exchange_ratio_infinite": infinite, "selection_eligible": eligible, "includes_anonymous_donor": anonymous}


class SelectionTests(unittest.TestCase):
    def test_strict_checks_both_boundaries_and_compensation_does_not_replace_it(self):
        rows = [candidate("strict", 2, .5, 2), candidate("compensated", 5, 1, 9, .2, 100, False),
                candidate("bad_p95", 3, .4, np.nextafter(2., np.inf), .1, 101, False)]
        selection = select_recommendations(pd.DataFrame(rows))
        self.assertEqual(selection["strict_selected_scenario"], "strict")
        self.assertEqual(selection["compensation_selected_scenario"], "compensated")

    def test_all_three_compensation_bounds_are_checked_at_full_precision(self):
        rows = [candidate("valid", 1, 1, 99, .2, 100, False),
                candidate("mean_above", 10, np.nextafter(1., np.inf), 0, .1, 200, False),
                candidate("fraction_above", 10, .1, 0, np.nextafter(.2, np.inf), 200, False),
                candidate("ratio_below", 10, .1, 0, .1, np.nextafter(100., -np.inf), False)]
        selection = select_recommendations(pd.DataFrame(rows))
        self.assertEqual(selection["compensation_selected_scenario"], "valid")
        self.assertEqual(selection["compensation_feasible_candidates"], ["valid"])

    def test_infinite_exchange_still_requires_positive_a10_gain(self):
        selection = select_recommendations(pd.DataFrame([candidate("zero", 0), candidate("negative", -2)]))
        self.assertEqual(selection["strict_selected_scenario"], "v3_base")
        self.assertEqual(selection["compensation_selected_scenario"], "v3_base")
        self.assertTrue(selection["compensation_fallback_to_strict"])

    def test_anonymous_and_sensitivity_excluded_and_ties_use_name(self):
        rows = [candidate("zeta", 2), candidate("alpha", 2), candidate("anonymous", 100, anonymous=True),
                candidate("sensitivity", 200, eligible=False)]
        selection = select_recommendations(pd.DataFrame(rows))
        self.assertEqual(selection["strict_selected_scenario"], "alpha")
        self.assertEqual(selection["compensation_selected_scenario"], "alpha")

    def test_compensation_falls_back_to_strict_without_changing_thresholds(self):
        rows = [candidate("strict", 2, .1, .2, .21, 99, False)]
        selection = select_recommendations(pd.DataFrame(rows))
        self.assertEqual(selection["compensation_selected_scenario"], "strict")
        self.assertTrue(selection["compensation_fallback_to_strict"])

    def test_c2_multiplier_changes_only_mean_bound_not_group_protection(self):
        rows = []
        for name, mean, maximum, group in [("fcfs", 100, 5, 10), ("sjf", 10, 100, 1),
                                           ("tight", 15, 50, 10), ("loose", 20, 20, 10),
                                           ("bad_group", 12, 1, 11)]:
            rows.append({"scenario": name, "wait_hours_mean": mean, "wait_hours_max": maximum,
                         **{f"group_{key}_mean_wait_hours": group for key in ("le1h", "1h_1d", "1d_7d", "gt7d")}})
        result = c2_sensitivity(pd.DataFrame(rows))
        self.assertEqual(result.selected_scenario.tolist(), ["tight", "loose", "loose"])

    def test_grid_has_exact_twelve_configurations_and_donor_order_is_preserved(self):
        configs = main_configs()
        self.assertEqual(len(configs), 12)
        self.assertEqual(configs["v3_ha"].donors, tuple(reversed(configs["v3_ah_t1h"].donors)))
        self.assertEqual(configs["v3_ah_d30_t1h"].max_routed_duration_seconds, 30*86400)
        self.assertEqual(configs["v3_ah_d30_t1h"].min_wait_seconds, 3600)


if __name__ == "__main__":
    unittest.main()
