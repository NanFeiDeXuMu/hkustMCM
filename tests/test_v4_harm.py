from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from analyze_v4_harm import diagnose, slots


class SnapshotTests(unittest.TestCase):
    def fixture(self):
        rows = [dict(job_name='run', requested_model='A10', assigned_model='A10', submit_time=0.,
                     start_time=0., end_time=20., original_duration=20., gpu_request=4, cpu_request=4., worker_num=1),
                dict(job_name='head', requested_model='A10', assigned_model='A10', submit_time=1.,
                     start_time=20., end_time=30., original_duration=10., gpu_request=4, cpu_request=4., worker_num=2),
                dict(job_name='target', requested_model='A10', assigned_model='A10', submit_time=2.,
                     start_time=30., end_time=130., original_duration=100., gpu_request=1, cpu_request=0., worker_num=1)]
        changed = pd.DataFrame(rows)
        base = changed.copy()
        base.start_time = [11., 1., 11.]
        base.end_time = base.start_time+base.original_duration
        alloc = pd.DataFrame([('run', 'n0', 1), ('head', 'n0', 1), ('head', 'n1', 1), ('target', 'n1', 1)],
                             columns=['job_name', 'node_name', 'worker_count'])
        nodes = pd.DataFrame([('n0', 'A10', 4, 4.), ('n1', 'A10', 4, 4.)],
                             columns=['node_name', 'gpu_model', 'gpu_capacity_num', 'cpu_num'])
        return base, changed, alloc, nodes

    def test_feasible_victim_behind_infeasible_gang_head(self):
        details, groups, summary = diagnose(*self.fixture())
        victim = details.set_index('job_name').loc['target']
        self.assertEqual(victim.category, 'feasible_behind_infeasible_head')
        self.assertEqual(victim.head_job_name, 'head')
        self.assertEqual(victim.free_gpus, 4)
        self.assertEqual(victim.head_available_worker_slots, 1)
        self.assertEqual(summary['harmed_jobs'], 2)
        self.assertEqual(summary['category_counts']['not_placeable_on_realized_timeline'], 1)

    def test_resource_lack_is_not_labeled_head_blocking(self):
        base, changed, alloc, nodes = self.fixture()
        for frame in (base, changed):
            frame.loc[frame.job_name.eq('target'), 'gpu_request'] = 2
            frame.loc[frame.job_name.eq('target'), 'worker_num'] = 3
        base.loc[base.job_name.eq('run'), ['start_time', 'end_time']] = [111., 131.]
        alloc = alloc[~alloc.job_name.eq('target')]
        alloc = pd.concat([alloc, pd.DataFrame([('target', 'n0', 2), ('target', 'n1', 1)], columns=alloc.columns)], ignore_index=True)
        details, _, _ = diagnose(base, changed, alloc, nodes)
        self.assertEqual(details.set_index('job_name').loc['target', 'category'], 'not_placeable_on_realized_timeline')

    def test_zero_cpu_and_fractional_slots(self):
        self.assertEqual(slots(np.array([2, 4]), np.array([0., .5]), 1, 0.), 6)
        self.assertEqual(slots(np.array([2, 4]), np.array([0., .5]), 1, .25), 2)

    def test_duplicate_identifiers_rejected(self):
        base, changed, alloc, nodes = self.fixture()
        with self.assertRaises(ValueError):
            diagnose(pd.concat([base, base.iloc[:1]]), changed, alloc, nodes)

    def test_changed_original_requests_rejected(self):
        base, changed, alloc, nodes = self.fixture()
        changed.loc[changed.job_name.eq('target'), 'gpu_request'] = 2
        with self.assertRaises(ValueError):
            diagnose(base, changed, alloc, nodes)


if __name__ == '__main__':
    unittest.main()
