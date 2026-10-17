"""Hand-calculated harm, tail thresholds, and bounded slowdown examples."""
from pathlib import Path
import json
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from analyze_tail import analyze_tail

TEMP = Path(__file__).resolve().parents[1] / "tmp/test_tail"
TEMP.mkdir(parents=True, exist_ok=True)


def job(name, wait=0., duration=120., *, gpus=1, workers=1, model="A10", submit=0., runtime=None):
    runtime = duration if runtime is None else runtime
    return dict(job_name=name, requested_model=model, submit_time=submit, start_time=submit+wait,
                end_time=submit+wait+runtime, original_duration=duration, run_duration=runtime,
                gpu_request=gpus, worker_num=workers)


class TailMetricsTests(unittest.TestCase):
    def run_analysis(self, candidate, baseline):
        with tempfile.TemporaryDirectory(dir=TEMP) as temporary:
            output = Path(temporary)
            result = analyze_tail(pd.DataFrame(candidate), pd.DataFrame(baseline), output)
            saved = json.loads((output / "tail_metrics.json").read_text(encoding="utf-8"))
            self.assertEqual(result, saved)
            tables = {name: pd.read_csv(output / f"tail_{name}.csv") for name in
                      ("by_duration_group", "cohorts", "model_distribution")}
        return result, tables

    def test_alignment_harm_fraction_and_original_gpu_work_weight(self):
        baseline = [job("a", 10, 120), job("b", 20, 3600, gpus=2, workers=3),
                    job("c", 30, 86400, model="H800")]
        candidate = [job("c", 29, 86400, model="H800"), job("a", 11, 120),
                     job("b", 25, 3600, gpus=2, workers=3, runtime=1800)]
        result, tables = self.run_analysis(candidate, baseline)
        self.assertEqual(result["fcfs_harmed_jobs"], 2)
        self.assertAlmostEqual(result["fcfs_harmed_fraction"], 2/3)
        self.assertAlmostEqual(result["fcfs_harmed_mean_wait_increase_hours"], 3/3600)
        # Work includes six GPUs for b, and its original hour even though its
        # simulated runtime is half an hour: (120 + 6*3600) / total work.
        self.assertAlmostEqual(result["fcfs_harmed_original_gpu_work_fraction"], 21720/108120)
        cohort = tables["cohorts"].set_index("cohort").loc["fcfs_harmed"]
        self.assertAlmostEqual(cohort.original_gpu_work_hours, 21720/3600)
        self.assertAlmostEqual(cohort.original_duration_hours_p50, (120+3600)/7200)
        models = tables["model_distribution"].set_index(["cohort", "requested_model"])
        self.assertEqual(models.loc[("fcfs_harmed", "A10"), "jobs"], 2)
        self.assertEqual(models.loc[("fcfs_harmed", "H800"), "jobs"], 0)

    def test_zero_harm_returns_null_conditional_statistics(self):
        baseline = [job("a", 10), job("b", 0)]
        result, tables = self.run_analysis([job("b", 0), job("a", 9)], baseline)
        self.assertEqual(result["fcfs_harmed_fraction"], 0)
        self.assertEqual(result["fcfs_harmed_original_gpu_work_fraction"], 0)
        self.assertIsNone(result["fcfs_harmed_mean_wait_increase_hours"])
        cohort = tables["cohorts"].set_index("cohort").loc["fcfs_harmed"]
        self.assertEqual(cohort.jobs, 0)
        self.assertTrue(pd.isna(cohort.original_duration_hours_min))
        self.assertTrue(pd.isna(cohort.original_duration_hours_p99))
        models = tables["model_distribution"].query("cohort == 'fcfs_harmed'")
        self.assertTrue(models.fraction_of_cohort_jobs.isna().all())

    def test_harm_and_extreme_wait_thresholds_are_strict(self):
        hour = 3600.
        baseline = [job("at1000", 1000*hour), job("above1000", 1000*hour),
                    job("atmax", 1200*hour), job("abovemax", 1200*hour),
                    job("tiny", 0)]
        candidate = [job("at1000", 1000*hour), job("above1000", 1000*hour+1),
                     job("atmax", 1200*hour), job("abovemax", 1200*hour+1),
                     job("tiny", 1e-9)]
        result, _ = self.run_analysis(candidate, baseline)
        self.assertEqual(result["fcfs_harmed_jobs"], 3)
        self.assertEqual(result["wait_over_1000h_jobs"], 3)
        self.assertEqual(result["wait_over_fcfs_max_jobs"], 1)
        self.assertEqual(result["fcfs_max_wait_hours"], 1200)

    def test_slowdown_floor_and_sixty_second_denominator(self):
        # Slowdowns are [1, 1, 2, 3]: the first job's 10/60 is floored at 1.
        jobs = [job("a", 0, 10), job("b", 50, 10), job("c", 110, 10), job("d", 240, 120)]
        result, tables = self.run_analysis(jobs, jobs)
        self.assertEqual(result["bounded_slowdown_60s_mean"], 1.75)
        self.assertAlmostEqual(result["bounded_slowdown_60s_p99"], 2.97)
        self.assertEqual(result["bounded_slowdown_60s_max"], 3)
        groups = tables["by_duration_group"].set_index("group")
        self.assertEqual(groups.loc["<=1h", "bounded_slowdown_60s_max"], 3)
        self.assertEqual(groups.loc["1h-1d", "jobs"], 0)
        self.assertTrue(pd.isna(groups.loc["1h-1d", "bounded_slowdown_60s_p99"]))

    def test_groups_use_original_duration_with_closed_upper_boundaries(self):
        durations = [3600., 86400., 7*86400., 7*86400.+1]
        jobs = [job(str(i), duration=d, runtime=10., wait=110.) for i, d in enumerate(durations)]
        _, tables = self.run_analysis(jobs, jobs)
        groups = tables["by_duration_group"].set_index("group")
        for group in ["<=1h", "1h-1d", "1d-7d", ">7d"]:
            self.assertEqual(groups.loc[group, "jobs"], 1)
            self.assertEqual(groups.loc[group, "bounded_slowdown_60s_max"], 2)

    def test_missing_extra_duplicate_null_and_nontext_ids_are_rejected(self):
        base = pd.DataFrame([job("001"), job("002")])
        cases = [base.iloc[:1], pd.DataFrame([job("001"), job("extra")]),
                 pd.DataFrame([job("001"), job("001")]),
                 pd.DataFrame([job("001"), job(None)]), pd.DataFrame([job("001"), job(2)])]
        with tempfile.TemporaryDirectory(dir=TEMP) as temporary:
            for candidate in cases:
                with self.subTest(ids=candidate.job_name.tolist()), self.assertRaises(ValueError):
                    analyze_tail(candidate, base, Path(temporary))
            with self.assertRaises(ValueError):
                analyze_tail(base, pd.concat([base, base.iloc[:1]]), Path(temporary))

    def test_changed_original_job_attributes_are_rejected(self):
        baseline = pd.DataFrame([job("a")])
        for field, value in [("requested_model", "H800"), ("submit_time", 1.),
                             ("original_duration", 240.), ("gpu_request", 2.), ("worker_num", 2.)]:
            candidate = baseline.copy()
            candidate.loc[0, field] = value
            if field == "submit_time":
                candidate.loc[0, ["start_time", "end_time"]] += 1.
            with tempfile.TemporaryDirectory(dir=TEMP) as temporary:
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, "Original job attribute"):
                    analyze_tail(candidate, baseline, Path(temporary))


if __name__ == "__main__":
    unittest.main()
