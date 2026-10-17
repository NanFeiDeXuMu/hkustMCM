# HKUST MCM 2026 — Problem B

当前项目覆盖 **B 题三个问题**：工作负载分析、逐节点调度与放置、跨型号分流的情景实验。原始题面与数据保持不变。

## 阅读顺序

1. `CURRENT_STATUS.md`：交付内容、已验证结果和下一步。
2. `reports/B_Q1_analysis_zh.md`：中文完整解释、图表与指标口径。
3. `reports/B_Q1_report_en.md`：英文论文第 1 问草稿。
4. `DECISIONS.md`：时间窗口、统计口径等决策依据。
5. `reports/B_Q23_analysis_zh.md`：后两问的实验结果、取舍与建议。
6. `reports/B_Q23_report_en.md`：后两问英文论文草稿。
7. `reports/B_Q23_methods_zh.md`：后两问详细模型、事件顺序及预约证明。

## 复现

本次使用 Python 3.12，依赖见 `requirements.txt`。项目内 `.venv` 已准备好，可在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe src\analyze_question1.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tests\verify_question1_outputs.py
```

在其他电脑的标准 Python 环境中，可以先创建虚拟环境并安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

仅需重建数值表时可加 `--skip-figures`；正式更新交付结果时运行完整命令，使图表与数据一致。`--data-dir` 可指定输入目录，`--output-dir` 可指定输出目录。独立核验脚本检查默认比赛数据和默认输出路径。

## 第 2、3 问复现

最后一轮 **v3 实验**遵循D026：冻结Q2*与调度核心，计算分流交换比、积分补偿和命名型号双推荐。仅原v2主网格与12个v3主配置参与选择，敏感性与Q2曲线补点不重新选型。

```powershell
.\.venv\Scripts\python.exe src\run_v3_experiments.py --suite all
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
.\.venv\Scripts\python.exe src\plot_v3_results.py
.\.venv\Scripts\python.exe src\write_v3_analysis.py
.\.venv\Scripts\python.exe tests\verify_v3_outputs.py
```

本轮默认时间盒至2026-10-17 16:03北京时间；未来主动复现冻结网格时加 `--reproduction`。从不含完整调度的补充包复现时，先把原始两CSV放回原数据目录，然后运行 `src\run_v3_experiments.py --suite all --bootstrap-dependencies --reproduction`；脚本只重建缺失的FCFS/SJF/Q2*基线CSV，不需要调用已过旧时限的runner。提交包保留各场景清单和小指标，详见 `SUBMISSION_PACKAGE.md`。

输出为 `outputs/question3/v3/`；`comparison.csv` 与 `compensation.csv`含每配置总账，`selection.json`保存两种推荐和固定阈值。旧v2配置的新指标位于其下 `v2_reference/`，历史输出保持原样。逐场景 `native_harm_by_donor.csv` 和 `borrowing_peaks.csv`分别记录个体代价和实际借用峰值。交换比分母为零时CSV数值留空，`exchange_ratio_infinite=True`；C/S在S≤0时为空并标记未定义。Q2的 `c2_multiplier_sensitivity.csv`仅用原22候选，`v3_curve_points/`的3个补点只用于图形展示。

历史 **v2 实验**（分组保护、对数aging、原生代价控制；以下为历史记录，应在独立副本运行，`write_v2_analysis.py`会改写当前中文稿）：

```powershell
.\.venv\Scripts\python.exe src\run_v2_experiments.py --suite all
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
.\.venv\Scripts\python.exe tests\verify_v2_regression.py
.\.venv\Scripts\python.exe tests\verify_all_schedules.py
.\.venv\Scripts\python.exe src\plot_v2_results.py
.\.venv\Scripts\python.exe src\write_v2_analysis.py
.\.venv\Scripts\python.exe tests\verify_v2_outputs.py
```

可用 `--suite q2` / `--suite q3` 分段运行；Q3需要已生成的 `selection_v2.json` 和Q2*。v2主规则先登记于D023/D024，全部22个Q2候选保存正式FCFS配对尾部/减速比；Q3含11个主网格配置、6个固定推荐配置的单因素敏感性及1个SJF桥接。主网格和敏感性使用Q2*无分流基线，桥接仅对SJF。新扫描始终只存指标，完整新CSV仅用于Q2*、AH/AHS无控制对照、命名主推荐和匿名条件推荐（去重）。

新目录：`outputs/question2/v2_candidates/`、`log_aging_sweep/`、`q2_star/`、`outputs/question3/v2/`。选择清单为 `outputs/question2/selection_v2.json` 和 `outputs/question3/v2/selection.json`；候选对照为 `outputs/question2/comparison_v2.csv` 与 `outputs/question3/v2/comparison.csv`。正式尾部数据在各候选的 `tail_metrics.json`、`tail_by_duration_group.csv`、`tail_cohorts.csv`、`tail_model_distribution.csv`。超长迁入过滤不改变兼容标志；`a10_duration_eligible_jobs` 是兼容且满足时长界的作业数，不等于实际迁移数。

v2前源码快照保存在 `outputs/v2_reference/source/`。v2回归按原CSV检查19个历史完整场景，并用旧核心重放检查12个线性aging和15个上一轮Q3配置；精确比较全部调度字段及节点worker分配。历史场景输出不重写，审计汇总区分保留历史证据与当前新审计。历史manifest的源码哈希应匹配对应快照，不能将它们当作新版本源码哈希。

当前报告及10页统稿提纲：[中文分析](reports/B_Q23_analysis_zh.md)、[英文草稿](reports/B_Q23_report_en.md)、[论文提纲](reports/paper_outline.md)。新4组图单独保存在 `outputs/question2/figures_v2/` 和 `outputs/question3/v2/figures/`，保留旧图。

以下为 **v1 历史改进实验**（默认按当时源码记录），供了解旧工作流。当前不要用这些命令替代v2回归；新源码下重跑旧runner会重建历史场景，旧交付检查中的源码哈希也不应与当前版本直接比较：

```powershell
.\.venv\Scripts\python.exe src\run_scheduling_experiments.py --suite optimization --save-schedules key
.\.venv\Scripts\python.exe tests\verify_legacy_regression.py
.\.venv\Scripts\python.exe tests\verify_optimization_outputs.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
.\.venv\Scripts\python.exe tests\verify_all_schedules.py
.\.venv\Scripts\python.exe src\plot_optimization_results.py
```

新默认 suite 为 `optimization`；也可分别运行 `--suite aging` 和 `--suite routing`（后者需要 aging_selection）。选择规则先登记于 DECISIONS D018–D021。12 个 α 完整扫描后，以选定 aging 为基线运行 15 个分流配置；SJF 桥接单独使用 SJF 基线。`--save-schedules key` 保存 aging_best、四个主配置及被选中的额外配置，`all` 保存所有新仿真的完整调度，`none` 不新写大 CSV；已存在的完整结果始终保留。指标扫描的 `scenario.json` 明确标注 `metrics_only`，独立审计将其列为跳过，不能据此声称通过逐节点审计。

新增结果位于 `outputs/question2/aging_sweep/`、`outputs/question2/aging_best/`、`outputs/question3/improved/`，参数选择与邻点敏感性另存 JSON/CSV。`outputs/question2/legacy_diagnostics/` 是从旧调度计算的新指标；旧 13 份调度、manifest 与独立审计不重写。新旧核心的逐作业、逐节点精确回归见 `outputs/legacy_regression.json`。

以下是原始 13 场景的历史复现命令；会重建旧目录，不应用来执行当前优化或替代保留参考文件的回归检查：

```powershell
.\.venv\Scripts\python.exe src\run_scheduling_experiments.py --suite all
.\.venv\Scripts\python.exe tests\verify_all_schedules.py
.\.venv\Scripts\python.exe src\plot_scheduling_results.py
```

`--suite question2` 或 `--suite question3` 可分开运行，Q3 需要先存在对应的 Q2 基线；修改数据或模型后先重跑 Q2，或直接运行 `--suite all`。也可使用 `--scenarios fcfs sjf` 等指定场景；`--force` 强制重建。只有输入、代码和配置哈希一致时才复用已有结果。全量原始作业每个场景均调度至完成，没有在第 90 天截断模拟。读取已保存结果时作业 ID 始终按字符串处理，使单独运行与缓存复用保持一致。

第 2 问比较 FCFS、严格 SJF、保护队首回填（扫描 128 与 512 个候选）；第 3 问主敏感性采用 SJF，改变兼容比例、假设加速倍数、迁入双资源上限及资格种子，另有一个回填分流对照。共 13 个场景。回填分流与无分流回填配对，其余分流场景与无分流 SJF 配对。

每个场景目录包含 `schedule.csv`（每作业启动/完成/型号）、`allocations.csv`（每作业在各节点上的 worker 数）、`scenario.json`（参数/哈希/运行诊断）、`metrics.json`、分组指标，以及独立核验结果。Q3 另有按同一原始作业比较的 `paired_wait_changes.csv`。时间和 CPU 小数使用默认最短可往返的浮点文本输出，避免固定有效位数引入截断或冗余尾差。

独立核验只读原始输入和保存的安排，不导入调度实现。它逐节点重建半开区间事件，以 Decimal 检查任意时刻 CPU/GPU 容量、全部 worker 数、型号/时长、迁移资格和双资源借用上限。它证明输出满足这些约束，**不证明算法最优**。所有场景的总核验结果在 `outputs/scheduling_validation.json`。

## 交付目录

| 路径 | 内容 |
|---|---|
| `src/analyze_question1.py` | 数据校验、描述统计、容量压力、事件积分与绘图 |
| `tests/test_question1.py` | 人工可计算的资源碎片、零CPU、小数CPU、事件边界和集中度测试 |
| `tests/verify_question1_outputs.py` | 独立标准库/Decimal复算，不调用主分析函数 |
| `outputs/question1/tables/` | 11张机器可读CSV表 |
| `outputs/question1/figures/` | 5组英文图表，每组PNG与SVG版本 |
| `outputs/question1/summary.json` | 主要数值结论 |
| `outputs/question1/validation.json` | 数据完整性与汇总守恒检查 |
| `outputs/question1/provenance.json` | 原始文件哈希、软件版本和指标定义 |
| `outputs/question1/independent_validation.json` | 独立核验结果 |
| `reports/` | 中文分析及英文草稿 |
| `src/scheduler.py` | 在线、非抢占、全部 worker 同时启动的调度核心 |
| `src/run_scheduling_experiments.py` / `src/run_optimization_experiments.py` | 原始 13 场景及新 aging/pull 网格、预登记选择与来源记录 |
| `src/run_v2_experiments.py` | v2候选、分组约束选择、命名donor优先及固定配置敏感性 |
| `src/analyze_tail.py` | 严格ID配对的FCFS受损集合、工作量占比、尾部构成和减速比 |
| `src/plot_v2_results.py` | v2四组英文PNG/SVG图 |
| `tests/verify_v2_regression.py` | 历史完整CSV与上一轮核心重放的精确兼容检查 |
| `src/plot_optimization_results.py` | 新增 4 组 aging、公平性及跨池不均衡 PNG/SVG 图 |
| `src/evaluate_scheduling.py` | 固定窗口和最终完成指标、原生作业配对代价 |
| `src/plot_scheduling_results.py` | 第 2、3 问的 6 组 PNG/SVG 图 |
| `outputs/question2/comparison.csv` | 4 个调度配置的核心指标 |
| `outputs/question3/comparison.csv` | 9 个分流配置的核心指标 |
| `tests/verify_schedules.py` | 独立核验单个完整场景 |
| `tests/verify_all_schedules.py` | 批量核验所有已保存场景 |

## 表格口径

- `model_summary.csv`：型号容量、请求工作量、作业特征、空池可行性、立即启动假设下的窗口诊断。
- `distribution_summary.csv`：每个变量的均值、标准差和分位数；变量名在 `metric` 列。这里 `std` 是 pandas 描述统计的样本标准差；日变化CV另按总体标准差计算。
- `duration_band.csv`、`gpu_size_band.csv`：时长及整作业GPU数量分组，时长区间左开右闭。
- `daily_submissions.csv`、`daily_by_model.csv`：按提交日归属整个作业的完整工作量，日编号从0开始。
- `weekly_submissions.csv`：每7天一组，最后一组只有6天，跨组比较使用日均值；这里不代表真实日历周。
- `offered_load_daily.csv`：每个作业假设提交即启动，按事件间隔积分得到的每日资源请求。它不施加容量和放置约束。
- `concentration.csv`：按完整GPU工作量降序，取 `ceil(比例 × 作业数)` 个作业；同时报告实际选中比例。
- `request_profiles.csv`：按型号、每worker GPU/CPU请求和worker数分组的作业配置频次。
- `node_configurations.csv`：原始节点配置与台数；不使用统一节点配置替代异质节点。

## 必须保留的解释边界

1. 每个作业的资源数为每worker请求乘worker数；CPU可以是小数或零。
2. 按提交日汇总的完整工作量不等于当天执行量。
3. 默认观察窗口为 `[0, 90×86400)`，包含最后提交时刻后的122秒。精确最后提交时刻和两种压力口径均保留。
4. 完整工作量/窗口容量、立即启动的窗口需求与实际调度利用率是不同指标。Q1 不包含调度等待；Q2/Q3 的等待和分配率来自明确假设下的模拟。
5. 第0天及时间零的集中记录完整保留；不推断其未提供的来源或日历周期。
6. 图5展示日平均需求，可能掩盖短时峰值；瞬时峰值和超容量时间比例在型号汇总表中。
7. 长作业与高工作量作业保留，不任意删除或截断。
8. Q2/Q3 的分配率来自模拟中实际安排的资源积分，仍不等于硬件实测利用率。最终完成指标包含所有提交的作业，90 天指标单独计算。
9. Q3 的兼容率和速度均为声明的假设；保持 worker/GPU/CPU 数量，以 `duration/speedup` 换算时间。不得从这些假设推断真实硬件速度或显存兼容性。
10. 目标池原生作业优先及双资源借用上限不能保证其零损失。原版 arrival 只尝试新到达 A10；新版 pull 在事件时持续扫描等待资格作业，设最小等待时有独立资格事件。按原始 job_name 配对报告受损者；边际 P95 差与逐作业正延迟 P95 分别列示。
11. SJF 的平均等待优势不等于对所有作业或所有公平性指标都最好。完整论文需说明主要目标及最长等待、长作业尾部和最后完工时间的代价。
12. 新增滞留积分固定 donor 集合为 A100/A800/H800/GPU-series-2，是当前调度时间线上忽略兼容、CPU 和装箱的潜力诊断，不能直接解释为可兑现收益。匿名型号兼容性是假设，另列仅已命名型号的建议。
13. v2的C1保护时长组均值，不保证个体不受损。对数aging的T控制时长比与提交间隔的权衡；已同时在队列中的作业不会仅因时钟流逝交换优先级。所有参数仍在同一trace上选择。
14. donor预留GPU/CPU只在迁入启动后检查，后来原生作业可使用余量；时长上限按原始时长过滤，速度敏感性不改变这个资格集合。两项机制仍不保证未来原生作业零损失。

`AGENTS.md` 约定后续协作方式。原始数据属于比赛资料；论文需遵守题面引用、AI披露和提交要求。
