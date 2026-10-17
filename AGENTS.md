# Project guidance

## Scope

- This repository supports the 2026 HKUST MCM competition. The current working problem is **Senior Group Problem B: Scheduling a GPU Cluster**.
- In the current discussion, "第一题" means **Problem B, Question 1 (understand the data)**. The user has now authorized completing **Questions 2 and 3** as well.
- The user also authorized implementing the aging/routing improvements in `TASK_aging_and_routing.md`. Keep that supplied guidance unchanged; apply the predeclared rules D018–D021 and preserve the 13 original schedules as regression references.
- The current v2 task follows `TASK_v2_selection_and_harm.md` and D023–D024. Preserve both supplied task files and all historical experiment outputs; prioritize log aging, paired tail analysis, named-donor harm controls, and the paper outline. The deadline is 2026-10-18 12:00 Asia/Shanghai.
- Read `CURRENT_STATUS.md` and `DECISIONS.md` before continuing work. Keep them current after meaningful implementation or analysis changes.
- The final experiment round is `TASK_v3_routing_frontier.md`, governed by D026. Preserve the frozen scheduling core and all historical artifacts; finish within its two-hour timebox, then freeze experiments for paper integration. The user authorized a supplement ZIP in a temporary directory outside the repository, excluding raw data and full schedules.

## Source data and evidence

- Preserve the supplied PDFs and CSVs unchanged. Put analysis code in `src/`, derived tables and figures in `outputs/`, and written analysis in `reports/`.
- Derive reported numbers from executable analysis. Record source filenames, file hashes, units, definitions, and commands needed to reproduce the outputs.
- Separate observed facts, modeling assumptions, and proposed experiments. Do not describe unrun simulations as results.
- Count a job's GPU work as `gpu_request * worker_num * duration`; CPU work follows the same rule. Requests are **per worker**.
- CPU requests may be fractional or zero. Do not round them to integers or divide by zero.
- Work attributed to a submission day includes the entire requested runtime. It is not resource usage on that day.
- Work divided by capacity and an observation window is a **workload-to-capacity ratio**, not measured utilization. Instantaneous offered load under immediate starts is also not a feasible scheduling result.
- The submission window does not bound job runtimes or the time needed to finish all submitted jobs. State time-window and censoring conventions explicitly.
- Preserve genuine long jobs and large jobs. Do not trim, winsorize, or delete them merely because they are extreme.

## Implementation and validation

- Prefer readable Python, deterministic calculations, and a small number of scripts. Do not introduce complex models before the baseline is correct.
- Use relative paths anchored to the repository, with CLI overrides when helpful. Do not hard-code a contributor's local runtime path in the analysis code.
- Read job and node identifiers as strings, including cached scheduling CSVs, so fresh and reused results join by the same identifier type.
- Validate identifiers, missing values, units, numerical domains, GPU-model compatibility, and empty-cluster job feasibility before interpreting results.
- Verify important totals independently and inspect final figures. Tests should target mathematical correctness and edge cases, not duplicate the implementation.
- Use English labels in report-ready figures. Explain findings and caveats in Chinese where useful; keep a concise English draft for the competition report.
- Do not change external Notion content unless requested for the current work. Do not commit, push, or submit competition files without a user request.
- Record AI assistance and human verification needs in the project status for the final competition disclosure.

## Engineering records

- `CURRENT_STATUS.md`: current scope, completed work, commands, validated outputs, open issues, and next actions.
- `DECISIONS.md`: dated decisions, their reasons, and consequences. Mark changes as superseding earlier decisions instead of silently rewriting history.
- Avoid recording credentials or personal data in project files.
