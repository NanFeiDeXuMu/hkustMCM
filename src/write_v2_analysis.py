"""Render the Chinese v2 analysis from completed experiment artifacts."""
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
Q2, Q3 = ROOT / "outputs/question2", ROOT / "outputs/question3/v2"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def table(headers, rows):
    return "\n".join(["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
                     + ["| " + " | ".join(map(str, row)) + " |" for row in rows])


def f(value):
    return f"{value:,.3f}"


def main():
    choice2, choice3 = read(Q2 / "selection_v2.json"), read(Q3 / "selection.json")
    t2 = pd.read_csv(Q2 / "comparison_v2.csv").set_index("scenario")
    t3 = pd.read_csv(Q3 / "comparison.csv").set_index("scenario")
    chosen = choice2["selected_scenario"]
    primary, conditional = choice3["primary_selected_scenario"], choice3["conditional_selected_scenario"]
    if (chosen, primary, conditional) != ("tau_604800", "v2_ah_d7", "v2_ahs_d7_r10"):
        raise ValueError("This report-specific narrative must be reviewed for a changed selection")
    star, sjf = t2.loc[chosen], t2.loc["sjf"]
    base, selected = t3.loc["v2_base"], t3.loc[primary]
    pairs = pd.read_csv(Q3 / primary / "paired_wait_changes.csv").set_index("requested_model")
    old_harm = pd.read_csv(Q2 / "v2_candidates/sjf/tail_cohorts.csv").set_index("cohort")
    tail = read(Q2 / "q2_star/tail_metrics.json")
    schedule = pd.read_csv(Q3 / primary / "schedule.csv", dtype={"job_name": str})
    migrants = schedule[schedule.migrated]
    destinations = migrants.groupby("assigned_model").size().to_dict()
    q2_rows = [[name, f(t2.loc[name, "wait_hours_mean"]), f(t2.loc[name, "wait_hours_max"]),
                f(t2.loc[name, "wait_hours_p99"]), f(t2.loc[name, "bounded_slowdown_60s_p99"]),
                f(t2.loc[name, "last_completion_days"]), "是" if t2.loc[name, "feasible"] else "否"]
               for name in ["fcfs", "sjf", "backfill", "backfill_l512", "alpha_1", "alpha_100", *[f"tau_{v}" for v in (3600, 21600, 86400, 259200, 604800, 2592000)]]]
    groups = {name: pd.read_csv(ROOT / t2.loc[name, "output_path"] / "tail_by_duration_group.csv").set_index("group") for name in ("fcfs", "sjf", chosen)}
    group_rows = [[group, *[f(groups[name].loc[group, "wait_hours_mean"]) for name in ("fcfs", "sjf", chosen)]] for group in ["<=1h", "1h-1d", "1d-7d", ">7d"]]
    harm_rows = [[name, int(t2.loc[name, "fcfs_harmed_jobs"]), f"{100*t2.loc[name,'fcfs_harmed_fraction']:.4f}%",
                  f(t2.loc[name, "fcfs_harmed_mean_wait_increase_hours"]), f"{100*t2.loc[name,'fcfs_harmed_original_gpu_work_fraction']:.3f}%",
                  int(t2.loc[name, "wait_over_1000h_jobs"]), int(t2.loc[name, "wait_over_fcfs_max_jobs"])] for name in ["sjf", chosen]]
    def routing_row(name):
        row = t3.loc[name]
        return [name, f(row.wait_hours_mean), f(row.a10_mean_wait_gain_hours), f(row.wait_hours_max),
                f(row.max_donor_mean_wait_delta_hours), f(row.max_donor_p95_delta_hours), "通过" if row.management_feasible else "未通过"]
    main_rows = [routing_row(name) for name in t3[t3.phase == "main"].index]
    sensitivity_rows = [routing_row(name) for name in t3[t3.phase == "sensitivity"].index]
    regression = read(ROOT / "outputs/v2_regression.json")
    audits = read(ROOT / "outputs/scheduling_validation.json")
    regression_status = "全部通过" if regression.get("completed") and regression.get("passed") else "尚未完成，不作通过声明"
    expected_audits = {"question2/q2_star", "question3/v2/v2_ah", "question3/v2/v2_ahs",
                       f"question3/v2/{primary}", f"question3/v2/{conditional}"}
    current_audits = [row for row in audits.get("results", []) if row.get("relative_path") in expected_audits]
    audit_pass = ({row.get("relative_path") for row in current_audits} == expected_audits
                  and all(row.get("passed") and row.get("status") in ("audited_new", "preserved_history")
                          for row in current_audits))
    text = f'''# B题第2、3问：v2 选型、尾部与原生代价分析

更新日期：2026-10-17。本轮依据 `TASK_v2_selection_and_harm.md` 改进实验，保留原始数据和历史场景。当前选择依据[D023/D024](../DECISIONS.md)，方法细节见[方法报告第11节](B_Q23_methods_zh.md)；[英文稿](B_Q23_report_en.md)与[10页论文提纲](paper_outline.md)供统稿使用。

## 1. 主结论

Q2*选中 **对数aging，T=7天**：在每个时长组平均等待不劣于FCFS、总体平均等待不超过SJF两倍的条件下，它的最大等待最小。均值{star.wait_hours_mean:.3f}小时、最大{star.wait_hours_max:.3f}小时，最大等待相对SJF减少{100*(1-star.wait_hours_max/sjf.wait_hours_max):.2f}%。但P99等待从{sjf.wait_hours_p99:.3f}升至{star.wait_hours_p99:.3f}小时，P99有界减速比从{sjf.bounded_slowdown_60s_p99:.3f}升至{star.bounded_slowdown_60s_p99:.3f}；不能写成所有服务指标都改善。

Q3命名型号主推荐为 **`{primary}`**：允许A100/H800，原始时长≤7天，p=.5、借用cap=.25、速度1，不额外设置空闲保留。总体均值由{base.wait_hours_mean:.3f}降至{selected.wait_hours_mean:.3f}小时（{100*(1-selected.wait_hours_mean/base.wait_hours_mean):.2f}%），A10均值由{pairs.loc['A10','wait_mean_before_hours']:.3f}降至{pairs.loc['A10','wait_mean_after_hours']:.3f}小时。在本trace中，所有donor原生作业的等待均未改变；这是观察结果，不是未来零伤害保证。

起主要作用的是**原时长过滤**。单独增加10%/20%池级空闲保留没有达到原生代价阈值；给时长过滤方案再加余量、更多donor或放宽短作业cap也没有产生额外收益。匿名条件方案`{conditional}`与主推荐的核心结果相同，本次结论无需依赖匿名型号。

## 2. A10饱和与SJF的真实代价

全部224,112个作业保留，每个worker的GPU/CPU在同一节点上，全部worker同时启动、不可抢占。各方案初始空闲、只使用已到达作业。90天用于固定窗口评价，仿真仍运行至所有作业完成。A10在既有策略下GPU分配率约99.9%，本池排序不能增加物理容量；它仍能改变哪些作业先完成、固定窗口完成数和等待分布，因此不能声称吞吐指标在所有策略下相同。

SJF在**所有四个原始时长组**的平均等待均低于FCFS，包括>7天组：

{table(['原始时长组','FCFS均值/h','SJF均值/h','Q2*均值/h'], group_rows)}

正式FCFS配对分析如下；受损定义为等待严格增加，工作量按原时长×每worker GPU×worker数加权。

{table(['策略','受损作业数','受损比例','受损者平均增量/h','受损者GPU工作量占比','等待>1000h','等待>FCFS最大值'], harm_rows)}

SJF的2,502个受损者并非全是A10：2,031个A10、392个series-1、53个series-2、26个A800。**等待>1000小时的1,525个作业则全部来自A10**，原始时长从{old_harm.loc['wait_over_1000h','original_duration_hours_min']/24:.3f}到{old_harm.loc['wait_over_1000h','original_duration_hours_max']/24:.3f}天；等待超过FCFS精确最大值1,423.2563889小时的1,199个作业也均为A10。尾部作业数虽少，受损集合占总GPU工作量14.832%，不能忽略。

Q2*将超过FCFS最大等待的作业从1,199减至159，并大幅降低受损者平均正增量；但受损人数、受损工作量占比及等待>1000小时人数均增加。这里是把最极端等待摊得更广的一种折中，不能从C1组均值约束推出个体公平性。

数据来源：[SJF尾部汇总](../outputs/question2/v2_candidates/sjf/tail_metrics.json)、[尾部时长分位数](../outputs/question2/v2_candidates/sjf/tail_cohorts.csv)、[型号构成](../outputs/question2/v2_candidates/sjf/tail_model_distribution.csv)、[Q2*尾部汇总](../outputs/question2/q2_star/tail_metrics.json)。

## 3. 对数aging与选择规则

线性aging使用`d+αa`。由于时长跨多个数量级，小α接近SJF，大α迅速偏向提交顺序。对数aging改用 `ln(d/1秒)+a/T`；提交间隔每增加T，可抵消e倍的时长差。长作业和后来短作业的临界提交间隔为 `T ln(d_long/d_short)`。所有已经同时等待的作业仍使用静态相对顺序，不存在单凭时钟流逝发生的队内交叉。

D023在新扫描前固定：C1要求四个时长组均值分别不高于FCFS；C2要求总体均值≤2×SJF，即精确阈值{choice2['c2_mean_bound_hours']:.9f}小时。对可行候选先最小化最大等待，再最小化均值，再按名称排序。只对旧候选应用新规则，确实选出α=1；加入6个对数参数后选T=7天。共22个候选、{len(choice2['feasible_candidates'])}个可行点，逐项判定保存在[selection_v2.json](../outputs/question2/selection_v2.json)。

{table(['策略','均值/h','最大等待/h','P99等待/h','P99有界减速比','最后完工/天','C1/C2可行'], q2_rows)}

有界减速比为 `max(1,(end−submit)/max(run_duration,60秒))`，无量纲。最大等待由单个极值决定，P99较少受单点影响，因此两者必须并列。T=7天的最大等待优于SJF而P99变差，这正说明单指标不能描述全部尾部。全部候选和分组减速比另存机器可读表。

上一轮α=100仍可作为必须同时封顶最大等待和清空时间时的备选；它的均值432.865小时只比FCFS改善3.23%，不再作为当前Q3主基线。修改选择规则是根据上一轮分析重新定义管理目标，不是独立样本外验证，也不意味着FCFS具有一般的最大等待最优性。

![Q2均值与最大/P99等待](../outputs/question2/figures_v2/04_policy_tradeoff.png)

![四个时长组的均值与P99有界减速比](../outputs/question2/figures_v2/06_group_fairness.png)

## 4. Q3：两项原生代价控制

主网格统一以Q2*无分流配对，默认pull、p=.5、cap=.25、速度1、种子20261017、τ=0。AH=A100/H800，AHS额外含匿名series-2，AHA额外含A800。对每个donor都要求原生平均等待增量≤0.5小时、边际P95增量≤2小时；主推荐优先仅命名型号，匿名组合单独选。敏感性不反过来重选推荐。

`D_max`在进入可拉取集合前过滤原时长过长者，以免前256个长作业遮住可拉取短作业；这些作业仍保持兼容标志。`R`要求每次迁入启动后池级空闲GPU及CPU至少保留R比例，原生作业随后可使用余量；逐节点可放置和迁入双cap仍同时检查。

{table(['主配置','总体均值/h','A10均值减少/h','最大等待/h','最大donor均值增量/h','最大donor P95增量/h','保护标准'], main_rows)}

无控制AH均值仅1.361小时，但H800原生均值/P95增加0.857/5.836小时，违反标准；AHS无控制也失败。R=.1/.2单独使用时，最大原生均值增量分别约1.291/.991小时、P95增量8.028/6.460小时，仍不合格，且不能解释成保留越多一定越安全：不可抢占作业的启动时机改变了后续竞争。

限制原时长≤1天可以避免本样本的donor等待增加，但总体均值仍约30.052小时；放宽到≤7天将均值降至26.368小时，仍满足保护标准。主推荐与加R=.1、扩展匿名/A800的7天版本核心指标相同，按预设名称规则选`{primary}`。1天方案将借用cap提高到.5也未改善指标，表明这组参数下借用上限并非当前收益的决定因素。

![v2收益与原生代价](../outputs/question3/v2/figures/05_benefit_vs_native_cost.png)

## 5. 推荐配置的收益、个体代价与资源含义

主推荐最后完工{selected.last_completion_days:.3f}天，最大等待{selected.wait_hours_max:.3f}小时，90天完成{int(selected.completed_at_90d):,}个作业，比Q2*多{int(selected.completed_at_90d-base.completed_at_90d):,}个。迁移{int(selected.migrated_jobs):,}个作业，占原A10数量{100*selected.a10_migrated_job_fraction:.2f}%，却只占原A10 GPU工作量{100*selected.a10_migrated_original_work_fraction:.4f}%。兼容A10为{int(selected.a10_flexible_jobs):,}个，满足原时长上限者{int(selected.a10_duration_eligible_jobs):,}个；资格、时长准入和实际迁移是三个不同计数。

实际迁入目标分布为：{', '.join(f'{model}：{count:,}' for model,count in destinations.items())}。配置允许的目标集合不等于每个目标都实际承担了迁入；额外型号没有自动带来收益。

全部donor原生作业的配对等待在本样本中不变，但原A10有{100*pairs.loc['A10','wait_increased_fraction']:.2f}%等待增加，最大增加{pairs.loc['A10','max_wait_increase_hours']:.3f}小时。因此“没有观察到donor受损”不能写成所有用户受益。配对正延迟分位数与两个总体等待分布的P95之差仍分别报告，详见[配对表](../outputs/question3/v2/{primary}/paired_wait_changes.csv)。

固定D={{A100,A800,H800,series-2}}的滞留匹配诊断由{base.gpu_stranded_upper_bound_hours_90d:,.3f}降至{selected.gpu_stranded_upper_bound_hours_90d:,.3f} GPU小时。该积分为 `∫min(Σ_D空闲GPU,A10排队GPU)dt`，是当前模拟时间线忽略兼容/CPU/装箱的匹配上界，不是可兑现收益或其他调度收益上界。分配率衡量模拟占用，并非GPU核心实测效率。

![Q2*与推荐配置的六池分配率及滞留诊断](../outputs/question3/v2/figures/06_allocation_imbalance.png)

## 6. 固定推荐后的敏感性与SJF桥接

下表前六项仍以Q2*无分流为基线，最后一项单独对SJF。p、种子、速度和τ只改变指定单因素，不将较有利的敏感性点替换主推荐。

{table(['敏感性','总体均值/h','A10均值减少/h','最大等待/h','最大donor均值增量/h','最大donor P95增量/h','保护标准'], sensitivity_rows)}

表中保留三位小数，0.000不一定是精确零：p=1的最大donor均值增量为{t3.loc['v2_p100','max_donor_mean_wait_delta_hours']:.3g}小时，虽很小但为正值。三种资格种子的总体均值为26.368–26.563小时；速度2在本例中未改变等待结果，τ=1小时则将实际迁入数降至2830。

SJF桥接与主推荐在两个本地策略下同时满足原生保护阈值：**{'是' if choice3['primary_and_sjf_bridge_both_feasible'] else '否'}**。这只能说明对这两种已测队列策略的表现，不能代替跨trace验证。全局平均等待仍须与SJF本身的16.568小时并列：Q2*加受保护分流的26.368小时不是均值最优方案，它继续承担了Q2*降低最大等待的目标。速度2仍是假设，不是硬件测量。

## 7. 证据、论文取舍与局限

全项目120项单元测试通过，涵盖对数排序、长队列过滤、CPU/GPU保留边界、同刻启动序、尾部手算、选择规则和基线配对。v2回归状态：**{regression_status}**；它覆盖19个历史完整CSV以及上一轮12个线性aging、15个Q3配置的新旧核心重放，逐字段及节点分配精确比较。新增关键完整场景当前独立审计：**{'通过' if audit_pass else '待完成'}**，本轮{len(current_audits)}个、{sum(row.get('comparisons',0) for row in current_audits):,}项比较。历史审计与本轮审计分开列示，metrics_only不算独立逐节点审计通过。

全部22个Q2候选、11个Q3主网格和7个敏感性/桥接使用完整作业轨迹。扫描只保存指标，完整新CSV只保存Q2*、AH/AHS无控制、命名推荐及匿名条件推荐（去重）。新旧源码与输入哈希、原始任务书和数据均保留；未提交Git或改写Notion。

建议论文围绕“容量不均衡—少数长作业代价—明确服务目标—受保护的有限分流”展开。统一价格数据缺失，p仅代表技术兼容且愿意参与的联合比例，不能推出真实最优价格、弹性或利润。所有型号替代仍是假设兼容；MIG、通信、移植成本、故障和时长误差未建模。GPU-series-1完整工作量/90天容量>1不等于窗口内持续超载。

本轮不再增加可选sjf_guard，优先完成验证与[10页论文提纲](paper_outline.md)。下一步是人工统稿、压缩表图和核对赛制；参数选择与敏感性均为同trace结果，不能写成样本外最优。

复现与证据：[README](../README.md)、[Q2对照](../outputs/question2/comparison_v2.csv)、[Q3对照](../outputs/question3/v2/comparison.csv)、[Q3选择](../outputs/question3/v2/selection.json)、[v2回归](../outputs/v2_regression.json)、[审计汇总](../outputs/scheduling_validation.json)。

AI记录：Claude撰写用户提供的v2任务书；Codex完成实现、计算、测试、图表和文字整理；参赛队负责最终题意、假设、数字与提交内容的人工确认及披露。
'''
    (ROOT / "reports/B_Q23_analysis_zh.md").write_text(text, encoding="utf-8")
    print(f"Wrote Chinese analysis: {chosen}, {primary}, {conditional}")


if __name__ == "__main__":
    main()
