"""Create v4 figures and bilingual evidence notes from completed saved results."""
from pathlib import Path
import json

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/v4_validation'
FIG = OUT/'figures'
COLORS = ['#176B87', '#B95135', '#448344']


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def markdown(table):
    headers = list(table.columns)
    return '\n'.join(['| '+' | '.join(headers)+' |', '| '+' | '.join(['---']*len(headers))+' |']+
                     ['| '+' | '.join(map(str, row))+' |' for row in table.itertuples(index=False, name=None)])


def save(fig, name):
    FIG.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG/f'{name}.png', dpi=180, bbox_inches='tight')
    fig.savefig(FIG/f'{name}.svg', bbox_inches='tight')
    plt.close(fig)


def figures(development, validation, aliases):
    plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False,
                         'svg.fonttype': 'none'})
    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.8), layout='constrained')
    for hours, color in [(0, COLORS[0]), (1, COLORS[1])]:
        records = []
        for days in (30, 45, 60, 75, 90):
            group = development[development.configuration.eq(f'd{days}_t{hours}h')]
            records.append((days, group.wait_hours_mean.mean(), group.max_donor_mean_wait_delta_hours.max(), group.strict_feasible.all()))
        a = np.array([r[:3] for r in records])
        axes[0].plot(a[:, 0], a[:, 1], 'o-', color=color, label=f'Minimum wait {hours} h')
        axes[1].plot(a[:, 0], a[:, 2], 'o-', color=color, label=f'Minimum wait {hours} h')
        for days, mean, harm, passed in records:
            if not passed:
                axes[0].scatter(days, mean, marker='x', color='black', s=100, zorder=4)
                axes[1].scatter(days, harm, marker='x', color='black', s=100, zorder=4)
    axes[0].set_ylabel('Mean wait across development seeds (h)')
    axes[1].set_ylabel('Worst donor mean increase across seeds (h)')
    axes[1].axhline(.5, color='gray', ls='--', lw=1, label='Strict mean threshold')
    for ax in axes:
        ax.set_xlabel('Maximum original duration eligible for routing (days)')
        ax.legend(fontsize=8)
        ax.grid(alpha=.2)
    fig.suptitle('Development only: duration and minimum-wait controls')
    fig.supxlabel('Three previously used eligibility seeds. Crosses fail at least one strict constraint; P95 is also checked.', fontsize=8)
    save(fig, '01_development')
    fig, axes = plt.subplots(1, 3, figsize=(12.5, 4.5), layout='constrained')
    fields = [('wait_hours_mean', 'Overall mean wait (h)'), ('max_donor_mean_wait_delta_hours', 'Worst donor mean increase (h)'),
              ('max_donor_p95_delta_hours', 'Worst donor P95 increase (h)')]
    labels = {'existing_strict': 'Existing strict', 'existing_compensation': 'Existing compensation', 'new_candidate': 'New candidate'}
    for i, (alias, configuration) in enumerate(aliases.items()):
        group = validation[validation.configuration.eq(configuration)].sort_values('seed')
        for ax, (field, ylabel) in zip(axes, fields):
            ax.scatter(i+np.linspace(-.12, .12, len(group)), group[field], color=COLORS[i], s=28, alpha=.85)
            ax.plot([i-.23, i+.23], [group[field].mean()]*2, color='black', lw=2)
            ax.set_ylabel(ylabel)
    axes[1].axhline(.5, color=COLORS[0], ls='--', lw=1, label='Strict: 0.5 h')
    axes[1].axhline(1., color=COLORS[1], ls=':', lw=1, label='Compensation: 1 h')
    axes[2].axhline(2., color=COLORS[0], ls='--', lw=1, label='Strict: 2 h')
    for ax in axes:
        ax.set_xticks(range(len(aliases)), [labels[a].replace(' ', '\n', 1) for a in aliases], fontsize=8)
        ax.grid(axis='y', alpha=.2)
    axes[1].legend(fontsize=7)
    axes[2].legend(fontsize=7)
    fig.suptitle('Ten held-out eligibility seeds on the same workload')
    fig.supxlabel('Each point is one seed; black lines are means. Compensation additionally checks harmed share and exchange ratio.', fontsize=8)
    save(fig, '02_validation')
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.7), layout='constrained')
    duration_labels = ['<=1h', '1h-1d', '1d-7d', '>7d']
    for i, alias in enumerate(('existing_strict', 'existing_compensation')):
        group = pd.read_csv(OUT/'harm_diagnostics'/alias/'harm_groups.csv')
        group = group[group.dimension.eq('duration_group')].set_index('group').loc[duration_labels]
        x = np.arange(4)+(i-.5)*.34
        axes[0].bar(x, group.harmed_fraction*100, .34, color=COLORS[i], label=alias.replace('existing_', '').capitalize())
        axes[1].bar(x, group.positive_job_hours_added, .34, color=COLORS[i])
    axes[0].set_ylabel('Jobs with increased waiting (%)')
    axes[1].set_ylabel('Sum of positive waiting increases (job-hours)')
    axes[0].legend()
    for ax in axes:
        ax.set_xticks(range(4), duration_labels)
        ax.set_xlabel('Original A10 runtime group')
        ax.grid(axis='y', alpha=.2)
    fig.suptitle('A10 internal harm in the two existing recommendations')
    fig.supxlabel('Original seed 20261017; positive losses are distinct from net savings. Donor credits do not cover these A10 losses.', fontsize=8)
    save(fig, '03_source_harm')


def main():
    development = pd.read_csv(OUT/'development.csv')
    validation = pd.read_csv(OUT/'validation.csv')
    summary = pd.read_csv(OUT/'validation_summary.csv')
    stress = pd.read_csv(OUT/'speed_stress.csv')
    choice = read(OUT/'development_selection.json')
    aliases = read(OUT/'validation_aliases.json')
    verification = read(OUT/'verification.json') if (OUT/'verification.json').exists() else None
    indexed = summary.set_index('label')
    original = indexed.loc['existing_strict']
    newer = indexed.loc['new_candidate'] if 'new_candidate' in indexed.index else original
    reduction = 1-newer.wait_hours_mean_mean/original.wait_hours_mean_mean
    figures(development, validation, aliases)
    scores = pd.DataFrame(choice['scores'])
    score_table = scores.copy()
    score_table.columns = ['Configuration', 'All 3 strict passes', 'Mean A10 gain (h)', 'Worst donor mean cost (h)']
    for field in score_table.columns[2:]:
        score_table[field] = score_table[field].map(lambda v: f'{v:.4f}')
    display = pd.DataFrame([{'Role': r.label, 'Configuration': r.configuration,
        'Passes': f'{r.passed}/{r.total}', 'Wilson 95%': f'{r.wilson95_low:.1%}–{r.wilson95_high:.1%}',
        'Overall mean wait (h)': f'{r.wait_hours_mean_mean:.3f}',
        'Worst donor mean range (h)': f'{r.max_donor_mean_wait_delta_hours_min:.3f}–{r.max_donor_mean_wait_delta_hours_max:.3f}'} for r in summary.itertuples()])
    stress_display = stress[['speedup', 'wait_hours_mean', 'max_donor_mean_wait_delta_hours', 'max_donor_p95_delta_hours', 'strict_feasible']].copy()
    for field in stress_display.columns[1:4]:
        stress_display[field] = stress_display[field].map(lambda v: f'{v:.4f}')
    snapshots = []
    for alias in ('existing_strict', 'existing_compensation'):
        item = read(OUT/'harm_diagnostics'/alias/'summary.json')
        groups = pd.read_csv(OUT/'harm_diagnostics'/alias/'harm_groups.csv')
        total = groups[groups.dimension.eq('migration_group')]
        short = groups[(groups.dimension.eq('duration_group')) & groups.group.eq('<=1h')].iloc[0]
        stayed = groups[(groups.dimension.eq('migration_group')) & groups.group.eq('stayed_A10')].iloc[0]
        snapshots.append({'Scheme': alias, 'Harmed A10 jobs': item['harmed_jobs'],
                          'Feasible behind blocked head': item['category_counts'].get('feasible_behind_infeasible_head', 0),
                          'Not placeable at snapshot': item['category_counts'].get('not_placeable_on_realized_timeline', 0),
                          'A10 positive loss (job-h)': f'{total.positive_job_hours_added.sum():.3f}',
                          'Harmed among stayed A10': f'{stayed.harmed_fraction:.2%}',
                          'Short-job net savings (job-h)': f'{short.net_job_hours_saved:.3f}'})
    audit_text = ('尚未完成独立验收，不作通过声明。' if verification is None else
                  f"独立验收passed={verification['passed']}；{verification['comparisons']:,}项总账/协议/快照等核对，"
                  f"{len(verification['full_schedule_audits'])}份新完整调度共{verification['full_schedule_comparisons']:,}项Decimal审计；"
                  f"{verification['metrics_only_not_independently_audited']}个仅指标结果不计为逐节点独立审计通过。"
                  f"{verification['historical_files_unchanged']}份历史输出文件哈希未变。")
    command = '''```powershell
.\\.venv\\Scripts\\python.exe src\\run_v4_validation.py --phase all --workers 2
.\\.venv\\Scripts\\python.exe src\\analyze_v4_harm.py
.\\.venv\\Scripts\\python.exe -m unittest discover -s tests -p 'test_*.py'
.\\.venv\\Scripts\\python.exe tests\\verify_v4_outputs.py --audit-schedules
.\\.venv\\Scripts\\python.exe src\\report_v4_validation.py
```'''
    zh = f'''# v4：重新开放实验后的开发、验证与受损机制

日期：2026-10-20。规则：[D028](../DECISIONS.md)。原始数据、冻结调度核心、Q2*=T7d和历史实验保持不变。这里新增的是分流配置搜索与资格随机集合验证，不是独立工作负载验证。所有调度覆盖全部224,112个作业至完工。

**主要结果：** 新候选在十个新资格种子中通过严格门槛{int(newer.passed)}/10，总体平均等待跨种子均值为{newer.wait_hours_mean_mean:.3f}小时；原60天方案为{original.wait_hours_mean_mean:.3f}小时，新候选降低{reduction:.2%}。其donor代价更接近门槛，且速度降至.8时失去严格可行性。因此可作为p=.5、速度1假设下的性能更优候选，原60天方案仍提供更大的已观察服务余量。原补偿方案仅{int(indexed.loc['existing_compensation','passed'])}/10通过补偿门槛，不能作为稳健默认方案。

## 设计与选择

13个预先登记配置×3个已使用的开发种子，共39次运行。Dmax=30/45/60/75/90天分别配τ=0/1h、cap=.25；另无限时长cap=.10/.15/.25。均A100→H800、p=.5、速度1。三个种子全部通过严格门槛且A10收益均为正才合格，最大化跨种子的平均A10收益，再按最坏donor均值和名称破平局。

开发阶段固定选择 **{choice['selected_configuration']}**。十个验证种子20261020–20261029从未用于此选择；验证后不重新选型。

{markdown(score_table)}

![Development](../outputs/v4_validation/figures/01_development.png)

## 固定方案在十个新资格种子上的结果

{markdown(display)}

existing_strict和new_candidate按严格门槛（每donor均值≤.5h、边际P95≤2h）统计；existing_compensation按原补偿门槛（交换比≥100、每donor均值≤1h、受损比例≤20%）统计，均另要求A10净收益>0。全部逐种子结果和两种门槛都保存在[验证表](../outputs/v4_validation/validation.csv)。门槛未放宽。Wilson区间是假定资格哈希抽样下的二项通过率区间；十次全通过也不能解释为100%可靠，更不是跨trace泛化。

![Validation](../outputs/v4_validation/figures/02_validation.png)

本轮独立于开发的是资格集合，不是提交时间、请求资源、作业时长或节点容量；这些保持同一条历史轨迹。若后续根据验证失败重新选型，必须再设新验证集合，不能把本表继续当未使用验证。

## 速度假设的负向与正向压力

固定开发选择，使用验证首种子20261020，仅改变迁入速度为.8或1.25；不再选择参数。速度.8意味着迁入运行时间除以.8，比原时长更长。

{markdown(stress_display)}

这些是统一速度情景，不是实际硬件性能；没有估计显存、网络、搬运成本或定价弹性。

## A10内部损失：证据收紧原解释

{markdown(pd.DataFrame(snapshots))}

在每个受损作业的**无分流基线启动时刻**，从已有完整分配表重建新方案所有已发生启动/结束后的逐节点资源，并按log-aging键找当时仍在等待的队首。绝大部分受损作业在该时刻本身不可放置；只有小部分满足“自身可放置且更高优先级队首不可放置”的直接瞬时阻塞证据。不能将全部损失归因为宽作业队首阻塞，也不能从此诊断排除更早阻塞或其他路径依赖的间接影响。此检查只解释局部状态，不拆解整个等待差的因果贡献。

分组结果进一步区分迁出作业与留在A10的作业、原时长和总GPU数。≤1h组的净节省若为负，表示该组平均等待上升，即使全体A10净收益很大。donor积分C未包含A10内部正损失。完整快照、分组和每类三个最大损失案例见[受损诊断目录](../outputs/v4_validation/harm_diagnostics)。

![Source harm](../outputs/v4_validation/figures/03_source_harm.png)

## 验证、复现与论文整合

{audit_text}

机制诊断的全部分类逻辑已核对，每个现有方案的六个案例另以Decimal重建资源与队首；不冒称独立重算了全部快照。完整审计验证可行性与积分守恒，不证明最优或真实兼容。

{command}

协议在[protocol.json](../outputs/v4_validation/protocol.json)，记录登记时刻、三小时预算、配置、种子和阈值。原实验runner在相同源码/配置/输入下复用已完成证据，不覆盖不匹配结果；原三小时预算过后不启动新仿真。已有历史输出文件哈希记录在historical_hashes.json。

从精简补充包重建三份锁定验证安排时，另运行 `python src/reproduce_v4_full.py`：它在新目录重建Q2*基线并核对其冻结哈希，再复现三个方案的首个验证种子，逐项核对十个核心指标。输出位于 `outputs/v4_reproduction/<时间>/`，对reproduction_check.json中的每个case路径运行 `python tests/verify_schedules.py --scenario-dir <路径>`。这是新三小时复现预算，不重新搜索或选型，也不改写旧结果；它不重建全部71次实验或历史机制分析所需的v3完整CSV。

统稿时应同时修正：分配率不等于完成作业吞吐量；α=100来自最大等待与全部完工时间双约束；p=.5按作业定义，不能直接推导半数用户或最优折扣；C/S是名义等待积分比，非实际现金成本；T7d不是原T网格的范围端点。成员信息和实际人工核验范围仍由参赛队填写。

AI记录：Codex登记并实现本轮脚本、运行实验、计算与验证结果、绘图及撰写本说明；参赛队仍需核对假设、结论与提交材料。不提交、不推送、不修改外部内容。
'''
    en = f'''# Additional experiments: eligibility validation and source-pool harm

20 October 2026. D028 reopened experimentation with a fixed three-hour budget. The node-level scheduler, Q2 log-aging T=7 days, raw trace, and historical outputs remain unchanged. These experiments assess eligibility uncertainty on the same workload, not generalization across independent workloads.

The new candidate passes strict criteria in {int(newer.passed)}/10 new eligibility seeds. Its across-seed overall mean waiting is {newer.wait_hours_mean_mean:.3f} h, compared with {original.wait_hours_mean_mean:.3f} h for the existing 60-day rule, a {reduction:.2%} reduction. The new candidate has less donor-service margin and fails strict criteria when migration speed is .8. Its performance advantage is conditional on the declared p=.5 and equal-speed assumptions. The old compensation scheme passes its own criteria in only {int(indexed.loc['existing_compensation','passed'])}/10 seeds.

## Development and locked choice

Thirteen routing configurations were evaluated on three previously used development seeds. Duration limits of 30, 45, 60, 75 and 90 days were crossed with minimum waits of zero or one hour at a 25% borrowing cap. Three further configurations had no duration limit and caps of 10%, 15% or 25%. All use A100 before H800, eligibility p=.5 and equal speed. Each development seed must satisfy positive A10 benefit, donor mean increases at most .5 h and marginal P95 increases at most 2 h. The feasible configuration with greatest average A10 gain was selected, with declared ties.

The locked choice is **{choice['selected_configuration']}**. Ten subsequent eligibility seeds were excluded from this choice; no reselection follows validation.

{markdown(score_table)}

## Held-out eligibility results

{markdown(display)}

Existing strict and new candidate use strict criteria. Existing compensation uses exchange ≥100, each donor mean increase ≤1 h and each donor harmed fraction ≤20%; all require positive A10 benefit. Wilson 95% intervals describe a binomial pass probability under the assumed eligibility sampling model. They do not quantify workload generalization, and ten successes do not establish certainty. Eligibility hashes alter participating jobs; the workload and capacities are unchanged.

![Validation](../outputs/v4_validation/figures/02_validation.png)

## Runtime sensitivity

The locked candidate is tested at speeds .8 and 1.25 with seed 20261020. Migrated runtime is original runtime divided by speed. This is a declared sensitivity, not measured hardware performance or another selection stage.

{markdown(stress_display)}

## Source-pool harm

{markdown(pd.DataFrame(snapshots))}

For each harmed A10 job in the original two recommendations, the changed schedule is reconstructed at that job's baseline start time, after simultaneous events. Only a small fraction are then individually placeable behind an infeasible higher-priority head. Most are not placeable on the realized changed timeline. This supports a limited local head-blocking diagnosis, not attribution of all additional waiting to wide queue heads. Earlier blocking and altered resource-release paths can still contribute indirectly. Donor credits exclude these A10 internal losses; positive loss totals differ from net savings. Negative short-job net savings mean that group's mean waiting increased.

![A10 harm](../outputs/v4_validation/figures/03_source_harm.png)

## Reproducibility and limits

Protocol, configuration manifests, scalar metrics, full first-seed schedules, and independent verification are in outputs/v4_validation. Saved full cases receive CSV/Decimal node-capacity and paired-ledger audits; metric-only cases are not counted as independently audited schedules. All historical output hashes are checked. Snapshot category arithmetic is checked for every harmed job, with independent exact resource/head reconstruction for six published examples per existing policy. This does not independently replay every snapshot or prove causality or global optimality.

For a data-free supplement with original inputs supplied separately, `python src/reproduce_v4_full.py` rebuilds Q2* and the three locked first-validation-seed cases in a fresh outputs/v4_reproduction directory, checks the baseline hash and ten scalar metrics per case, and preserves original evidence. Then run the independent schedule auditor for each case path in reproduction_check.json. It does not rerun all 71 experiments or reconstruct historical v3 inputs for source-harm analysis.

{command}

AI assistance: Codex implemented and executed the experiments, validations, figures and drafts. The team must verify assumptions, interpretation, references and final disclosure. No files were submitted to the competition system.
'''
    (ROOT/'reports/B_v4_validation_zh.md').write_text(zh, encoding='utf-8')
    (ROOT/'reports/B_v4_validation_en.md').write_text(en, encoding='utf-8')
    print('Created bilingual v4 reports and three PNG/SVG figure pairs.')


if __name__ == '__main__':
    main()
