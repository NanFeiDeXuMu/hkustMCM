"""Paired A10 waiting and completion harms, with unchanged original-job strata."""
import math

import pandas as pd


def analyze_source_harm(baseline, schedule, output=None):
    if baseline.job_name.duplicated().any() or schedule.job_name.duplicated().any():
        raise ValueError('Duplicate job IDs in paired schedules')
    base = baseline.set_index('job_name')
    new = schedule.set_index('job_name')
    if set(base.index) != set(new.index):
        raise ValueError('Paired job sets differ')
    new = new.reindex(base.index)
    for field in ('requested_model', 'original_duration', 'submit_time', 'gpu_request', 'cpu_request', 'worker_num'):
        if not base[field].eq(new[field]).all():
            raise ValueError(f'Original job attributes differ: {field}')
    source = new[new.requested_model.eq('A10')].copy()
    source['wait_delta_h'] = (source.start_time-base.loc[source.index, 'start_time'])/3600
    source['completion_delta_h'] = (source.end_time-base.loc[source.index, 'end_time'])/3600
    source['gpu_count'] = source.gpu_request*source.worker_num

    def summarize(group):
        wait, completion = group.wait_delta_h, group.completion_delta_h
        return {'jobs': len(group), 'harmed_jobs': int((wait > 0).sum()),
            'harmed_fraction': float((wait > 0).mean()) if len(group) else 0.,
            'positive_wait_job_hours': math.fsum(wait.clip(lower=0)),
            'positive_wait_gpu_hours': math.fsum(wait.clip(lower=0)*group.gpu_count),
            'max_wait_increase_hours': max(0., float(wait.max())) if len(group) else 0.,
            'net_wait_job_hours_saved': -math.fsum(wait),
            'completion_harmed_jobs': int((completion > 0).sum()),
            'completion_harmed_fraction': float((completion > 0).mean()) if len(group) else 0.,
            'positive_completion_job_hours': math.fsum(completion.clip(lower=0)),
            'max_completion_increase_hours': max(0., float(completion.max())) if len(group) else 0.}

    total = {f'a10_{key}': value for key, value in summarize(source).items()}
    rows = [{'grouping': 'all', 'group': 'A10', **summarize(source)}]
    source['duration_band'] = pd.cut(source.original_duration, [0, 3600, 86400, 604800, float('inf')],
                                     labels=['le1h', '1h_1d', '1d_7d', 'gt7d'])
    for name, group in source.groupby('duration_band', observed=True, sort=True):
        rows.append({'grouping': 'original_duration', 'group': str(name), **summarize(group)})
    for migrated in (False, True):
        rows.append({'grouping': 'realized_migration', 'group': 'migrated' if migrated else 'stayed',
                     **summarize(source[source.migrated.eq(migrated)])})
    if output is not None:
        pd.DataFrame(rows).to_csv(output/'a10_harm_groups.csv', index=False)
    return total
