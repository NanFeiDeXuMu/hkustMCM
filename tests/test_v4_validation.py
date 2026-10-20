"""Hand-checked development/validation separation and pass-rate boundaries."""
from pathlib import Path
import sys
import unittest

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from run_v4_validation import configurations, DEVELOPMENT_SEEDS, VALIDATION_SEEDS, select_development, wilson


def rows(name, gains, means=(0., 0., 0.), p95=(0., 0., 0.)):
    return [{'configuration': name, 'phase': 'development', 'seed': seed,
             'a10_mean_wait_gain_hours': gain, 'max_donor_mean_wait_delta_hours': mean,
             'max_donor_p95_delta_hours': tail, 'exchange_ratio_infinite': True,
             'exchange_ratio': None, 'max_donor_native_wait_increased_fraction': 0.}
            for seed, gain, mean, tail in zip(DEVELOPMENT_SEEDS, gains, means, p95)]


class ValidationTests(unittest.TestCase):
    def test_any_seed_failure_excludes_high_average_gain(self):
        table = pd.DataFrame(rows('safe', (2, 3, 4))+rows('unsafe', (9, 9, 9), means=(0, .50001, 0)))
        self.assertEqual(select_development(table)['selected_configuration'], 'safe')

    def test_each_seed_requires_positive_gain_and_p95_bound(self):
        table = pd.DataFrame(rows('zero', (0, 4, 4))+rows('tail', (4, 4, 4), p95=(0, 0, 2.0001)))
        self.assertIsNone(select_development(table)['selected_configuration'])

    def test_tie_prefers_lower_worst_donor_mean_then_name(self):
        table = pd.DataFrame(rows('a', (3, 3, 3), means=(.5, .1, 0))+
                             rows('b', (3, 3, 3), means=(.4, .4, .4))+
                             rows('c', (3, 3, 3), means=(.4, .4, .4)))
        self.assertEqual(select_development(table)['selected_configuration'], 'b')

    def test_missing_duplicate_or_validation_seed_rejected(self):
        records = rows('a', (1, 2, 3))
        with self.assertRaises(ValueError):
            select_development(pd.DataFrame(records[:2]))
        with self.assertRaises(ValueError):
            select_development(pd.DataFrame(records+[records[0]]))
        records[0]['phase'] = 'validation'
        with self.assertRaises(ValueError):
            select_development(pd.DataFrame(records))

    def test_wilson_all_pass_does_not_mean_certainty(self):
        low, high = wilson(10, 10)
        self.assertAlmostEqual(low, .7224672, places=6)
        self.assertAlmostEqual(high, 1.)
        low, high = wilson(0, 10)
        self.assertAlmostEqual(low, 0.)
        self.assertAlmostEqual(high, .2775328, places=6)

    def test_fixed_grid_and_disjoint_seed_sets(self):
        self.assertEqual(len(configurations()), 13)
        self.assertFalse(set(DEVELOPMENT_SEEDS) & set(VALIDATION_SEEDS))
        self.assertEqual(len(VALIDATION_SEEDS), 10)
        self.assertEqual(configurations()['d75_t1h'].max_routed_duration_seconds, 75*86400)


if __name__ == '__main__':
    unittest.main()
