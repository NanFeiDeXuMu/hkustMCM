# 补充材料包与复现说明

本包是 HKUST MCM Senior Group Problem B 的代码、派生结果和核验证据补充材料。它不是最终比赛论文，也没有上传到比赛系统。实验已完成后的 ZIP 应在仓库外系统临时目录生成，大小严格小于 **50,000,000 字节**；论文统稿仍需补齐成员信息、人工核对 AI 声明并排版为 PDF。

## v4追加材料（2026-10-20）

用户重新开放的D028实验单独位于 `outputs/v4_validation/`，包含预登记协议、39次开发、30次新资格验证、2次速度压力、A10受损快照与新核验记录；报告为 `reports/B_v4_validation_zh.md` 和 `reports/B_v4_validation_en.md`。这些是追加证据，不覆盖下文v3历史交付。

精简包继续排除全部完整schedule/allocations CSV。取得原始输入后可运行：

```powershell
.\.venv\Scripts\python.exe src\reproduce_v4_full.py
```

它在 `outputs/v4_reproduction/<时间>/` 重建Q2*基线并核对冻结哈希，再重建三个锁定方案的首个验证种子，核对各10个数值。按该目录 `reproduction_check.json` 的cases路径逐一运行 `tests/verify_schedules.py --scenario-dir <路径>`。它有单独三小时复现预算，不重新选型，不覆盖原输出；该入口不重建71次全网格，也不重建A10机制分析所需的原v3完整调度。已有复现试运行的full CSV同样排除，报告保留。

`tests/verify_v4_outputs.py` 适用于原完整工作目录，核对全部历史哈希、选择、通过率、指标和新完整调度，不能把精简包中缺失的大文件标为通过。当前实验轮与独立复现试运行的核验数分开报告。

## 包含与排除

- 包含 `src/`、`tests/`、`requirements.txt`、`README.md`、`AGENTS.md`、`CURRENT_STATUS.md`、`DECISIONS.md`、本说明和三份 `TASK*.md` 任务书。
- 包含 `reports/` 中的分析稿、英文稿、方法和论文提纲，以及 `outputs/` 的小型派生 CSV/JSON、英文 PNG/SVG 图、单元测试日志、独立核验与历史回归证据、历史源码快照。特别保留各版本全部 `comparison*.csv`、`selection*.json`、`scenario.json`、`metrics*.json`/分组指标、`compensation.csv` 和 `c2_multiplier_sensitivity.csv`。
- **排除原始 PDF 和两份原始 CSV、所有层级的 `schedule.csv`/`allocations.csv`、虚拟环境、缓存、Git 目录及 ZIP 文件。** 包中的历史 full 清单和审计报告仍说明原始实验保存过完整安排；它们不表示本 ZIP 也附带这些大文件。
- 内嵌 `PACKAGE_MANIFEST.json` 逐项记录文件路径、字节数和 SHA256。历史证据记录的是当时已通过的检查；只有原始输入与完整调度再次具备时，才能重新执行逐节点独立审计。

## 打包与校验

在项目根目录运行；先完成报告和工程记录的最终更新，再构建最终 ZIP：

```powershell
.\.venv\Scripts\python.exe src\build_submission_package.py --dry-run
.\.venv\Scripts\python.exe src\build_submission_package.py
```

脚本默认输出 `$env:TEMP\hkustMCM_submission\hkustMCM_supplement_<UTC时间>.zip`，旁边保存 `.verification.json`。它拒绝覆盖已有 ZIP，也拒绝写入仓库或系统临时目录之外。它逐个读取归档成员，核对 SHA256、字节数、CRC、禁入文件和大小，并检查打包期间源文件未变化。

可以指定一个尚不存在的临时路径，并在移动 ZIP 后再次验证内容：

```powershell
.\.venv\Scripts\python.exe src\build_submission_package.py --output (Join-Path $env:TEMP 'hkustMCM_supplement_final.zip')
.\.venv\Scripts\python.exe src\build_submission_package.py --verify-only '<ZIP的完整路径>'
```

验证不需要原始数据或 pandas。输出中的 `archive_sha256` 用于确认整包内容。成员清单检查保护包内自洽性；如需确认来源，还应将整包 SHA256 与交付时给出的值比较。

## 从补充包复现

1. 在独立目录解压，保留交付 ZIP 作为不可变参考。推荐 Python 3.12，创建环境并安装锁定的三个依赖：

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
   ```

2. 从比赛原始资料另行取得两 CSV，放在以下目录；不要从报告里的派生表反推原始数据：

   ```text
   2026_MCM_Senior_GPU_Scheduling/2026_MCM_Senior_GPU_Scheduling/
       job_info_clean.csv
       node_info_df.csv
   ```

   | 输入文件 | SHA256 |
   |---|---|
   | `job_info_clean.csv` | `735bc06c3986b1d0c5932236f8685b7204463829118fc6a1d37030e253ed38e1` |
   | `node_info_df.csv` | `1aba161961a5a4a1a61aa581383c5e5abe3400b59f8597ba8c4eef7597bc9d18` |

   可使用 `Get-FileHash -Algorithm SHA256 <文件路径>` 核对。完整数据有 224,112 个作业、4,278 个节点。CPU 请求保留零与小数；资源请求均按每 worker 解释。

3. 若只需要阅读和再绘图，包中已提供全部小表和原图，无需运行仿真。若需要重新执行最终固定实验，使用新 runner；它会先重建缺失的 FCFS、SJF 和 Q2* 完整 CSV，再使用保留的 v2 配置与最终 v3 网格：

   ```powershell
   .\.venv\Scripts\python.exe src\run_v3_experiments.py --suite all --bootstrap-dependencies --reproduction --force
   .\.venv\Scripts\python.exe src\plot_v3_results.py
   .\.venv\Scripts\python.exe src\write_v3_analysis.py
   ```

   `--reproduction` 明确开启一次新的两小时复现预算，不改变冻结的实验网格和选择规则。`--force` 重算结果，防止把包内已有小表的缓存读取误称为重新仿真；省略它可只重建缺失的推荐完整安排并复用哈希一致的指标。请在解压副本执行，避免覆盖交付目录的小表证据。

4. 对最终推荐的完整安排执行独立审计。`outputs/question3/v3/selection.json` 中的 `selected_output_paths` 给出确切目录（两推荐相同时去重）；每个目录执行：

   ```powershell
   $selectedPaths = (Get-Content outputs\question3\v3\selection.json -Raw | ConvertFrom-Json).selected_output_paths.PSObject.Properties.Value | Sort-Object -Unique
   foreach ($scenarioPath in $selectedPaths) {
       .\.venv\Scripts\python.exe tests\verify_schedules.py --scenario-dir $scenarioPath
       if ($LASTEXITCODE -ne 0) { throw "Schedule audit failed: $scenarioPath" }
   }
   ```

   该核验器不导入调度实现，以标准库 CSV 和 Decimal 核验 224,112 作业、worker 数、原始 CPU/GPU 请求、逐节点同时容量、迁移资格/时长/并发上限、启动时资源余量与资源工作量积分守恒。它证明输出满足所检查的约束，不证明最优性。补偿积分与配对等待总账另由原完整目录的 `verify_v3_outputs.py` 验证；两种核验的范围不同。

5. 第 1 问可独立重算：

   ```powershell
   .\.venv\Scripts\python.exe src\analyze_question1.py
   .\.venv\Scripts\python.exe tests\verify_question1_outputs.py
   ```

## 历史证据与复现边界

`outputs/v2_regression.json` 保存 41 个历史目录、46 组比较、208,704,795 个字段的零差异回归证据；本轮调度核心 SHA256 固定为 `714e94bdc388abd63a9274e67c394157eb6e06bfb5a6ffba0a8a0320acb10966`。当前 v3 继承该证据时，另外核对当前核心、旧核心、原始输入和历史产物哈希，不把历史结果冒充本轮重跑。

精简包没有附上 24 个历史 full 场景的全部调度 CSV，所以在刚解压的目录直接执行历史批量/回归验收会因缺少大文件失败，这是明确的依赖缺失。最终 runner 的 bootstrap 只补齐当前复现所需的基线；它不重建全部历史 41 个目录。历史完整回归若需重新执行，应另行提供保存的历史 CSV，或在独立复现目录按对应版本源码重建后再比较。不要为使核验通过而删除历史 full 声明、修改历史哈希或将缺失安排标作“通过”。

`tests/verify_v3_outputs.py` 是**原完整工作目录**的交付验收入口，包含历史全部文件的哈希认证，不是精简包的独立复现命令。精简包重建后的当前推荐使用上面第4步验证；原完整目录首次运行 `python tests/verify_v3_outputs.py --audit-schedules`，后续 `python tests/verify_v3_outputs.py` 认证已保存的新审计。其输出分别为 `outputs/v3_scheduling_validation.json` 和 `outputs/v3_delivery_review.json`，不会覆盖 v2 汇总。

最终推荐仍是同一 trace 上、在假设兼容资格与速度下的样本内选择。延迟积分不是实际价格，也没有模拟积分在未来兑现为优先级后的反馈。所有论文结论需保留这些解释边界。
