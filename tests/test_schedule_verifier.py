"""Hand-built feasible/infeasible schedules for the independent verifier."""
import csv
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from verify_schedules import verify_scenario
from verify_all_schedules import audit_saved_scenarios


JOB_FIELDS = ["job_name", "gpu_model", "gpu_request", "cpu_request", "worker_num", "submit_time", "duration"]
NODE_FIELDS = ["node_name", "gpu_model", "gpu_capacity_num", "cpu_num"]
RUN_FIELDS = ["job_name", "requested_model", "assigned_model", "gpu_request", "cpu_request", "worker_num", "submit_time",
              "original_duration", "start_time", "end_time", "run_duration", "wait_time", "migrated", "flexible", "start_sequence"]


def write_csv(path, fieldnames, data):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)


def make_run(job, start=None, assigned=None, runtime=None, flexible=False):
    start = job["submit_time"] if start is None else start
    assigned = assigned or job["gpu_model"]
    runtime = job["duration"] if runtime is None else runtime
    return dict(job_name=job["job_name"], requested_model=job["gpu_model"], assigned_model=assigned,
                gpu_request=job["gpu_request"], cpu_request=job["cpu_request"], worker_num=job["worker_num"],
                submit_time=job["submit_time"], original_duration=job["duration"], start_time=start,
                end_time=start + runtime, run_duration=runtime, wait_time=start - job["submit_time"],
                migrated=assigned != job["gpu_model"], flexible=flexible)


class IndependentScheduleVerifierTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "tmp").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / "tmp")
        self.directory = Path(self.temporary.name)
        self.jobs = [
            dict(zip(JOB_FIELDS, ["a", "A10", 1, 0.5, 2, 0, 10])),
            dict(zip(JOB_FIELDS, ["b", "A10", 1, 0, 1, 10, 5])),
        ]
        self.nodes = [
            dict(zip(NODE_FIELDS, ["n0", "A10", 2, 1])),
            dict(zip(NODE_FIELDS, ["n1", "A100-SXM4-80GB", 4, 8])),
        ]
        self.runs = [make_run(job) for job in self.jobs]
        self.allocations = [{"job_name": "a", "node_name": "n0", "worker_count": 2},
                            {"job_name": "b", "node_name": "n0", "worker_count": 1}]
        self.routing = None

    def tearDown(self):
        self.temporary.cleanup()

    def check_fixture(self):
        write_csv(self.directory / "job_info_clean.csv", JOB_FIELDS, self.jobs)
        write_csv(self.directory / "node_info_df.csv", NODE_FIELDS, self.nodes)
        exported_runs = [dict(run) for run in self.runs]
        for sequence, run in enumerate(exported_runs, 1):
            run.setdefault("start_sequence", sequence)
        write_csv(self.directory / "schedule.csv", RUN_FIELDS, exported_runs)
        write_csv(self.directory / "allocations.csv", ["job_name", "node_name", "worker_count"], self.allocations)
        (self.directory / "scenario.json").write_text(json.dumps({"policy": "fixture", "routing": self.routing}), encoding="utf-8")
        return verify_scenario(self.directory, self.directory)

    def migration_fixture(self):
        self.jobs = [dict(zip(JOB_FIELDS, ["a", "A10", 1, 0.5, 1, 0, 10]))]
        self.routing = {"flexible_fraction": 1.0, "speedup": 3.0, "donor_cap_fraction": 0.25,
                        "seed": 20261017, "donors": ["A100-SXM4-80GB", "A800-SXM4-80GB"]}
        self.runs = [make_run(self.jobs[0], assigned="A100-SXM4-80GB", runtime=10 / 3, flexible=True)]
        self.allocations = [{"job_name": "a", "node_name": "n1", "worker_count": 1}]

    def batch_case(self, relative, storage="full", *, include_csvs=True, preserve_report=False):
        self.check_fixture()
        destination = self.directory / "outputs" / relative
        destination.mkdir(parents=True, exist_ok=True)
        metadata = {"policy": "fixture", "routing": self.routing}
        if storage is not None:
            metadata["schedule_storage"] = storage
        (destination / "scenario.json").write_text(json.dumps(metadata), encoding="utf-8")
        if include_csvs:
            for name in ("schedule.csv", "allocations.csv"):
                shutil.copyfile(self.directory / name, destination / name)
        if preserve_report:
            report = verify_scenario(destination, self.directory)
            (destination / "independent_validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return destination

    def check_batch(self):
        return audit_saved_scenarios(self.directory / "outputs", self.directory, verbose=False)

    def assert_rejected_with(self, text):
        report = self.check_fixture()
        self.assertFalse(report["passed"])
        self.assertTrue(any(text in item for item in report["failures"]), report["failures"])

    def test_half_open_release_fractional_and_zero_cpu(self):
        result = self.check_fixture()
        self.assertTrue(result["passed"], result["failures"])
        self.assertEqual(result["work_by_assigned_model"]["A10"]["integrated_gpu_seconds"], "25")
        self.assertEqual(result["node_resource_peaks"]["n0"]["peak_gpu"], 2)

    def test_cpu_capacity_overload_rejected(self):
        self.jobs[0]["cpu_request"] = self.runs[0]["cpu_request"] = 0.6
        self.assert_rejected_with("CPU allocation 1.2 outside")

    def test_gpu_capacity_overlap_rejected(self):
        self.jobs[1]["submit_time"] = 9
        self.runs[1] = make_run(self.jobs[1])
        self.assert_rejected_with("GPU allocation 3 outside")

    def test_worker_split_and_missing_worker_rejected(self):
        self.allocations[0]["worker_count"] = 1.5
        self.assert_rejected_with("noninteger value")

    def test_node_model_mismatch_rejected(self):
        self.allocations[0]["node_name"] = "n1"
        self.assert_rejected_with("incompatible assigned model")

    def test_duplicate_job_rejected(self):
        self.runs.append(dict(self.runs[0]))
        self.assert_rejected_with("unknown or duplicate job")

    def test_speed_conversion_and_donor_cap_valid(self):
        self.migration_fixture()
        result = self.check_fixture()
        self.assertTrue(result["passed"], result["failures"])
        self.assertEqual(result["migrated_jobs"], 1)
        self.assertEqual(result["donor_cap_checks"]["A100-SXM4-80GB"]["peak_borrowed_gpu"], 1)

    def test_donor_cpu_cap_independent_of_physical_node_rejected(self):
        self.migration_fixture()
        self.nodes[0]["cpu_num"] = 8
        self.jobs[0]["cpu_request"] = self.runs[0]["cpu_request"] = 3
        self.assert_rejected_with("borrowed pool A100-SXM4-80GB at 0: CPU allocation 3 outside [0, 2.00]")

    def test_hash_eligibility_rejected(self):
        self.migration_fixture()
        self.routing["flexible_fraction"] = 0
        self.assert_rejected_with("migrated job was not selected as flexible")

    def test_incorrect_speed_conversion_rejected(self):
        self.migration_fixture()
        self.runs[0]["run_duration"] = self.runs[0]["end_time"] = 5
        self.assert_rejected_with("runtime does not match the scenario assumption")

    def test_donor_gpu_cap_independent_of_physical_node_rejected(self):
        self.migration_fixture()
        self.jobs[0]["worker_num"] = self.runs[0]["worker_num"] = self.allocations[0]["worker_count"] = 2
        self.assert_rejected_with("borrowed pool A100-SXM4-80GB at 0: GPU allocation 2 outside [0, 1]")

    def test_migrated_job_cannot_queue_then_start(self):
        self.migration_fixture()
        self.runs[0]["start_time"] = 1
        self.runs[0]["end_time"] += 1
        self.runs[0]["wait_time"] = 1
        self.assert_rejected_with("migrated job must start at submission")

    def test_pull_can_start_after_original_queue_wait(self):
        self.migration_fixture()
        self.routing.update(mode="pull", min_wait_seconds=5)
        self.runs[0] = make_run(self.jobs[0], start=7, assigned="A100-SXM4-80GB", runtime=10 / 3, flexible=True)
        result = self.check_fixture()
        self.assertTrue(result["passed"], result["failures"])
        self.assertEqual(result["routing_mode"], "pull")

    def test_pull_minimum_wait_violated(self):
        self.migration_fixture()
        self.routing.update(mode="pull", min_wait_seconds=5)
        self.runs[0] = make_run(self.jobs[0], start=4, assigned="A100-SXM4-80GB", runtime=10 / 3, flexible=True)
        self.assert_rejected_with("pull migration starts before submission plus minimum wait")

    def test_pull_h800_and_anonymous_donor_are_supported(self):
        for model in ("H800", "GPU-series-2"):
            with self.subTest(model=model):
                self.migration_fixture()
                self.nodes[1]["gpu_model"] = model
                self.routing.update(mode="pull", donors=[model], min_wait_seconds=5)
                self.runs[0] = make_run(self.jobs[0], start=5, assigned=model, runtime=10 / 3, flexible=True)
                result = self.check_fixture()
                self.assertTrue(result["passed"], result["failures"])
                self.assertEqual(result["donor_cap_checks"][model]["peak_borrowed_gpu"], 1)

    def test_pull_override_can_raise_default_gpu_cap(self):
        self.migration_fixture()
        self.routing.update(mode="pull", donor_caps=[["A100-SXM4-80GB", 0.5]])
        self.jobs[0]["worker_num"] = self.runs[0]["worker_num"] = self.allocations[0]["worker_count"] = 2
        result = self.check_fixture()
        self.assertTrue(result["passed"], result["failures"])
        self.assertEqual(result["donor_cap_checks"]["A100-SXM4-80GB"]["gpu_cap"], 2)

    def test_pull_override_lower_gpu_cap_is_enforced(self):
        self.migration_fixture()
        self.routing.update(mode="pull", donor_caps=[["A100-SXM4-80GB", 0.1]])
        self.assert_rejected_with("GPU allocation 1 outside [0, 0]")

    def test_pull_override_lower_cpu_cap_is_enforced(self):
        self.migration_fixture()
        self.nodes[0]["cpu_num"] = 8
        self.jobs[0]["cpu_request"] = self.runs[0]["cpu_request"] = 3
        self.routing.update(mode="pull", donor_cap_fraction=1, donor_caps=[["A100-SXM4-80GB", 0.25]])
        self.assert_rejected_with("CPU allocation 3 outside [0, 2.00]")

    def test_arrival_ignores_new_minimum_wait_and_cap_overrides(self):
        self.migration_fixture()
        self.routing.update(mode="arrival", min_wait_seconds=999, donor_caps=[["A100-SXM4-80GB", 0]],
                            max_routed_duration_seconds=0, donor_reserve_fraction=1)
        result = self.check_fixture()
        self.assertTrue(result["passed"], result["failures"])
        self.assertEqual(result["migration_min_wait_seconds"], "0")
        self.assertEqual(result["donor_cap_checks"]["A100-SXM4-80GB"]["gpu_cap"], 1)
        self.assertIsNone(result["maximum_routed_original_duration_seconds"])
        self.assertEqual(result["donor_reserve_fraction"], "0")

    def test_pull_still_disallows_other_source_models(self):
        self.migration_fixture()
        self.jobs[0]["gpu_model"] = self.runs[0]["requested_model"] = "H800"
        self.nodes[0]["gpu_model"] = "H800"
        self.routing.update(mode="pull")
        self.assert_rejected_with("disallowed model substitution")

    def test_pull_zero_cpu_request_allowed_when_borrowed_cpu_cap_is_exactly_full(self):
        self.migration_fixture()
        self.nodes[0]["cpu_num"] = 2
        self.nodes[1]["gpu_capacity_num"] = 8
        self.jobs = [dict(zip(JOB_FIELDS, ["a", "A10", 1, 2, 1, 0, 10])),
                     dict(zip(JOB_FIELDS, ["b", "A10", 1, 0, 1, 0, 10]))]
        self.routing.update(mode="pull", speedup=1)
        self.runs = [make_run(job, assigned="A100-SXM4-80GB", flexible=True) for job in self.jobs]
        self.allocations = [{"job_name": job["job_name"], "node_name": "n1", "worker_count": 1} for job in self.jobs]
        result = self.check_fixture()
        self.assertTrue(result["passed"], result["failures"])
        donor = result["donor_cap_checks"]["A100-SXM4-80GB"]
        self.assertEqual(donor["peak_borrowed_gpu"], 2)
        self.assertEqual(donor["peak_borrowed_cpu"], "2")

    def test_pull_original_duration_cutoff_includes_equality(self):
        self.migration_fixture()
        self.routing.update(mode="pull", max_routed_duration_seconds=10)
        result = self.check_fixture()
        self.assertTrue(result["passed"], result["failures"])

    def test_pull_duration_cutoff_uses_original_not_accelerated_runtime(self):
        self.migration_fixture()
        self.routing.update(mode="pull", max_routed_duration_seconds=9)
        # Original runtime is 10; accelerated runtime is only 10/3.
        self.assert_rejected_with("original duration exceeds maximum routed duration")

    def test_pull_reserve_gpu_equality_allowed(self):
        self.migration_fixture()
        self.routing.update(mode="pull", donor_reserve_fraction=.75)
        result = self.check_fixture()
        self.assertTrue(result["passed"], result["failures"])
        self.assertEqual(result["donor_reserve_start_checks"]["A100-SXM4-80GB"]["minimum_free_gpu_after_migration_start"], 3)

    def test_pull_reserve_gpu_violation_rejected(self):
        self.migration_fixture()
        self.routing.update(mode="pull", donor_reserve_fraction=.8)
        self.assert_rejected_with("free GPU 3 below reserve 3.2")

    def test_pull_reserve_cpu_equality_allowed(self):
        self.migration_fixture()
        self.nodes[0]["cpu_num"] = 4
        self.jobs[0]["cpu_request"] = self.runs[0]["cpu_request"] = 2
        self.routing.update(mode="pull", donor_reserve_fraction=.75)
        result = self.check_fixture()
        self.assertTrue(result["passed"], result["failures"])

    def test_pull_reserve_cpu_violation_rejected(self):
        self.migration_fixture()
        self.nodes[0]["cpu_num"] = 4
        self.jobs[0]["cpu_request"] = self.runs[0]["cpu_request"] = 2.1
        self.routing.update(mode="pull", donor_cap_fraction=1, donor_reserve_fraction=.75)
        self.assert_rejected_with("free CPU 5.9 below reserve 6.00")

    def reserve_native_fixture(self, native_start, native_sequence):
        self.migration_fixture()
        self.routing.update(mode="pull", speedup=1, donor_cap_fraction=1, donor_reserve_fraction=.5)
        self.nodes[0]["cpu_num"] = 4
        migrating = dict(zip(JOB_FIELDS, ["a", "A10", 1, 1, 2, 0, 10]))
        native = dict(zip(JOB_FIELDS, ["n", "A100-SXM4-80GB", 2, 6, 1, native_start, 10]))
        self.jobs = [migrating, native]
        moved_run = make_run(migrating, assigned="A100-SXM4-80GB", flexible=True)
        native_run = make_run(native)
        moved_run["start_sequence"] = 1 if native_sequence == 2 else 2
        native_run["start_sequence"] = native_sequence
        # CSV order is deliberately opposite to start_sequence.
        self.runs = [native_run, moved_run]
        self.allocations = [{"job_name": "a", "node_name": "n1", "worker_count": 2},
                            {"job_name": "n", "node_name": "n1", "worker_count": 1}]

    def test_later_native_start_can_use_reserved_resources(self):
        self.reserve_native_fixture(native_start=1, native_sequence=2)
        result = self.check_fixture()
        self.assertTrue(result["passed"], result["failures"])

    def test_same_time_later_native_start_can_use_reserve(self):
        self.reserve_native_fixture(native_start=0, native_sequence=2)
        result = self.check_fixture()
        self.assertTrue(result["passed"], result["failures"])

    def test_same_time_earlier_native_start_counts_before_migration(self):
        self.reserve_native_fixture(native_start=0, native_sequence=1)
        self.assert_rejected_with("free GPU 0 below reserve 2.0")

    def test_reserve_reconstruction_rejects_duplicate_start_sequence(self):
        self.reserve_native_fixture(native_start=0, native_sequence=2)
        for run in self.runs:
            run["start_sequence"] = 1
        self.assert_rejected_with("start_sequence must be positive and unique")

    def test_reserve_reconstruction_rejects_nonchronological_start_sequence(self):
        self.reserve_native_fixture(native_start=1, native_sequence=2)
        for run in self.runs:
            run["start_sequence"] = 3 - run["start_sequence"]
        self.assert_rejected_with("start_sequence order disagrees with chronological job starts")

    def test_positive_reserve_requires_saved_start_sequence_column(self):
        self.migration_fixture()
        self.routing.update(mode="pull", donor_reserve_fraction=.5)
        self.check_fixture()
        write_csv(self.directory / "schedule.csv", [name for name in RUN_FIELDS if name != "start_sequence"], self.runs)
        with self.assertRaisesRegex(ValueError, "missing columns.*start_sequence"):
            verify_scenario(self.directory, self.directory)

    def test_second_same_time_migration_sees_first_migration(self):
        self.migration_fixture()
        self.routing.update(mode="pull", speedup=1, donor_cap_fraction=1, donor_reserve_fraction=.75)
        self.jobs.append(dict(zip(JOB_FIELDS, ["b", "A10", 1, 0, 1, 0, 10])))
        self.runs = [make_run(job, assigned="A100-SXM4-80GB", flexible=True) for job in self.jobs]
        self.allocations.append({"job_name": "b", "node_name": "n1", "worker_count": 1})
        self.assert_rejected_with("job b, A100-SXM4-80GB at 0, start_sequence 2: free GPU 2 below reserve 3.00")

    def test_completion_at_migration_time_is_released_first(self):
        self.migration_fixture()
        self.routing.update(mode="pull", speedup=1, donor_cap_fraction=1, donor_reserve_fraction=.75)
        native = dict(zip(JOB_FIELDS, ["n", "A100-SXM4-80GB", 3, 6, 1, 0, 5]))
        self.jobs.append(native)
        moved_run = make_run(self.jobs[0], start=5, assigned="A100-SXM4-80GB", flexible=True)
        moved_run["start_sequence"] = 2
        native_run = make_run(native)
        native_run["start_sequence"] = 1
        self.runs = [moved_run, native_run]
        self.allocations.append({"job_name": "n", "node_name": "n1", "worker_count": 1})
        result = self.check_fixture()
        self.assertTrue(result["passed"], result["failures"])

    def test_batch_recurses_and_separates_metrics_only(self):
        full = self.batch_case("question3/improved/q3_example")
        skipped = self.batch_case("question2/aging_sweep/alpha_0", "metrics_only", include_csvs=False)
        result = self.check_batch()
        self.assertTrue(result["passed"], result)
        self.assertEqual((result["audited_new"], result["preserved_legacy"], result["skipped_metrics"]), (1, 0, 1))
        self.assertEqual(result["scenarios"], 1)
        self.assertTrue((full / "independent_validation.json").is_file())
        self.assertFalse((skipped / "independent_validation.json").exists())
        self.assertNotIn("passed", result["skipped"][0])

    def test_batch_declared_full_missing_csv_fails(self):
        directory = self.batch_case("question2/aging_best")
        (directory / "allocations.csv").unlink()
        result = self.check_batch()
        self.assertFalse(result["passed"])
        self.assertEqual(result["failure_count"], 1)
        self.assertIn("missing required CSV files: allocations.csv", result["results"][0]["failures"][0])

    def test_batch_legacy_without_storage_still_requires_full_csvs(self):
        self.batch_case("question2/legacy", None, include_csvs=False)
        result = self.check_batch()
        self.assertFalse(result["passed"])
        self.assertEqual(result["skipped_metrics"], 0)

    def test_batch_legacy_audit_is_preserved_byte_for_byte(self):
        directory = self.batch_case("question2/legacy", None, preserve_report=True)
        audit_path = directory / "independent_validation.json"
        original_bytes = audit_path.read_bytes()
        result = self.check_batch()
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["preserved_legacy"], 1)
        self.assertEqual(result["preserved_history"], 1)
        self.assertEqual(result["current_run_comparisons"], 0)
        self.assertEqual(audit_path.read_bytes(), original_bytes)

    def test_batch_previous_full_audit_is_preserved_even_with_storage_header(self):
        directory = self.batch_case("question2/aging_best", "full", preserve_report=True)
        audit_path = directory / "independent_validation.json"
        original_bytes = audit_path.read_bytes()
        result = self.check_batch()
        self.assertTrue(result["passed"], result)
        self.assertEqual((result["preserved_history"], result["preserved_legacy"], result["audited_new"]), (1, 0, 0))
        self.assertEqual(result["current_run_comparisons"], 0)
        self.assertEqual(audit_path.read_bytes(), original_bytes)

    def test_batch_changed_legacy_output_fails_without_replacing_audit(self):
        directory = self.batch_case("question2/legacy", None, preserve_report=True)
        audit_path = directory / "independent_validation.json"
        original_bytes = audit_path.read_bytes()
        with (directory / "schedule.csv").open("a", encoding="utf-8") as handle:
            handle.write("\n")
        result = self.check_batch()
        self.assertFalse(result["passed"])
        self.assertIn("hash does not match schedule.csv", result["results"][0]["failures"][0])
        self.assertEqual(audit_path.read_bytes(), original_bytes)

    def test_batch_metrics_only_does_not_claim_full_audit(self):
        self.batch_case("question2/aging_sweep/alpha_0", "metrics_only", include_csvs=False)
        result = self.check_batch()
        self.assertFalse(result["passed"])
        self.assertEqual((result["scenarios"], result["audited_new"], result["skipped_metrics"]), (0, 0, 1))

    def test_batch_metrics_only_with_full_artifacts_rejects_storage_mismatch(self):
        self.batch_case("question2/aging_sweep/alpha_0", "metrics_only")
        result = self.check_batch()
        self.assertFalse(result["passed"])
        self.assertIn("unexpected schedule/allocation CSV artifacts", result["results"][0]["failures"][0])


if __name__ == "__main__":
    unittest.main()
