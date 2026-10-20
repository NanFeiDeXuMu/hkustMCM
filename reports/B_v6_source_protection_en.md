# A10 source waiting protection

The development rule selected **loans512**. Across ten unused eligibility seeds and two assumed speeds, 20/20 validation cases have zero positive A10 waiting loss; 20/20 also satisfy both donor limits, positive A10 net benefit and the aggregate waiting limit. The source loan scan is 512 jobs and the donor CPU/GPU borrowing fraction is 0.25. The reference is the no-routing Q2* schedule, rather than the previous routing policy. Waiting and completion losses are evaluated separately.

## Validation results

Means average ten eligibility seeds equally. Donor columns are maxima over seeds and the two named destination pools. Limits remain 0.5 h for mean waiting and 2 h for the difference of marginal P95s. Positive waiting loss sums positive per-job differences before aggregation; gains cannot cancel these losses. A10 percentages use all 111,284 source jobs.

| Policy | Speed | Mean wait h | A10 later starts % | Positive wait loss job h | A10 later completions % | Donor mean max h | Donor P95 max h | Donor passes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Current | 1 | 3.598098 | 28.2580 | 8796.710 | 28.2580 | 0.221067 | 0.510278 | 10/10 |
| loans512 | 1 | 10.495106 | 0.0000 | 0.000 | 0.0000 | 0.233660 | 0.590556 | 10/10 |
| Current | 0.8 | 6.834822 | 28.7697 | 9045.850 | 48.7163 | 0.287638 | 1.042708 | 10/10 |
| loans512 | 0.8 | 14.839447 | 0.0000 | 0.000 | 18.6977 | 0.292788 | 1.268125 | 10/10 |

At speed 1, mean waiting changes from 3.598098 to 10.495106 h (+191.68%). The selected policy ranges from 10.096663 to 10.877505 h across seeds. Mean last completion changes from 211.091707 to 247.106285 days. The worst maximum wait across seeds changes from 661.763333 to 1514.551111 h; mean completed jobs at day 90 changes from 218091.6 to 217654.1.

At speed 0.8, mean waiting changes from 6.834822 to 14.839447 h (+117.12%). The selected policy ranges from 14.425671 to 15.255131 h across seeds. Mean last completion changes from 216.842653 to 247.106285 days. The worst maximum wait across seeds changes from 813.261667 to 1514.551111 h; mean completed jobs at day 90 changes from 217424.6 to 216918.4.

At speed 0.8, migrated execution takes 1.25 times the original duration. Earlier or unchanged starts can still produce later completions. The reported waiting protection therefore does not imply zero completion harm.

![Validation comparison](../outputs/v6_source_protection/source_protection_comparison.png)

The speed-0.8 strata below average within-group fractions across seeds. Duration bands use original execution times. Migrated/stayed groups describe realized assignments and do not estimate a causal migration effect. Both speeds are included in the [group summary](../outputs/v6_source_protection/validation_group_summary.csv).

| Policy | Group | Later starts % | Positive waiting loss job h | Net waiting saved job h | Later completions % |
|---|---|---:|---:|---:|---:|
| current | 1d_7d | 4.9256 | 352.460 | 815260.125 | 16.4910 |
| current | 1h_1d | 29.1697 | 4899.145 | 240855.739 | 54.9180 |
| current | gt7d | 0.0149 | 1.150 | 4418032.761 | 0.5276 |
| current | le1h | 32.4368 | 3793.095 | -1015.824 | 53.8608 |
| current | migrated | 0.5294 | 181.754 | 2568249.181 | 49.4425 |
| current | stayed | 48.2187 | 8864.095 | 2904883.620 | 48.2187 |
| loans512 | 1d_7d | 0.0000 | 0.000 | 572094.342 | 11.5468 |
| loans512 | 1h_1d | 0.0000 | 0.000 | 129746.631 | 25.2021 |
| loans512 | gt7d | 0.0000 | 0.000 | 2974851.628 | 0.5126 |
| loans512 | le1h | 0.0000 | 0.000 | 2034.581 | 19.9154 |
| loans512 | migrated | 0.0000 | 0.000 | 2585984.412 | 46.1395 |
| loans512 | stayed | 0.0000 | 0.000 | 1092742.770 | 0.0000 |


## Mechanism and assumptions

An online shadow A10 pool receives only arrived jobs and runs the unmodified Q2* log-aging policy with a seven-day scale. At each shadow start, a still-waiting actual job starts on the exact shadow nodes. If that job has already migrated or started early, the shadow reservation can be loaned to another waiting job whose entire execution fits before every borrowed node expires. Gang starts and physical CPU limits are retained. The bounded arrival-order source prefix has 128 or 512 jobs, with shorter durations attempted first.

The controller never reads the saved baseline schedule or future arrivals. By induction, actual borrowers release every node before its shadow reservation expires. Any source job not already started can therefore use its original start and placement. This construction requires the observed one-GPU geometry of all 2,494 A10 nodes and known execution durations; it is not a guarantee under duration prediction errors or arbitrary node geometry. Fractional and zero CPU requests remain valid.

The shadow retains reservations for jobs that actually migrated. Idle physical capacity can only accept jobs that fit within those intervals, so long or wide jobs may lose the earlier starts available under unrestricted rescheduling. This explains the fairness tradeoff: the protected mode is retained as an option rather than automatically replacing the policy with lower mean waiting.

In the complete paired case for the first validation seed 20261033, all 1,909 A10 jobs with original durations above 75 days retain their Q2* starts. They account for 58.23% at speed 1, and 38.52% at speed 0.8 of the increase in total waiting relative to the current policy. The denominator sums signed waiting differences across all jobs. This decomposition describes the first seed only; see the [long-job breakdown](../outputs/v6_source_protection/long_job_tradeoff_first_seed.csv).

The original 75-day migration eligibility ceiling and destination execution ceilings of 75 days for A100 and 30 days for H800 remain. Longer jobs stay in A10. Donors are attempted in A100 then H800 order; eligibility probability is assumed to be 0.5. Compatibility and relative speed are assumptions. All submitted jobs run to completion without truncation at the submission horizon. Hours measure waiting/completion loss; last completion is measured in days from the original time origin.

## Selection and verification

Six predeclared configurations were evaluated on three development eligibility seeds, 20261030–20261032, at speeds 1 and 0.8: 36 cases. A candidate must pass all six cases with zero individual positive A10 waiting loss, positive A10 net gain, mean cluster waiting at most 31.189516 h, and both donor limits. Eligible candidates are ranked by mean development waiting, then name. The locked choice is evaluated on seeds 20261033–20261042 without reselection.

| Policy | Six-case mean waiting h | Worst donor mean delta h | Worst donor P95 delta h | Joint passes |
|---|---:|---:|---:|---:|
| current | 5.127494 | 0.214863 | 0.508611 | 0/6 |
| loans128 | 18.275339 | 0.228921 | 0.457833 | 6/6 |
| loans128_cap10 | 27.181835 | 0.036615 | 0.001111 | 6/6 |
| loans128_cap15 | 21.654793 | 0.385047 | 1.777569 | 6/6 |
| loans512 | 12.616067 | 0.203599 | 0.457833 | 6/6 |
| shadow | 19.002953 | 0.228921 | 0.457833 | 6/6 |

These seeds perturb eligibility on one trace; they are not independent workload traces. The results do not establish global optimality or universal donor protection. The experimental engine preserves the frozen historical implementations and reuses the transactional storage and audit interfaces.

All 187 unit tests pass. Sixteen disabled-control regressions match 72,603,825 cells exactly; 2,372 historical files retain their hashes. The 4 saved validation schedules pass 23,897,403 independent Decimal checks. The other 72 cases retain metrics only and have no independent node-level audit. An additional 252,508 checks pass, including 18,622 allocation-level checks that source loans fit inside reclaimed baseline intervals. Full cases also compare each A10 start directly using Decimal arithmetic.

A separate full reproduction of the development loans512 case, seed 20261030 at speed 0.8, matches 75 original scalar metrics and passes 5,996,651 independent Decimal checks. A repeated call hits the cache and leaves 20 files unchanged. This is engineering reproduction, not a new eligibility sample.

```text
python -m unittest discover -s tests -p "test_*.py"
python tests/verify_v6_outputs.py
python src/reproduce_source_case.py --scenario outputs/v6_source_protection/cases/validation_loans512_s20261033_v0.8/scenario.json
python src/run_v6_source_protection.py --output outputs/v6_source_protection_reproduction --phase all
python tests/verify_v6_outputs.py --output outputs/v6_source_protection_reproduction
```

The dedicated reproduction adapter restores the explicit source-protection field, which is absent from the frozen generic SimulationSpec. A full rerun uses a new output directory and a new two-hour budget. Input CSVs, the full Q2* reference and the protocol's historical regression cases are required.

Source directory: `2026_MCM_Senior_GPU_Scheduling/2026_MCM_Senior_GPU_Scheduling/`.

- `job_info_clean.csv`: `735bc06c3986b1d0c5932236f8685b7204463829118fc6a1d37030e253ed38e1`
- `node_info_df.csv`: `1aba161961a5a4a1a61aa581383c5e5abe3400b59f8597ba8c4eef7597bc9d18`

See the [protocol](../outputs/v6_source_protection/protocol.json), [verification](../outputs/v6_source_protection/verification.json) and per-case receipts for provenance. The main manuscript and previous supplement remain historical versions. AI assisted implementation and analysis; the team must verify assumptions, management thresholds and final claims.
