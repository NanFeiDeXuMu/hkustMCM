# Ten-Page English Paper Outline

Working title: **Balancing Response Time and Extreme Delays in a Heterogeneous GPU Cluster**

Prepared 17 October 2026. This is a layout and evidence plan for human integration, not an additional experiment. Use fixed Q2* and the final v3 dual recommendation; retain α=100 only as an alternative under stricter maximum-wait and completion caps. All reported waiting distributions include every submitted job, while allocation and completion counts explicitly use the 90-day window.

## Page budget

| Section | Budget | Suggested placement | Main purpose |
|---|---:|---|---|
| Abstract | 0.5 page | Page 1, upper half | State the bottleneck, queue trade-off, and two routing choices |
| Q1: Data and workload characterization | 2 pages | Page 1 lower half through Page 3 upper half | Explain demand concentration, model mismatch, and window definitions |
| Q2: Queue rules, tail costs, and selection | 3 pages | Page 3 lower half through Page 6 upper half | Establish feasibility and justify Q2* under C1/C2 |
| Q3: Controlled cross-model routing | 3 pages | Page 6 lower half through Page 9 upper half | Compare donor harm and distinguish strict service from compensation |
| Limitations, conclusion, and references | 1 page | Page 9 lower half through Page 10 upper half | Bound the claims and report validation evidence |
| AI assistance and human verification | 0.5 page | Page 10, lower half | Provide a factual disclosure |
| **Total** | **10 pages** | | |

Treat these as typesetting budgets: equations, captions, tables, references, and section headings must fit within them. Prefer six figures total: two Q1 figures, two Q2 figures, and two Q3 figures. Avoid reproducing the full experiment grid in the paper; link its CSV in the reproducibility material.

## Abstract — 0.5 page

Write approximately 140–180 words after the body is finalized. Suggested sequence: node-level online model → A10 saturation and long-work concentration → SJF's average benefit and concentrated harm → Q2*'s explicit tail trade-off → strict routing and the conditional compensation alternative.

Use no more than five headline values:

- 224,112 jobs and 10,412 GPUs; jobs longer than seven days contribute 76.95% of original GPU work. Source: [Q1 summary](../outputs/question1/summary.json) and [duration-group table](../outputs/question1/tables/duration_band.csv).
- Q2*: log aging T=7 days, mean 31.190 h and maximum 1,514.551 h; SJF maximum is 3,017.288 h. Source: [Q2 comparison](../outputs/question2/comparison_v2.csv).
- Strict routing with a 60-day duration limit lowers overall mean to 6.818 h; unrestricted compensation routing reaches 1.361 h with a 363.464:1 job-hour exchange. Qualify that compensation fails its mean-cost bound under both alternate seeds. Sources: [final comparison](../outputs/question3/v3/comparison.csv), [selection](../outputs/question3/v3/selection.json), and [formal ledger](../outputs/question3/v3/compensation.csv).

Avoid describing Q2* as universally best, strict service as zero harm, or compensation as robust across eligibility sets. Anonymous models are not needed for either recommendation.

## Q1 — 2 pages

### Data, units, and validity — approximately 0.4 page

Define per-worker GPU/CPU requests and simultaneous worker count. Present `original GPU work = gpu_request × worker_num × original_duration`. State 4,278 nodes, 632,636 vCPUs, six GPU models, preserved fractional/zero CPU requests, and successful empty-pool feasibility checks. Source: [summary](../outputs/question1/summary.json), [validation](../outputs/question1/validation.json), and [provenance](../outputs/question1/provenance.json).

### Concentration and model-specific mismatch — approximately 1.2 pages

Explain that large *work* often means long duration, not many concurrent GPUs. The largest 1% of jobs by GPU work contribute 43.31%; >7-day jobs are 6.00% of jobs but 76.95% of work. A10 has 2,494 GPUs and 111,284 jobs, with full-work/90-day supply ratio 2.064. Its requested CPU/GPU ratio differs sharply from provisioned capacity, motivating joint resource placement. Sources: [concentration](../outputs/question1/tables/concentration.csv) and [model summary, including CPU/GPU balance](../outputs/question1/tables/model_summary.csv).

Figures, with English captions and a common column width:

1. [Job distributions](../outputs/question1/figures/02_job_distributions.png), vector source [SVG](../outputs/question1/figures/02_job_distributions.svg). Emphasize separate count and work shares.
2. [Model demand and capacity](../outputs/question1/figures/01_model_demand_capacity.png), [SVG](../outputs/question1/figures/01_model_demand_capacity.svg). Caption must call the ratios workload-to-capacity ratios, not measured utilization.

### Temporal boundary — approximately 0.4 page

Time-zero submissions are 3,512 jobs and 23.07% of work, with unknown origin. Even hypothetical immediate starts place 26.56% of GPU work after day 90. A10's immediate-start window-average demand/capacity is 1.449, while GPU-series-1's peak is only 0.953 despite its full-work ratio exceeding one. The strict-model fixed-capacity final-completion lower bound is 185.762 days, not an achieved schedule. Source: [summary](../outputs/question1/summary.json) and [model summary](../outputs/question1/tables/model_summary.csv). Summarize these in text; omit an additional time-series figure unless space remains.

## Q2 — 3 pages

### Feasible online scheduling — approximately 0.6 page

Introduce shared node placement and non-preemptive gang constraints. Explain release → arrivals → native dispatch ordering, deterministic ties, and strict head blocking. Define FCFS, SJF, and the protected-head backfill sufficient condition. Include the bounded-slowdown formula with its 60-second denominator floor. Reference the [method note](B_Q23_methods_zh.md), [scheduler](../src/scheduler.py), and [Slurm reservation reference](https://slurm.schedmd.com/sched_config.html).

A10 saturation explains why backfill has little local capacity to recover: 99.930% FCFS allocation, 1,529.609 GPU-hours idle during its own queues, conditional idle fraction 0.0285%. Source: [exact FCFS diagnostics](../outputs/question2/legacy_diagnostics/fcfs/metrics_idle_while_queued_by_model.csv). Do not claim saturation fixes window-completion counts: FCFS completes 164,886 jobs by day 90 versus SJF's 217,494.

### SJF harm and aging scale — approximately 0.7 page

Use a compact table contrasting FCFS, SJF, linear α=1, Q2*, and α=100; take all values from [comparison_v2.csv](../outputs/question2/comparison_v2.csv). Show mean, P99, maximum waiting, fixed-window completions, and last completion.

Pairing against FCFS finds 2,502 SJF-harmed jobs (1.1164%) with 14.8321% of original work and conditional mean harm 770.493 h. All 1,525 jobs waiting >1,000 h are A10 jobs with original durations 49.521–183.508 days. Sources: [SJF tail metrics](../outputs/question2/v2_candidates/sjf/tail_metrics.json), [cohort quantiles](../outputs/question2/v2_candidates/sjf/tail_cohorts.csv), and [model counts](../outputs/question2/v2_candidates/sjf/tail_model_distribution.csv).

Give linear `d+αa` and log `ln(d/1s)+a/T` priorities. One sentence explains cancellation of the common clock term. The arrival-gap comparison `T ln(d_long/d_short)` motivates the logarithmic scale without claiming two already-queued jobs exchange rank over time.

### Choice and unavoidable trade-offs — approximately 1.7 pages

State C1 (each original-duration group's mean no worse than FCFS), C2 (mean ≤ twice exact SJF mean), and the objective (minimum maximum wait, then mean, then name). D023 was fixed before the new runs but informed by earlier results; all selection is in-sample. Q2*=T7d has mean 31.190 h, maximum 1,514.551 h, and last completion 247.106 days. The earlier-only candidate check selects α=1. Source: [selection_v2.json](../outputs/question2/selection_v2.json).

Use both new figures:

3. [Policy trade-off](../outputs/question2/figures_v3/04_policy_tradeoff.png), [SVG](../outputs/question2/figures_v3/04_policy_tradeoff.svg). C2 shading does not substitute for checking C1. Open T=10/14/21-day points improve curve resolution only; they are excluded from Q2* selection. The α=1 label remains separate from the near-SJF small-α cluster.
4. [Group fairness and slowdown](../outputs/question2/figures_v2/06_group_fairness.png), [SVG](../outputs/question2/figures_v2/06_group_fairness.svg). The slowdown panel uses a log scale.

A short trade-off paragraph is mandatory: compared with SJF, maximum wait falls 49.80%, but P99 waiting rises 366.313→1,097.589 h and P99 slowdown 4.179→7.883. Counts >1,000 h rise 1,525→3,354; counts >FCFS maximum fall 1,199→159. Q2* harms 1.3440% of jobs, carrying 19.1825% of work, with conditional mean harm 118.594 h. Sources: [Q2* tail metrics](../outputs/question2/q2_star/tail_metrics.json) and [cohorts](../outputs/question2/q2_star/tail_cohorts.csv). Retain α=100 only as the earlier strict-cap alternative. One sensitivity sentence: using the original 22 candidates, C2 multipliers 1.5/2/3 select T=30/7/7 days; T30 has mean 19.833 h and maximum 2,187.630 h. Source: [C2 sensitivity](../outputs/question2/c2_multiplier_sensitivity.csv).

## Q3 — 3 pages

### Mechanism and two management rules — approximately 0.5 page

Define stable eligibility p=.5 as joint technical compatibility and willingness to accept substitution, one-to-one GPU mapping, speed=1, concurrent GPU/CPU borrowing caps .25, native-first dispatch, and pull scans of 256 eligible waiters. Original-runtime filtering precedes entry into the scanned set; τ sets minimum waiting. A reserve R protects current free capacity but not future arrivals. Sources: [D024/D026](../DECISIONS.md), [v3 runner](../src/run_v3_experiments.py), and [method note](B_Q23_methods_zh.md).

The 23 main candidates comprise 11 historical and 12 new configurations. Both recommendations require positive A10 benefit and named donors. Strict service checks each donor's native mean increase ≤.5 h and marginal P95 increase ≤2 h. Compensation checks exchange ≥100, each donor's mean increase ≤1 h, and each donor's harmed fraction ≤20%. Maximize A10 mean-wait gain, then use scenario-name ties. Thresholds remain fixed; sensitivities never reselect. P95 cost is a marginal-quantile difference, not the P95 of individual delay changes. Source: [selection.json](../outputs/question3/v3/selection.json).

### Frontier and final recommendations — approximately 1 page

Use a compact table: baseline, AH/D7, AH/D30, strict AH/D60, compensation AH/unrestricted. Source: [comparison.csv](../outputs/question3/v3/comparison.csv). Recommended table numbers and supporting details:

- Baseline: overall mean 31.190 h, A10 mean 62.365 h, maximum 1,514.551 h, completion 247.106 d, 215,229 jobs completed by day 90.
- **Strict: A100 then H800, Dmax=60d, τ=0, cap=.25.** Overall mean 6.818 h, A10 mean 13.235 h, maximum 788.598 h, completion 216.858 d, 217,617 day-90 completions. Worst donor mean/P95 increase .066991/0 h. A100/H800 harmed fractions 4.586%/.254%, pooled 4.347%; strict does not mean zero harm. Source: [strict accounts](../outputs/question3/v3/v3_ah_d60/frontier_metrics.json).
- **Compensation: same donors, no duration limit, τ=0, cap=.25.** Overall mean 1.361 h, A10 mean 2.129 h, maximum 494.451 h, completion 204.602 d, 218,279 day-90 completions. Worst native mean/P95 increase .856739/5.836111 h; fails strict limits but passes the declared compensation rule. A100/H800 harmed fractions 9.189%/17.409%, pooled 9.642%. Source: [compensation accounts](../outputs/question3/v3/v2_reference/v2_ah/frontier_metrics.json).
- A10 internal harmed fractions remain 27.295%/27.265%, with maximum increases 33.267/79.517 h. Source scheduling paths change under strict head blocking; no all-user improvement claim.

5. [Routing frontier](../outputs/question3/v3/figures/05_routing_frontier.png), [SVG](../outputs/question3/v3/figures/05_routing_frontier.svg). Six-item legend, family curves, two stars, and symlog native-mean costs. Horizontal lines are not complete feasibility tests.

Mechanism paragraph: widening D7→D60 increases original A10 work moved 1.2425%→16.0684%. Unrestricted routing moves 28.5373% despite fewer jobs (27,722 versus 47,810). D60 with τ=1h preserves 97.31% of its A10 benefit while reducing migrations to 4,977; benefit still falls, so it is not the chosen optimum under D026. A .10 cap passes strict limits, while .15 fails P95. With D30, reversing donor order keeps A10 benefit but raises worst native mean/P95 costs to 17.881/151.638 h; unrestricted H800-first yields 130.388/555.017 h and fails both rules. Sources: [main comparison](../outputs/question3/v3/comparison.csv), [strict borrowing peaks](../outputs/question3/v3/v3_ah_d60/borrowing_peaks.csv), and [compensation borrowing peaks](../outputs/question3/v3/v2_reference/v2_ah/borrowing_peaks.csv). Report peak GPU/CPU pairs if space permits: strict A100 864/12,298 and H800 189/2,584; compensation 864/13,824 and 438/9,153. Peaks need not be simultaneous. No separate anonymous recommendation is needed.

### Auditable compensation account — approximately 0.7 page

Define Δw=new−baseline and G=whole-job GPU request. Job-hour exchange is `sum_A10(-Δw) / sum_donor max(0,Δw)`; denominator zero uses an explicit infinity flag. Strict/compensation exchange ratios are 1,008.430:1/363.464:1. Corresponding net A10 savings are 5,467,397.862/6,703,307.366 job-hours and donor positive costs are 5,421.691/18,442.826 job-hours.

Define credits C=`sum_donor G×max(0,Δw)` and GPU-weighted net savings S=`sum_A10 G×(-Δw)`. C is 35,177.769/794,345.272 GPUh; S is 5,601,825.318/6,823,034.740 GPUh; C/S is .628%/11.642% (undefined if S≤0). These weighted accounts must not be confused with the unweighted exchange ratio. Source: [compensation.csv](../outputs/question3/v3/compensation.csv).

6. [Delay-credit accounts](../outputs/question3/v3/figures/07_compensation.png), [SVG](../outputs/question3/v3/figures/07_compensation.svg). Include net A10 job-hours, positive donor job-hours, and GPU-weighted C/S.

The proposed account is auditable but does not demonstrate true utility, fairness, payment, or actual willingness. Future credit redemption through discounts/priority and its scheduling feedback are not simulated. The compensation recommendation requires an explicit management decision.

### Sensitivity and remaining imbalance — approximately 0.8 page

This paragraph must remain in the main paper. Strict at p=.5 passes across three seeds (overall means 6.653–6.951 h); p=.25 passes with mean 16.758 h. At p=1 it has mean .928 h but native mean/P95 costs .886/5.278 h and maximum harmed fraction 23.664%, failing both rules. Compensation seeds 18/19 yield native mean increases 1.254/1.314 h, both above 1 h; p=1 gives 1.608 h and 27.905% harmed, also failing. Compensation p=.25 passes with mean 10.710 h. The strict scheme is the default at its specified participation level; compensation is a conditional same-trace alternative, not a robust deployment default. Source: [eight new sensitivity rows](../outputs/question3/v3/comparison.csv).

Briefly define exact `∫min(fixed-donor idle GPUs, queued A10 GPU requests)dt`. Fixed donors are A100/A800/H800/Series-2 regardless of routing configuration. The 90-day diagnostic decreases 3.681→1.327/.264 million GPUh for strict/compensation; total allocation rises 69.438%→76.268%/79.602%. Requests queue under the original model on [submission,start), including future migrants. The integral bounds matching on the realized timeline only, ignoring compatibility/CPU/packing; it does not bound another schedule's improvement. Keep this in text to stay within six figures.

## Limitations, conclusion, and references — 1 page

Conclusion: log aging T7d remains the selected C1/C2 compromise. AH/D60 is the strict-service recommendation; AH/unrestricted is a conditional compensation alternative. The latter's failure under alternate seeds materially limits generalization. Both use named models. Neither is globally optimal, and all choices depend on the declared management objectives.

Discuss same-trace tuning, known runtimes, assumed compatibility, no transfer/network/MIG/failure model, and no measured hardware utilization. Interpret p as compatibility plus willingness to substitute; do not infer prices, elasticities, profits, or actual utility from delay credits.

Validation paragraph must separate: 140 tests pass; two newly saved recommendations pass 11,701,433 independent Decimal checks plus 489 paired-account/history checks, with zero failures; 39 metric-only outputs are excluded from per-node audit claims. Twenty-four historical full-schedule audits remain preserved. The unchanged core authenticates inherited historical replay evidence of 208,704,795 exact field comparisons, not a v3 rerun. That historical evidence covers 46 comparison groups across 41 directories, five overlapping. Sources: [v3 audit](../outputs/v3_scheduling_validation.json), [historical replay report](../outputs/v2_regression.json), and [v3 test log](../outputs/v3_unit_tests.txt). Checks do not prove global optimality or independently recompute every window statistic. Recorded core times are 14.4–29.6 s, excluding CSV I/O, evaluation, and plotting; concurrent execution prevents algorithmic ranking by those timings.

Use a compact reference list: supplied Problem B and data; Slurm scheduling documentation; reproducibility source files. The full source and output links belong in the supplement/repository rather than repeated footnotes throughout the ten pages. Follow the [submission-package guide](../SUBMISSION_PACKAGE.md); raw data and full schedule/allocation CSVs are excluded from the supplemental ZIP.

## AI assistance and human verification — 0.5 page

Disclose that Claude supplied the optimization task briefs and Codex assisted with implementation, experiments, figures, and drafting. State what the team actually verified; do not assert verification that has not occurred. Human checks should cover problem interpretation, application compatibility assumptions, management thresholds, the meaning of the 90-day window, all final numbers and captions, and final competition formatting/disclosure requirements. Supporting records: [project status](../CURRENT_STATUS.md), [decisions](../DECISIONS.md), and the [English Q2/Q3 draft](B_Q23_report_en.md).
