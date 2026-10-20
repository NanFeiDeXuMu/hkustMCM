"""Build bilingual source-protection reports and a paper-ready comparison plot."""
import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_plot_cache = ROOT/'tmp/matplotlib'
_plot_cache.mkdir(parents=True, exist_ok=True)
os.environ.setdefault('MPLCONFIGDIR', str(_plot_cache))

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from experiment_store import digest, read_json, write_json

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'outputs/v6_source_protection')
    out = parser.parse_args().output.resolve()
    verification = read_json(out/'verification.json')
    if not verification['passed']:
        raise ValueError('Independent verification must pass before reporting')
    protocol = read_json(out/'protocol.json')
    decision = read_json(out/'development_selection.json')
    table = pd.read_csv(out/'validation_summary.csv').sort_values(
        ['speedup', 'configuration'], ascending=[False, True])
    raw = pd.read_csv(out/'validation.csv')
    dev = pd.read_csv(out/'development.csv')
    selected = decision['selected']
    tail_zh = tail_en = ''
    if selected:
        seed = protocol['validation_seeds'][0]
        baseline = pd.read_csv(ROOT/'outputs/question2/q2_star/schedule.csv', dtype={'job_name': str},
                               float_precision='round_trip').set_index('job_name')
        tail_rows = []
        for speed in protocol['speeds']:
            old_path = out/'cases'/f'validation_current_s{seed}_v{speed:g}'
            new_path = out/'cases'/f'validation_{selected}_s{seed}_v{speed:g}'
            old = pd.read_csv(old_path/'schedule.csv', dtype={'job_name': str}, float_precision='round_trip').set_index('job_name')
            new = pd.read_csv(new_path/'schedule.csv', dtype={'job_name': str}, float_precision='round_trip').set_index('job_name').reindex(old.index)
            cutoff = read_json(new_path/'scenario.json')['routing']['max_routed_duration_seconds']
            mask = new.requested_model.eq('A10') & new.original_duration.gt(cutoff)
            extra = (new.start_time-old.start_time)/3600
            tail_rows.append({'seed': seed, 'speedup': speed, 'cutoff_days': cutoff/86400,
                'jobs': int(mask.sum()), 'all_match_q2_start': bool(new.loc[mask, 'start_time'].eq(baseline.loc[new.index[mask], 'start_time']).all()),
                'additional_wait_job_hours': float(extra[mask].sum()), 'total_extra_wait_job_hours': float(extra.sum()),
                'share_of_total_extra_wait': float(extra[mask].sum()/extra.sum()) if extra.sum() else None})
        pd.DataFrame(tail_rows).to_csv(out/'long_job_tradeoff_first_seed.csv', index=False)
        parts_zh = [f"速度{r['speedup']:g}时{r['share_of_total_extra_wait']*100:.2f}%" for r in tail_rows]
        parts_en = [f"{r['share_of_total_extra_wait']*100:.2f}% at speed {r['speedup']:g}" for r in tail_rows]
        equal = all(r['all_match_q2_start'] for r in tail_rows)
        if equal:
            tail_zh = (f"仅在首验证种子{seed}的完整配对中，{tail_rows[0]['jobs']:,}个原时长超过75天的A10作业全部保留Q2*启动时刻。"
                f"这组作业对相对现方案新增总等待的贡献为：{'、'.join(parts_zh)}。新增总等待按全体作业的等待差求和，"
                '这一分解仅描述首种子，见[长作业代价分解](../outputs/v6_source_protection/long_job_tradeoff_first_seed.csv)。')
            tail_en = (f"In the complete paired case for the first validation seed {seed}, all {tail_rows[0]['jobs']:,} A10 jobs with original "
                f"durations above 75 days retain their Q2* starts. They account for {', and '.join(parts_en)} of the increase in total "
                'waiting relative to the current policy. The denominator sums signed waiting differences across all jobs. '
                'This decomposition describes the first seed only; see the [long-job breakdown](../outputs/v6_source_protection/long_job_tradeoff_first_seed.csv).')
    grouped_cases = []
    for _, case in raw.iterrows():
        frame = pd.read_csv(out/'cases'/case.scenario/'a10_harm_groups.csv')
        frame['configuration'], frame['speedup'], frame['seed'] = case.configuration, case.speedup, case.seed
        grouped_cases.append(frame)
    grouped = pd.concat(grouped_cases).groupby(['configuration', 'speedup', 'grouping', 'group'], as_index=False).agg(
        mean_jobs=('jobs', 'mean'), mean_harmed_fraction=('harmed_fraction', 'mean'),
        mean_positive_wait_job_hours=('positive_wait_job_hours', 'mean'),
        mean_net_wait_job_hours_saved=('net_wait_job_hours_saved', 'mean'),
        mean_completion_harmed_fraction=('completion_harmed_fraction', 'mean'),
        mean_positive_completion_job_hours=('positive_completion_job_hours', 'mean'))
    grouped.to_csv(out/'validation_group_summary.csv', index=False)
    group_table = '| Policy | Group | Later starts % | Positive waiting loss job h | Net waiting saved job h | Later completions % |\n|---|---|---:|---:|---:|---:|\n'
    for _, r in grouped[(grouped.speedup == .8) & (grouped.grouping != 'all')].iterrows():
        group_table += (f'| {r.configuration} | {r.group} | {r.mean_harmed_fraction*100:.4f} | '
                        f'{r.mean_positive_wait_job_hours:.3f} | {r.mean_net_wait_job_hours_saved:.3f} | '
                        f'{r.mean_completion_harmed_fraction*100:.4f} |\n')
    group_table_zh = group_table.replace('| Policy | Group | Later starts % | Positive waiting loss job h | Net waiting saved job h | Later completions % |',
        '| 方案 | 分组 | 更晚启动 % | 正等待损失 作业小时 | 净等待节省 作业小时 | 更晚完成 % |')
    def result_table(english=False):
        header = ('| Policy | Speed | Mean wait h | A10 later starts % | Positive wait loss job h | A10 later completions % | Donor mean max h | Donor P95 max h | Donor passes |\n'
            if english else '| 方案 | 速度 | 总体平均等待 h | A10更晚启动 % | 正等待损失 作业小时 | A10更晚完成 % | donor最大均值增量 h | donor最大P95增量 h | donor通过数 |\n')
        text = header+'|---|---:|---:|---:|---:|---:|---:|---:|---:|\n'
        for _, r in table.iterrows():
            name = ('Current' if english else '当前75/30天') if r.configuration == 'current' else r.configuration
            text += (f'| {name} | {r.speedup:g} | {r.mean_wait_h:.6f} | {r.a10_harmed_fraction*100:.4f} | '
                f'{r.a10_positive_wait_job_hours:.3f} | {r.a10_completion_harmed_fraction*100:.4f} | '
                f'{r.donor_worst_mean_h:.6f} | {r.donor_worst_p95_h:.6f} | {r.donor_passed}/{r.seeds} |\n')
        return text
    development = '| 方案 | 六情景平均等待 h | 最坏donor均值增量 h | 最坏donor P95增量 h | 联合通过数 |\n|---|---:|---:|---:|---:|\n'
    for name, group in dev.groupby('configuration', sort=True):
        development += (f'| {name} | {group.wait_hours_mean.mean():.6f} | '
            f'{group.max_donor_mean_wait_delta_hours.max():.6f} | {group.max_donor_p95_delta_hours.max():.6f} | '
            f'{int(group.joint_pass.sum())}/6 |\n')
    development_en = development.replace('| 方案 | 六情景平均等待 h | 最坏donor均值增量 h | 最坏donor P95增量 h | 联合通过数 |',
        '| Policy | Six-case mean waiting h | Worst donor mean delta h | Worst donor P95 delta h | Joint passes |')
    changes = []
    if selected:
        for speed in (1., .8):
            old = table[(table.configuration == 'current') & (table.speedup == speed)].iloc[0]
            new = table[(table.configuration == selected) & (table.speedup == speed)].iloc[0]
            group = raw[(raw.configuration == selected) & (raw.speedup == speed)]
            changes.append((speed, old, new, group))
        summary_zh = f'开发阶段锁定 **{selected}**。在十个未用资格种子、两种速度的20次验证中，'
        group = raw[raw.configuration == selected]
        summary_zh += (f'A10零正等待损失为{int(group.source_zero_wait_harm.sum())}/20，'
                       f'同时满足donor门槛、A10净收益及总体等待门槛为{int(group.joint_pass.sum())}/20。')
        summary_en = (f'The development rule selected **{selected}**. Across ten unused eligibility seeds and two assumed speeds, '
            f'{int(group.source_zero_wait_harm.sum())}/20 validation cases have zero positive A10 waiting loss; '
            f'{int(group.joint_pass.sum())}/20 also satisfy both donor limits, positive A10 net benefit and the aggregate waiting limit.')
        config = protocol['configurations'][selected]
        summary_zh += f" 空位扫描上限为{config['protection']['loan_scan']}个作业，donor双资源借用比例上限为{config['cap']:g}。"
        summary_en += f" The source loan scan is {config['protection']['loan_scan']} jobs and the donor CPU/GPU borrowing fraction is {config['cap']:g}."
    else:
        summary_zh = '没有候选通过全部开发门槛，未选择新方案；后续验证只保留当前规则。不放宽门槛或用验证结果重选。'
        summary_en = 'No candidate passed every development condition. No new policy was selected; validation contains the current policy only.'
    trade_zh, trade_en = '', ''
    for speed, old, new, group in changes:
        percent = (new.mean_wait_h/old.mean_wait_h-1)*100
        old_group = raw[(raw.configuration == 'current') & (raw.speedup == speed)]
        trade_zh += (f'速度{speed:g}时，总体平均等待从{old.mean_wait_h:.6f}变为{new.mean_wait_h:.6f}小时'
            f'（{percent:+.2f}%）；候选跨种子等待范围{group.wait_hours_mean.min():.6f}–{group.wait_hours_mean.max():.6f}小时。'
            f'平均最终完工时间为{old.mean_last_completion_days:.6f}→{new.mean_last_completion_days:.6f}天；'
            f'跨种子最坏的最大等待为{old_group.wait_hours_max.max():.6f}→{group.wait_hours_max.max():.6f}小时，'
            f'第90天完成作业数的跨种子均值为{old_group.completed_at_90d.mean():.1f}→{group.completed_at_90d.mean():.1f}。\n\n')
        trade_en += (f'At speed {speed:g}, mean waiting changes from {old.mean_wait_h:.6f} to {new.mean_wait_h:.6f} h '
            f'({percent:+.2f}%). The selected policy ranges from {group.wait_hours_mean.min():.6f} to '
            f'{group.wait_hours_mean.max():.6f} h across seeds. Mean last completion changes from '
            f'{old.mean_last_completion_days:.6f} to {new.mean_last_completion_days:.6f} days. '
            f'The worst maximum wait across seeds changes from {old_group.wait_hours_max.max():.6f} to '
            f'{group.wait_hours_max.max():.6f} h; mean completed jobs at day 90 changes from '
            f'{old_group.completed_at_90d.mean():.1f} to {group.completed_at_90d.mean():.1f}.\n\n')
    source_hashes = '\n'.join(f'- `{name}`: `{value}`' for name, value in protocol['source_sha256'].items())
    reproduce = (f'python src/reproduce_source_case.py --scenario outputs/v6_source_protection/cases/validation_{selected}_s20261033_v0.8/scenario.json'
                 if selected else 'python src/reproduce_source_case.py --scenario outputs/v6_source_protection/cases/validation_current_s20261033_v0.8/scenario.json')
    commands = ('```text\npython -m unittest discover -s tests -p "test_*.py"\n'
        'python tests/verify_v6_outputs.py\n'+reproduce+'\n'
        'python src/run_v6_source_protection.py --output outputs/v6_source_protection_reproduction --phase all\n'
        'python tests/verify_v6_outputs.py --output outputs/v6_source_protection_reproduction\n```')
    details = (f"{verification['historical_files']:,}", f"{verification['exact_regression_cells']:,}",
               f"{verification['independent_decimal_comparisons']:,}", f"{verification['checks']:,}")
    interval_checks = sum(r['independent_loan_interval_checks'] for r in verification['full_details'])
    replay_root = ROOT/'outputs/v6_source_reproduction'
    replay_case = replay_root/'cases/development_loans512_s20261030_v0.8'
    replay_zh = replay_en = ''
    if (replay_case/'reproduction_check.json').exists() and (replay_root/'cache_replay_check.json').exists():
        replay = read_json(replay_case/'reproduction_check.json')
        audit = read_json(replay_case/'independent_validation.json')
        cache = read_json(replay_root/'cache_replay_check.json')
        if not (replay['passed'] and audit['passed'] and cache['passed']):
            raise ValueError('Recorded single-case reproduction failed')
        replay_zh = (f"另对开发情景loans512、种子20261030、速度0.8做完整工程复现：{replay['scalar_comparisons']}项原指标一致，"
            f"新安排通过{audit['comparisons']:,}项独立Decimal审计；再次调用全部命中缓存，{cache['files_unchanged']}个文件不变。"
            '这不计为新增资格样本，见[复现核对](../outputs/v6_source_reproduction/cases/development_loans512_s20261030_v0.8/reproduction_check.json)。')
        replay_en = (f"A separate full reproduction of the development loans512 case, seed 20261030 at speed 0.8, matches "
            f"{replay['scalar_comparisons']} original scalar metrics and passes {audit['comparisons']:,} independent Decimal checks. "
            f"A repeated call hits the cache and leaves {cache['files_unchanged']} files unchanged. This is engineering reproduction, not a new eligibility sample.")
    zh = f'''# A10内部等待保护实验

{summary_zh} 保护参照是不分流的Q2*，不是保证所有作业都比旧分流方案更早启动。等待改善与完成时间改善分开评价。

## 验证结果

以下平均值按十个资格种子等权汇总。donor列取跨种子、逐目标池的最大增量；门槛为均值≤0.5小时且边际P95≤2小时。正等待损失先对每个作业取正值再求和，不被其他作业的收益抵消。

{result_table()}
{trade_zh}速度0.8意味着迁移后执行时长为原时长的1.25倍。即使启动不晚，迁出作业仍可能更晚完成；这一残余损失已单列，不能把等待保护写成全口径无损。表中比例以全部111,284个A10作业为分母。

![Validation tradeoff](../outputs/v6_source_protection/source_protection_comparison.png)

速度0.8的分组结果如下，比例为每个组内比例的跨种子均值。时长分组按未迁移的原始执行时长定义；migrated/stayed按实际迁移结果分类，是描述性分组，不作为迁移因果效应。两速度全部分组见[分组汇总](../outputs/v6_source_protection/validation_group_summary.csv)。

{group_table_zh}

## 在线机制与适用边界

独立影子队列只接收当前已到达的A10作业，按Q2*的对数aging T7d及原节点放置规则运行。影子作业开始时，若实际作业还在等待，就在相同节点立即启动；如果它已经迁出或提前启动，则相应影子占用区间可以形成临时空位。实际候选必须在每个借用节点的区间结束前完成，保留多worker同启和CPU容量约束。源池扫描范围固定为到达顺序队列的前128或512个等待作业，再按时长优先尝试。

该机制不读取预先计算的基线启动时刻，也不读取未来到达。基线CSV只用于事后配对评价。保护成立的归纳条件是：影子要求的物理节点从未被越期借用；同一时刻先归还实际资源，再处理影子完成与启动。未提前启动的作业因此保留原启动机会。当前实现明确要求A10每节点1 GPU，符合全部2,494个A10节点；不直接推广到多GPU节点或执行时长预测有误的系统。CPU请求为小数或零均保留。

平均等待增加的机制是保留了已经迁出作业的影子占用：空闲节点只能接受能在预约到期前完成的作业，较长或较宽的作业可能无法利用这些区间。原分流允许更自由地重新安排，因此能获得更大的平均收益，也可能让另一批A10作业稍晚启动。零等待损失是明确的公平性取舍，本轮不据此自动替换追求较低平均等待的现方案。

{tail_zh}

原始时长超过75天的作业继续留在A10，未裁剪或删除。donor仍为A100→H800，资格比例假设为0.5，实际执行时长准入分别为75天与30天。速度、兼容资格和准确运行时长仍是建模假设；所有作业运行至完成，不以提交窗口截断。等待及完成损失均以小时计，最终完工以相对原始时间原点的天计。

## 开发选择和证据范围

协议先登记六个方案、三个既用开发资格种子20261030–20261032及速度1和0.8，共36次。联合合格条件为A10逐作业零正等待损失、A10净收益>0、总体平均等待不高于Q2*的31.189516小时，且donor两门槛均通过；合格者按开发平均等待最小锁定，名称用于平手排序。独立验证使用20261033–20261042，没有利用验证再次调参。

{development}
种子只改变同一trace中的假定迁移资格，不是20份独立工作负载。每个速度十次验证不能证明对任意资格集合均有效，也不支持全局最优结论。实现采用独立实验引擎保存冻结历史语义，保留公共存储和审计接口；没有把这一实验扩展称为完整内核重构。

## 复核与复现

187项单元测试通过。关闭保护时16份完整历史安排共{details[1]}字段精确一致，{details[0]}份既有文件哈希不变。本轮{verification['full_schedules']}份完整安排通过{details[2]}项独立Decimal审计，其余{verification['metrics_only']}次仅保存指标，不冒充逐节点独立审计。新增验收{details[3]}项检查零失败，其中{interval_checks:,}个实际空位借用分配独立核对基线可回收区间。完整案例逐A10作业启动时刻也按Decimal直接比较。

{replay_zh}

{commands}

单案例复现使用专用适配器显式恢复source_protection；冻结通用SimulationSpec尚不包含该字段。全实验重放应使用新目录，重新登记两小时预算；已有案例可在同一协议下复用。需要原始数据、Q2*完整参考以及协议所列历史回归安排。

输入目录：`2026_MCM_Senior_GPU_Scheduling/2026_MCM_Senior_GPU_Scheduling/`。

{source_hashes}

实现、验证器与基线哈希见[协议](../outputs/v6_source_protection/protocol.json)，完整核验见[验收](../outputs/v6_source_protection/verification.json)，逐案例来源与文件收据在cases目录。主论文和既有补充ZIP保留历史版本，尚未吸收本轮结果。AI辅助完成实现、实验与分析；参赛队需人工确认运行时长、速度/兼容假设、门槛和论文措辞。
'''
    en = f'''# A10 source waiting protection

{summary_en} The reference is the no-routing Q2* schedule, rather than the previous routing policy. Waiting and completion losses are evaluated separately.

## Validation results

Means average ten eligibility seeds equally. Donor columns are maxima over seeds and the two named destination pools. Limits remain 0.5 h for mean waiting and 2 h for the difference of marginal P95s. Positive waiting loss sums positive per-job differences before aggregation; gains cannot cancel these losses. A10 percentages use all 111,284 source jobs.

{result_table(True)}
{trade_en}At speed 0.8, migrated execution takes 1.25 times the original duration. Earlier or unchanged starts can still produce later completions. The reported waiting protection therefore does not imply zero completion harm.

![Validation comparison](../outputs/v6_source_protection/source_protection_comparison.png)

The speed-0.8 strata below average within-group fractions across seeds. Duration bands use original execution times. Migrated/stayed groups describe realized assignments and do not estimate a causal migration effect. Both speeds are included in the [group summary](../outputs/v6_source_protection/validation_group_summary.csv).

{group_table}

## Mechanism and assumptions

An online shadow A10 pool receives only arrived jobs and runs the unmodified Q2* log-aging policy with a seven-day scale. At each shadow start, a still-waiting actual job starts on the exact shadow nodes. If that job has already migrated or started early, the shadow reservation can be loaned to another waiting job whose entire execution fits before every borrowed node expires. Gang starts and physical CPU limits are retained. The bounded arrival-order source prefix has 128 or 512 jobs, with shorter durations attempted first.

The controller never reads the saved baseline schedule or future arrivals. By induction, actual borrowers release every node before its shadow reservation expires. Any source job not already started can therefore use its original start and placement. This construction requires the observed one-GPU geometry of all 2,494 A10 nodes and known execution durations; it is not a guarantee under duration prediction errors or arbitrary node geometry. Fractional and zero CPU requests remain valid.

The shadow retains reservations for jobs that actually migrated. Idle physical capacity can only accept jobs that fit within those intervals, so long or wide jobs may lose the earlier starts available under unrestricted rescheduling. This explains the fairness tradeoff: the protected mode is retained as an option rather than automatically replacing the policy with lower mean waiting.

{tail_en}

The original 75-day migration eligibility ceiling and destination execution ceilings of 75 days for A100 and 30 days for H800 remain. Longer jobs stay in A10. Donors are attempted in A100 then H800 order; eligibility probability is assumed to be 0.5. Compatibility and relative speed are assumptions. All submitted jobs run to completion without truncation at the submission horizon. Hours measure waiting/completion loss; last completion is measured in days from the original time origin.

## Selection and verification

Six predeclared configurations were evaluated on three development eligibility seeds, 20261030–20261032, at speeds 1 and 0.8: 36 cases. A candidate must pass all six cases with zero individual positive A10 waiting loss, positive A10 net gain, mean cluster waiting at most 31.189516 h, and both donor limits. Eligible candidates are ranked by mean development waiting, then name. The locked choice is evaluated on seeds 20261033–20261042 without reselection.

{development_en}
These seeds perturb eligibility on one trace; they are not independent workload traces. The results do not establish global optimality or universal donor protection. The experimental engine preserves the frozen historical implementations and reuses the transactional storage and audit interfaces.

All 187 unit tests pass. Sixteen disabled-control regressions match {details[1]} cells exactly; {details[0]} historical files retain their hashes. The {verification['full_schedules']} saved validation schedules pass {details[2]} independent Decimal checks. The other {verification['metrics_only']} cases retain metrics only and have no independent node-level audit. An additional {details[3]} checks pass, including {interval_checks:,} allocation-level checks that source loans fit inside reclaimed baseline intervals. Full cases also compare each A10 start directly using Decimal arithmetic.

{replay_en}

{commands}

The dedicated reproduction adapter restores the explicit source-protection field, which is absent from the frozen generic SimulationSpec. A full rerun uses a new output directory and a new two-hour budget. Input CSVs, the full Q2* reference and the protocol's historical regression cases are required.

Source directory: `2026_MCM_Senior_GPU_Scheduling/2026_MCM_Senior_GPU_Scheduling/`.

{source_hashes}

See the [protocol](../outputs/v6_source_protection/protocol.json), [verification](../outputs/v6_source_protection/verification.json) and per-case receipts for provenance. The main manuscript and previous supplement remain historical versions. AI assisted implementation and analysis; the team must verify assumptions, management thresholds and final claims.
'''
    for language, content in [('zh', zh), ('en', en)]:
        (ROOT/f'reports/B_v6_source_protection_{language}.md').write_text(content, encoding='utf-8')
    plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False})
    fig, axes = plt.subplots(1, 3, figsize=(12, 4.2), constrained_layout=True)
    labels = [f'{"Current" if r.configuration == "current" else "Protected"}\nv={r.speedup:g}' for _, r in table.iterrows()]
    colors = ['#64748b' if r.configuration == 'current' else '#087f8c' for _, r in table.iterrows()]
    for ax, field, title, unit in zip(axes,
        ['mean_wait_h', 'a10_harmed_fraction', 'a10_completion_harmed_fraction'],
        ['Cluster mean waiting', 'A10 later starts', 'A10 later completions'], ['Hours', 'Percent of A10 jobs', 'Percent of A10 jobs']):
        values = table[field].to_numpy() * (100 if 'fraction' in field else 1)
        bars = ax.bar(np.arange(len(table)), values, color=colors, width=.65)
        ax.set_xticks(np.arange(len(table)), labels)
        ax.set_title(title)
        ax.set_ylabel(unit)
        ax.set_ylim(0, max(values.max()*1.2, 1))
        ax.bar_label(bars, labels=[f'{v:.2f}' for v in values], padding=4, fontsize=9)
        ax.grid(axis='y', alpha=.18)
        ax.set_axisbelow(True)
    fig.suptitle('Source protection: ten eligibility seeds per speed; paired against Q2*', fontsize=12)
    for ext in ('png', 'svg'):
        fig.savefig(out/f'source_protection_comparison.{ext}', dpi=180)
    plt.close(fig)
    paths = [out/name for name in ('protocol.json', 'validation.csv', 'validation_summary.csv', 'validation_group_summary.csv', 'verification.json')]
    if selected:
        paths.append(out/'long_job_tradeoff_first_seed.csv')
    paths += [ROOT/f'reports/B_v6_source_protection_{language}.md' for language in ('zh', 'en')]
    paths += [out/f'source_protection_comparison.{ext}' for ext in ('png', 'svg')]
    write_json(out/'report_provenance.json', {'script_sha256': digest(Path(__file__)),
        'files': {str(p.relative_to(ROOT)): digest(p) for p in paths}})
    print('Bilingual reports and PNG/SVG comparison written from verified evidence.')


if __name__ == '__main__':
    main()
