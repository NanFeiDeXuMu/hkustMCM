# Questions 2–3: Queue Fairness and Controlled Cross-Model Routing

Competition-report draft, 17 October 2026. All 224,112 jobs are followed to completion. Q2* is fixed; the final v3 grid yields separate strict-service and compensation recommendations. Historical experiments remain available as controls.

## Model and common evaluation

Nodes start idle. Runtime is known when a job arrives, but future arrivals are unavailable. Execution is non-preemptive; all workers start together, and each worker receives its GPU and CPU requests on the same compatible node. Requests are per worker, including fractional or zero CPU. Every policy uses the same deterministic node placement. A node's worker slots equal the minimum of its available GPU and CPU request multiples, omitting the CPU division for a zero request. Gang placement requires enough slots across compatible nodes.

Simultaneous completions release resources before simultaneous arrivals and native dispatch. Waiting is start minus submission. Mean and tail statistics include jobs finishing after day 90; fixed-window completions and resource allocation use the separate 90-day convention. Allocation rates measure simulated occupancy, not hardware utilization. Bounded slowdown is

\[
BS_j=\max\left\{1,\frac{\text{end}_j-\text{submit}_j}{\max(\text{runtime}_j,60\text{ s})}\right\}.
\]

## Question 2: Scheduling under saturation

FCFS orders by arrival; strict SJF orders by known runtime. Both block the pool when their head cannot be placed. Restricted backfill protects the FCFS head's earliest feasible reservation: among the first 128 successors, shorter candidates can start only if they fit and finish by that reservation. We also test 512 candidates. This follows the reservation principle described in the [Slurm scheduling guide](https://slurm.schedmd.com/sched_config.html), but is a deliberately restricted implementation.

Under FCFS, A10 allocates **99.930%** of its 90-day GPU capacity-time. Exact event integration finds only **1,529.609 GPU-hours** idle while its own queue is nonempty, or **0.0285%** of capacity during that queued time. There is little local idle capacity for backfill to recover: overall mean waiting changes from 447.319 hours to 446.156/443.018 hours with 128/512 candidates. This explains the small gain more directly than attributing it entirely to reservation restrictions. Saturation does not imply identical completed-job counts: changing order can complete many more short jobs within the fixed window. [Evidence: FCFS queue–idle diagnostics](../outputs/question2/legacy_diagnostics/fcfs/metrics_idle_while_queued_by_model.csv).

### SJF benefits and concentrated harm

SJF lowers mean waiting in every original-duration group, including jobs longer than seven days: 480.924 to 270.187 hours. Overall mean waiting falls to 16.568 hours and day-90 completions rise from 164,886 to 217,494. Its cost is concentrated. Exactly **2,502 jobs (1.1164%)** wait longer than under FCFS, but they represent **14.8321% of original GPU work**; their conditional mean increase is 770.493 hours. All 1,525 jobs waiting over 1,000 hours request A10 and have original runtimes of 49.521–183.508 days. The full harmed cohort also contains other GPU models. [Evidence: paired tail metrics](../outputs/question2/v2_candidates/sjf/tail_metrics.json), [cohorts](../outputs/question2/v2_candidates/sjf/tail_cohorts.csv), and [model distribution](../outputs/question2/v2_candidates/sjf/tail_model_distribution.csv).

### Aging and the revised choice

We compare static priority keys

\[
k_j^{lin}=d_j+\alpha a_j,\qquad
k_j^{log}=\ln(d_j/1\text{ s})+a_j/T.
\]

Here \(d_j\) is original runtime, \(a_j\) is submission time, and \(T>0\) is a time scale. The keys equal the corresponding age-adjusted scores after removing a common time term. Thus no heap-wide update is needed, and two already-waiting jobs do not exchange order merely as the clock advances. Under log aging, an older long job outranks a later short job when their arrival gap exceeds \(T\ln(d_L/d_S)\). This addresses multiplicative runtime differences. Both variants retain strict head blocking.

The earlier rule bounded both maximum wait and final completion by FCFS, selecting linear \(\alpha=100\), with mean 432.865 hours. We retain it as a strict-cap alternative. Following review of that trade-off, **D023 was fixed before the new runs**: each duration group's mean must not exceed FCFS (C1), and the overall mean must not exceed twice SJF's exact mean (C2: 33.135 hours rounded). Among feasible candidates, minimize maximum waiting, then mean waiting, then scenario name. This is an explicit policy preference and same-trace tuning, not independent out-of-sample validation.

The 22 candidates comprise four baseline configurations, twelve linear-aging settings, and six log-aging settings. The [selection record](../outputs/question2/selection_v2.json) chooses **Q2*: log aging, T=7 days**; using only the older candidates selects linear \(\alpha=1\), providing the required consistency check.

| Policy | Mean wait, h | P99 wait, h | Maximum wait, h | Day-90 completions | Last completion, days |
|---|---:|---:|---:|---:|---:|
| FCFS | 447.319 | 1,416.306 | 1,423.256 | 164,886 | 243.264 |
| SJF | 16.568 | 366.313 | 3,017.288 | 217,494 | 309.720 |
| Linear aging, α=1 | 18.435 | 706.994 | 2,841.827 | 217,192 | 302.409 |
| **Q2*: log aging, T=7 d** | **31.190** | **1,097.589** | **1,514.551** | **215,229** | **247.106** |
| Strict-cap alternative, α=100 | 432.865 | 1,393.817 | 1,421.416 | 165,868 | 243.226 |

Source: [complete candidate comparison](../outputs/question2/comparison_v2.csv).

![Queue-policy trade-offs](../outputs/question2/figures_v3/04_policy_tradeoff.png)

*Figure 1. C2's shaded mean-wait budget and the two aging curves; C1 is checked separately. Open T=10/14/21-day points refine the curve only and never enter selection.*

Applying C2 multipliers 1.5/2/3 to the original 22 candidates selects T=30/7/7 days. The tighter budget selects mean 19.833 hours but maximum 2,187.630 hours; Q2* remains fixed at T=7 days. [Rule sensitivity](../outputs/question2/c2_multiplier_sensitivity.csv).

Q2* cuts SJF's maximum waiting by **49.80%**, but does not improve every tail measure. A maximum is determined by one job; P99 is less sensitive to an individual extreme, so both are reported. P99 waiting rises from 366.313 to 1,097.589 hours and P99 bounded slowdown from 4.179 to 7.883. Jobs exceeding 1,000 hours increase from 1,525 to 3,354, while those exceeding FCFS's exact maximum decrease from 1,199 to 159. Relative to FCFS, the harmed share becomes 1.3440%, representing 19.1825% of work; their conditional mean increase falls to 118.594 hours. The choice compresses the most extreme waits by spreading moderate delays more broadly. Group-mean protection does not guarantee individual protection. [Evidence: Q2* tail metrics](../outputs/question2/q2_star/tail_metrics.json) and [cohorts](../outputs/question2/q2_star/tail_cohorts.csv).

![Group means and slowdown](../outputs/question2/figures_v2/06_group_fairness.png)

*Figure 2. Original-duration groups show C1 compliance and the distinct P99 slowdown trade-off.*

## Question 3: Routing benefits, native costs, and compensation

All main configurations use Q2* without routing as their paired baseline. A stable job-ID hash assigns eligibility at \(p=0.5\), interpreted as the joint fraction technically compatible and willing to accept substitution. Workers and GPU/CPU requests are preserved; default migrated runtime is unchanged. One-to-one GPU mapping, compatibility, and zero transfer overhead are assumptions, not consequences of model names.

After native dispatch, pull routing scans the earliest 256 eligible A10 waiters for each donor, normally A100 then H800. Each migration requires an empty donor-native queue, immediate node placement, and concurrent GPU and CPU borrowing within 25% of pool capacities. Jobs start immediately and never move again. An original-runtime limit \(D_{max}\) filters the eligible set; a minimum wait \(\tau\) delays eligibility. A separate reserve \(R\) protects current aggregate free capacity, but cannot guarantee service for future arrivals.

### Two declared management choices

D026 compares 11 historical and 12 new main configurations, excludes anonymous donors from recommendation, and maximizes positive A10 mean-wait reduction subject to either rule:

- **Strict service:** every donor's native mean increase ≤0.5 hours and marginal P95 increase ≤2 hours.
- **Compensation option:** exchange ratio ≥100, every donor's mean increase ≤1 hour, and every donor's fraction waiting longer ≤20%.

P95 cost is a difference between marginal quantiles, not a quantile of paired delays. Thresholds are management assumptions fixed before v3; sensitivities cannot reselect parameters. [Rules and final selection](../outputs/question3/v3/selection.json).

| Configuration | Overall mean, h | A10 mean, h | Maximum, h | Worst donor mean/P95 increase, h |
|---|---:|---:|---:|---:|
| Q2*, no routing | 31.190 | 62.365 | 1,514.551 | 0 / 0 |
| AH, duration ≤7 d | 26.368 | 52.655 | 1,445.110 | 0 / 0 |
| AH, duration ≤30 d | 15.803 | 31.378 | 1,130.068 | 0 / 0 |
| **Strict: AH, duration ≤60 d** | **6.818** | **13.235** | **788.598** | **0.067 / 0** |
| **Compensation: AH, unrestricted** | **1.361** | **2.129** | **494.451** | **0.857 / 5.836** |

AH denotes A100/H800 in that order. Source: [full comparison](../outputs/question3/v3/comparison.csv). Strict routing finishes at day 216.858 with 217,617 day-90 completions; compensation routing finishes at day 204.602 with 218,279 completions, versus baseline day 247.106 and 215,229 completions. Both recommendations use named models; anonymous compatibility is unnecessary.

“Strict” does not mean zero individual harm. A100/H800 native harmed fractions are **4.586%/0.254%**, with mean increases **0.066991/0.000780 hours**; both P95 increments remain zero. Compensation routing increases those harmed fractions to **9.189%/17.409%**, with mean increments **0.178078/0.856739 hours** and P95 increments **0.321722/5.836111 hours**. Pooled harmed fractions are 4.347% and 9.642%, but the 20% rule is checked separately for each donor. [Strict donor costs](../outputs/question3/v3/v3_ah_d60/native_harm_by_donor.csv), [compensation donor costs](../outputs/question3/v3/v2_reference/v2_ah/native_harm_by_donor.csv).

Routing also changes later A10 queue order and strict-head blocking. Consequently, **27.295%/27.265%** of A10 jobs wait longer under strict/compensation routing, with largest increases **33.267/79.517 hours**, despite substantial mean gains. Neither policy protects every source-pool user.

### Which mechanisms matter?

The duration frontier fills the gap left by the earlier seven-day experiment. D30 produces zero observed donor delay; D60 moves more long work and meets strict bounds. Unlimited routing violates strict bounds but meets the compensation rule. A one-hour wait with D60 preserves 97.31% of its A10 mean benefit while cutting migrations from 47,810 to 4,977; it slightly lowers worst donor mean cost but also lowers overall benefit. A 10% borrowing cap passes strict bounds with A10 benefit 38.432 hours; 15% achieves 50.600 hours but fails the P95 bound. Thus controls have distinct, non-monotone effects.

Donor order matters sharply. With D30, H800-first keeps the same A10 benefit but raises the worst native mean/P95 increments to **17.881/151.638 hours**; unrestricted H800-first reaches **130.388/555.017 hours**. Both fail both rules. Historical reserve-only controls also fail strict bounds. These are trace-specific comparisons, not universal rankings.

![Routing frontier](../outputs/question3/v3/figures/05_routing_frontier.png)

*Figure 3. Family curves and two recommendations. Horizontal lines show only mean-cost thresholds; P95, harmed-share, and exchange conditions are checked separately.*

Strict routing moves **47,810 jobs and 16.068% of original A10 GPU work**, compared with **27,722 jobs and 28.537%** under compensation routing. Counts are not a proxy for work. Strict A100/H800 migrant counts are 45,313/2,497; their actual peak borrowed GPU/CPU pairs are **864/12,298** and **189/2,584**. Compensation counts are 22,739/4,983 and peaks **864/13,824** and **438/9,153**. GPU and CPU maxima may occur at different times. [Strict peaks](../outputs/question3/v3/v3_ah_d60/borrowing_peaks.csv), [compensation peaks](../outputs/question3/v3/v2_reference/v2_ah/borrowing_peaks.csv).

### A transparent delay-credit account

Let \(\Delta w_j=w_j^{new}-w_j^{base}\), in hours, and \(G_j\) be whole-job GPU demand. Define unweighted exchange

\[
E=\frac{\sum_{j\in A10}(-\Delta w_j)}{\sum_{j\in donor\ native}\max(0,\Delta w_j)},
\]

with an infinite-ratio flag when the denominator is zero. Strict/compensation routing saves **5,467,397.862/6,703,307.366 job-hours** for A10 and adds **5,421.691/18,442.826 positive job-hours** to donor natives, giving **1,008.430:1/363.464:1** exchange ratios.

Proposed credits use a separate GPU-weighted account:

\[
C=\sum_{j\in donor\ native}G_j\max(0,\Delta w_j),\qquad
S=\sum_{j\in A10}G_j(-\Delta w_j).
\]

Strict/compensation credits are **35,177.769/794,345.272 GPU-hours** against net savings **5,601,825.318/6,823,034.740 GPU-hours**, so \(C/S\) is **0.628%/11.642%**. It is undefined when \(S\le0\). The job-hour exchange and GPU-weighted share answer different questions. [Formal compensation ledger](../outputs/question3/v3/compensation.csv).

![Compensation ledger](../outputs/question3/v3/figures/07_compensation.png)

*Figure 4. Net A10 benefit, positive donor cost, and proposed GPU-weighted credit share.*

Credits make the delay account auditable; they do not establish real utility, fairness, cash reimbursement, or willingness to pay. Redeeming credits for future discounts or priority—and the resulting scheduling feedback—has not been simulated. The compensation option therefore requires an explicit management choice rather than replacing strict service automatically.

### Sensitivity and residual congestion

The strict configuration passes its limits across three eligibility seeds, with overall mean 6.653–6.951 hours. At p=.25 its mean is 16.758 hours with zero observed donor harm. At p=1 its mean is 0.928 hours, but worst donor mean/P95 increments rise to 0.886/5.278 hours and the maximum harmed fraction to 23.664%: both rules fail.

**The compensation option is sensitive to the eligibility set.** Seeds 18/19 yield worst donor mean increases 1.254/1.314 hours, both violating its 1-hour bound. At p=1 the increase is 1.608 hours and maximum harmed share 27.905%, also failing. At p=.25 it passes both rules, with mean 10.710 hours. Thresholds and the original main-grid choices remain unchanged. Therefore strict routing is the default proposal at the specified participation level; compensation is a conditional in-sample management alternative, not a robust default. [All eight new sensitivities](../outputs/question3/v3/comparison.csv).

Exact 90-day event integration of \(\min\{\sum_{k\in D}\mathrm{idle}_k(t),Q_{A10}(t)\}\), for fixed \(D=\{A100,A800,H800,Series\text{-}2\}\), falls from **3.681 million GPU-hours** at baseline to **1.327/0.264 million** for strict/compensation routing. Total GPU allocation rises **69.438%→76.268%/79.602%**. Requests queue under their original model on [submission,start), including future migrants. This diagnostic is a matching bound on the realized timeline, ignoring compatibility, CPU, and packing; it is not attainable savings or an upper bound on a different schedule's improvement.

## Limits, validation, and practical interpretation

The parameter \(p\) can represent the joint fraction of technically compatible jobs and users willing to accept substitution, possibly for a discount. Without price or willingness data, no tariff, elasticity, revenue, or profit can be inferred. Selection is in-sample; runtimes are assumed known. MIG, communication, migration overhead, failures, and runtime errors remain unmodeled. GPU-series-1's full-work/90-day-capacity ratio above one includes work beyond the window and does not establish sustained within-window overload.

All **140 tests** pass. The unchanged scheduler retains the authenticated historical replay evidence of **208,704,795 exact field comparisons**; that replay was not rerun in v3. The two newly saved recommendation schedules pass **11,701,433 independent Decimal checks**, plus 489 paired-account/history checks, with zero failures. Earlier evidence for 24 full schedules is preserved. The 39 new metric-only outputs are excluded from per-node audit claims. These checks support recorded feasibility and accounting, not global optimality or independent recomputation of every window statistic. Recorded core times are 14.4–29.6 seconds, excluding CSV I/O, evaluation, and plotting; concurrent runs prevent interpreting timing differences as algorithmic rankings. Sources: [v3 audit](../outputs/v3_scheduling_validation.json), [historical replay](../outputs/v2_regression.json), and [test log](../outputs/v3_unit_tests.txt). Definitions and reproducibility are documented in the [method note](B_Q23_methods_zh.md), [v3 runner](../src/run_v3_experiments.py), and [submission-package guide](../SUBMISSION_PACKAGE.md). Final competition disclosure should identify AI assistance and the team's own verification.
