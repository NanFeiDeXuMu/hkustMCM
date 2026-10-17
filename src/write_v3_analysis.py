"""Update the Chinese final-round report from saved v3 evidence, without simulation."""
import json
from pathlib import Path
import re

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/"outputs/question3/v3"
REPORT = ROOT/"reports/B_Q23_analysis_zh.md"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def table(headers, rows):
    return "\n".join(["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"]*len(headers)) + " |"]
                     + ["| " + " | ".join(map(str, row)) + " |" for row in rows])


def number(value, places=3):
    return "未定义" if pd.isna(value) else f"{value:,.{places}f}"


def ratio(row):
    return "∞（正代价为0）" if row.exchange_ratio_infinite else number(row.exchange_ratio, 2)


def config(row):
    duration = "不限时长" if pd.isna(row.max_routed_duration_seconds) else f"原时长≤{row.max_routed_duration_seconds/86400:g}天"
    donors = " → ".join(row.donors.split(","))
    return f"{donors}；{duration}；τ={row.min_wait_seconds/3600:g}小时；cap={row.donor_cap_fraction:g}"


def main():
    selection = read(OUT/"selection.json")
    if not selection.get("completed"):
        raise ValueError("Finish the fixed grid and sensitivities before rendering the final report")
    results = pd.read_csv(OUT/"comparison.csv").set_index("scenario")
    strict_name, comp_name = selection["strict_selected_scenario"], selection["compensation_selected_scenario"]
    strict, comp = results.loc[strict_name], results.loc[comp_name]
    old = results.loc["v2_ah"]
    base = read(ROOT/"outputs/question2/q2_star/metrics.json")
    # Keep the previously reviewed frozen-Q2 analysis, replacing only its figure path.
    previous = REPORT.read_text(encoding="utf-8-sig")
    q2_text = previous.split("## 2. A10饱和与SJF的真实代价", 1)[1].split("## 4.", 1)[0]
    q2_text = q2_text.split("### 3.1 最终轮补充", 1)[0].rstrip()
    q2_text = ("## 2. A10饱和与SJF的真实代价" + q2_text).replace("figures_v2/04_policy_tradeoff", "figures_v3/04_policy_tradeoff")
    c2 = pd.read_csv(ROOT/"outputs/question2/c2_multiplier_sensitivity.csv")
    summary_rows, seen_summary = [], set()
    for label, name in [("严格推荐", strict_name), ("补偿推荐", comp_name), ("v2七天基准", "v2_ah_d7"), ("AH不限时长对照", "v2_ah")]:
        if name in seen_summary:
            continue
        seen_summary.add(name)
        row = results.loc[name]
        summary_rows.append([label, name, number(row.wait_hours_mean), number(row.wait_hours_max), number(row.last_completion_days),
                             number(row.max_donor_mean_wait_delta_hours), number(row.max_donor_p95_delta_hours),
                             f"{100*row.max_donor_native_wait_increased_fraction:.2f}%"])
    ledger_rows, harm_rows, peak_rows, seen_ledger = [], [], [], set()
    for label, name in [("严格推荐", strict_name), ("补偿推荐", comp_name), ("AH不限时长对照", "v2_ah")]:
        if name in seen_ledger:
            continue
        seen_ledger.add(name)
        row = results.loc[name]
        ledger_rows.append([label, number(row.a10_job_hours_saved, 1), number(row.donor_native_job_hours_added, 1), ratio(row),
                            number(row.a10_gpu_wait_hours_saved, 1), number(row.compensation_gpu_hours, 1),
                            "未定义" if pd.isna(row.compensation_share) else f"{100*row.compensation_share:.3f}%"])
        path = ROOT/row.output_path
        for donor in pd.read_csv(path/"native_harm_by_donor.csv").itertuples():
            harm_rows.append([label, donor.donor_model, donor.native_wait_increased_jobs,
                              f"{100*donor.native_wait_increased_fraction:.3f}%", number(donor.native_wait_mean_delta_hours), number(donor.native_wait_p95_delta_hours), number(donor.native_max_wait_increase_hours)])
        if label != "AH不限时长对照":
            for donor in pd.read_csv(path/"borrowing_peaks.csv").itertuples():
                peak_rows.append([label, donor.donor_model, donor.migrated_jobs, donor.peak_borrowed_gpus,
                                  number(donor.peak_borrowed_cpus), f"{100*donor.peak_borrowed_gpu_fraction:.2f}%", f"{100*donor.peak_borrowed_cpu_fraction:.2f}%"])
    main_rows = []
    for name, row in results[results.phase == "main"].iterrows():
        main_rows.append([name, number(row.a10_mean_wait_gain_hours), number(row.wait_hours_mean),
                          number(row.max_donor_mean_wait_delta_hours), number(row.max_donor_p95_delta_hours),
                          f"{100*row.max_donor_native_wait_increased_fraction:.2f}%", "是" if row.strict_feasible else "否", "是" if row.compensation_feasible else "否"])
    sensitivity_rows = []
    for name, row in results[results.phase == "sensitivity"].iterrows():
        sensitivity_rows.append([name, row.sensitivity_anchor, number(row.wait_hours_mean), number(row.a10_mean_wait_gain_hours),
                                 number(row.max_donor_mean_wait_delta_hours), f"{100*row.max_donor_native_wait_increased_fraction:.2f}%",
                                 "是" if row.strict_feasible else "否", "是" if row.compensation_feasible else "否"])
    strict_sensitivity = results[(results.phase == "sensitivity") & (results.sensitivity_anchor == strict_name)]
    comp_sensitivity = results[(results.phase == "sensitivity") & (results.sensitivity_anchor == comp_name)]
    strict_failures = ", ".join(strict_sensitivity[~strict_sensitivity.strict_feasible].index) or "无"
    comp_failures = ", ".join(comp_sensitivity[~comp_sensitivity.compensation_feasible].index) or "无"
    test_log = (ROOT/"outputs/v3_unit_tests.txt").read_text(encoding="utf-8-sig")
    test_count = int(re.search(r"Ran (\d+) tests", test_log).group(1))
    audit_path = ROOT/"outputs/v3_scheduling_validation.json"
    audit_text = "独立审计尚未完成，不作通过声明。"
    if audit_path.exists():
        audit = read(audit_path)
        audit_text = "最终推荐完整调度独立审计" + ("全部通过" if audit.get("passed") else "尚未通过") + "，计数与逐场景证据见审计汇总。"
    same = "两种推荐相同。" if strict_name == comp_name else "两种推荐不同，应按管理目标分别呈现。"
    content = f'''# B题第2、3问：最终分流权衡与补偿方案

更新日期：2026-10-17。最后一轮按 `TASK_v3_routing_frontier.md` 和运行前登记的[D026](../DECISIONS.md)执行；Q2*与调度核心保持不变，实验完成后冻结。[英文稿](B_Q23_report_en.md)、[10页提纲](paper_outline.md)、[方法第12节](B_Q23_methods_zh.md)和[提交包说明](../SUBMISSION_PACKAGE.md)供统稿使用。

## 1. 最终结论

Q2*仍为对数aging T=7天：总体平均等待31.190小时、最大1514.551小时。相对SJF最大等待下降49.80%，但P99等待从366.313升至1097.589小时，不能写成全面改善。

**严格推荐：`{strict_name}`**，{config(strict)}。总体平均等待降至{strict.wait_hours_mean:.3f}小时，较Q2*减少{100*(1-strict.wait_hours_mean/base['wait_hours_mean']):.2f}%；所有donor分别满足原生均值增量≤0.5小时、边际P95增量≤2小时。

**需管理层采用补偿机制的推荐：`{comp_name}`**，{config(comp)}。总体平均等待{comp.wait_hours_mean:.3f}小时，交换比{ratio(comp)}，逐donor均值增量≤1小时且受损比例≤20%；积分补偿比C/S={100*comp.compensation_share:.3f}%。{same}两者均只用命名型号，匿名兼容不是建议的前提。

{q2_text}

### 3.1 最终轮补充

仅对原22个候选将C2改为1.5、2、3倍SJF均值，依次选择{', '.join(c2.selected_scenario)}，符合预期核对。新增T=10/14/21天点只用于画曲线，不纳入重选；Q2*=7天保持冻结。[倍数敏感性](../outputs/question2/c2_multiplier_sensitivity.csv)、[曲线补点](../outputs/question2/v3_curve_points/comparison.csv)。

## 4. Q3双推荐：约束与完整权衡

主实验均与同一Q2*无分流配对，默认p=.5、速度1、种子20261017。候选为11个v2主配置和12个v3主配置；严格与补偿分别最大化A10净平均等待下降，平局按配置名。敏感性不参与选择。补偿门槛为交换比≥100、每个donor均值增量≤1小时、每个donor受损比例≤20%；三个门槛及积分机制均是管理假设，运行后没有追改。

{table(['方案','配置','总体均值/h','最大等待/h','最后完工/天','最大donor均值增量/h','最大donor P95增量/h','最大donor受损比例'], summary_rows)}

新增12个配置的完整结果如下；严格、补偿可行性是各自多项条件共同判定，不能只看图中一条均值阈值线。

{table(['配置','A10均值下降/h','总体均值/h','最大donor均值增量/h','最大donor P95增量/h','最大donor受损比例','严格可行','补偿可行'], main_rows)}

![分流收益与原生代价前沿](../outputs/question3/v3/figures/05_routing_frontier.png)

D_max限制迁入作业占用目标池的时长，τ要求先在A10等待一段时间才允许拉取，cap限制同时借用资源，donor顺序决定先向谁尝试分流。这些控制都有资源放置和严格队首阻塞的反馈，效果不必单调；应依据上表和逐donor峰值判断。本次全部型号替代仍是假设兼容，不能由显存大小推出应用可移植性。

补齐中间区间后，D_max从7天放宽到60天，迁出的A10原始GPU工作量占比由{100*results.loc['v2_ah_d7','a10_migrated_original_work_fraction']:.3f}%升至{100*results.loc['v3_ah_d60','a10_migrated_original_work_fraction']:.3f}%；不限时长为{100*old.a10_migrated_original_work_fraction:.3f}%，说明迁入作业数量不能代替工作量比例。60天方案加τ=1小时后，仅分流{int(results.loc['v3_ah_d60_t1h','migrated_jobs']):,}个作业，保留{100*results.loc['v3_ah_d60_t1h','a10_mean_wait_gain_hours']/results.loc['v3_ah_d60','a10_mean_wait_gain_hours']:.2f}%的A10平均等待收益，但其总体均值仍高于τ=0。不限时长加τ反而使最大donor均值增量由{old.max_donor_mean_wait_delta_hours:.3f}升至{results.loc['v3_ah_t1h','max_donor_mean_wait_delta_hours']:.3f}小时，不能称等待门槛普遍更好。

H800优先的30天方案保留了与A100优先相同的A10平均收益，却令最大donor均值代价升至{results.loc['v3_ha_d30','max_donor_mean_wait_delta_hours']:.3f}小时；不限时长H800优先时达到{results.loc['v3_ha','max_donor_mean_wait_delta_hours']:.3f}小时。两者均被规则排除，原生代价对顺序非常敏感。

## 5. 正式延迟总账与积分补偿

令δ=新等待−基线等待（小时）。A10节省作业小时B=−Σ_A10 δ，取净值；donor代价H=Σ_native max(0,δ)，只累计受损者。交换比B/H与GPU加权积分账不是同一尺度。H=0时以空值和 `exchange_ratio_infinite=True`表示无穷，而非CSV字符串；推荐仍需B>0。

令每作业GPU数g=每worker GPU请求×worker数，补偿积分C=Σ_native max(0,δ)g，A10节省S=−Σ_A10 δg。C与S单位均为GPU小时；C/S只在S>0时定义。这是按延迟计量的可审计记账方案，不代表现金价格或真实支付意愿；若兑换为未来优先级，其反馈尚未仿真，不能宣称完成了真实效用补偿。

{table(['方案','A10净节省/作业小时','donor正代价/作业小时','交换比','S/GPU小时','C/GPU小时','C/S'], ledger_rows)}

不限时长AH的正式交换比为{old.exchange_ratio:.3f}，替代此前“约363”的粗算。逐donor受损人数与比例采用严格δ>0定义：

{table(['方案','donor','受损原生作业数','受损比例','原生均值增量/h','原生边际P95增量/h','单作业最大增量/h'], harm_rows)}

![双推荐的作业小时总账与积分比例](../outputs/question3/v3/figures/07_compensation.png)

严格推荐仍使{100*strict.a10_wait_increased_fraction:.3f}%的A10作业等待增加，最大增加{strict.a10_max_wait_increase_hours:.3f}小时；补偿推荐对应{100*comp.a10_wait_increased_fraction:.3f}%和{comp.a10_max_wait_increase_hours:.3f}小时。移走部分作业会改变严格队首阻塞及资源装箱顺序，因此源池个体改善不具有单调保证；donor补偿账也不覆盖这些A10内部损失。

## 6. 实际承接者、参与率与敏感性

逐donor峰值覆盖整个仿真，按[start,end)同时占用重建；同刻先释放后启动，CPU以Decimal累加。GPU、CPU峰值可发生在不同时刻。

{table(['方案','donor','迁入数','GPU借用峰值','CPU借用峰值','GPU峰值/容量','CPU峰值/容量'], peak_rows)}

严格推荐的固定donor滞留匹配诊断为{strict.gpu_stranded_upper_bound_hours_90d:,.3f} GPU小时；补偿推荐为{comp.gpu_stranded_upper_bound_hours_90d:,.3f}，Q2*基线为{base['gpu_stranded_upper_bound_hours_90d']:,.3f}。它是已实现时间线上忽略兼容、CPU和装箱约束的匹配上界，不能作为另一调度可兑现收益的上界。

p解释为技术兼容且接受折扣、愿意换型号的联合比例。以下固定推荐，只改变种子或p；p=.5主点在第4节，p=.25/1用于比较参与率，不能据此反推价格弹性。三位小数显示为0.000的代价不一定精确为零，原始小数在CSV保留。

{table(['敏感性','固定推荐锚点','总体均值/h','A10均值下降/h','最大donor均值增量/h','最大donor受损比例','严格可行','补偿可行'], sensitivity_rows)}

严格门槛未通过的敏感性为：{strict_failures}；补偿门槛未通过的敏感性为：{comp_failures}。尤其补偿方案换种子后仍有很高的平均收益，却不再满足既定原生均值门槛，不能称为对资格集合稳健的默认方案。论文以严格推荐为主线，补偿结果保留为需要重新核对实际参与集合和管理条件的备选；不放宽阈值或用敏感性点替换已登记的主推荐。

既有速度2与SJF桥接保留为历史敏感性，后者继续只对SJF无分流比较，不进入本次前沿或推荐。敏感性中不满足原推荐门槛的点照实报告，不以更有利点重新选择。

## 7. 验证、冻结与人工统稿

全项目{test_count}项单元测试通过。调度核心哈希保持v2冻结值，继承此前208,704,795字段零差异的历史回归证据，本轮不冒称重新运行了全部旧回归。{audit_text}扫描场景仅有指标，不计为独立逐节点审计通过。[测试日志](../outputs/v3_unit_tests.txt)、[新推荐审计](../outputs/v3_scheduling_validation.json)、[最终交付核对](../outputs/v3_delivery_review.json)。

本轮为最终实验轮，现有结果是同trace样本内选择，不是全局最优或样本外保证。运行时长已知、零迁移开销、型号兼容和速度仍是假设，MIG、网络、故障与时长估计误差未建模。实验冻结后只做论文统稿、填写成员姓名、核对AI声明和排版PDF，不继续增扫参数。

正式机器可读数据：[对照表](../outputs/question3/v3/comparison.csv)、[双推荐记录](../outputs/question3/v3/selection.json)、[补偿总账](../outputs/question3/v3/compensation.csv)。补充包仅含代码、指标、图表及复现记录，不包含原数据或完整调度；未提交比赛系统、Git或改写Notion。

AI使用记录：Claude撰写用户提供的v3指导；Codex实现、计算、核验、绘图和整理文稿；参赛队负责最终题意、假设、成员信息、披露与提交格式的人工核验。
'''
    REPORT.write_text(content, encoding="utf-8")
    print(f"Wrote final Chinese report: {strict_name} / {comp_name}")


if __name__ == "__main__":
    main()
