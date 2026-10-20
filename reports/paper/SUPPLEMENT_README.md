# Code Supplement — 2026 MCM Senior Group, Problem B

This archive contains the code, tests and summary results behind the paper
*Fast and Fair: Scheduling and Rebalancing a Heterogeneous GPU Cluster*.
The two input CSV files supplied with the problem are not included; place them as
described below before running any simulation.

## Layout

| Path | Contents |
|---|---|
| `src/scheduler.py` | Event-driven online gang scheduler: node-level best-fit placement, FCFS / SJF / backfilling / linear and logarithmic aging, pull routing between GPU models |
| `src/experimental_scheduler.py`, `src/source_guard_engine.py`, `src/routing_admission.py`, `src/source_protection.py` | Pool-specific runtime limits and the protected (shadow-schedule) mode of Section 4.4 |
| `src/evaluate_scheduling.py`, `src/analyze_*.py` | Waiting-time, allocation, stranded-capacity, tail and paired-harm metrics |
| `src/analyze_question1.py` | Question 1 workload analysis |
| `src/run_*.py`, `src/reproduce_*.py` | Experiment drivers (queue policies, routing grids, held-out validation) |
| `src/plot_paper_figures.py` | All figures of the paper |
| `tests/` | Unit tests (`test_*.py`) and independent exact-arithmetic schedule auditors (`verify_*.py`) |
| `paper/paper.tex` | LaTeX source of the paper |
| `outputs/paper_figures/` | Figures as used in the paper (PNG and SVG) |
| `outputs/...` (other) | Summary tables behind every number in the paper (see below) |

## Environment

Python 3.12 with the pinned packages:

```
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt      # Windows
# .venv/bin/python -m pip install -r requirements.txt        # Linux/macOS
python -m unittest discover -s tests -p "test_*.py"
```

## Input data

Copy the two supplied files to

```
2026_MCM_Senior_GPU_Scheduling/2026_MCM_Senior_GPU_Scheduling/job_info_clean.csv
2026_MCM_Senior_GPU_Scheduling/2026_MCM_Senior_GPU_Scheduling/node_info_df.csv
```

| File | SHA-256 |
|---|---|
| `job_info_clean.csv` | `735bc06c3986b1d0c5932236f8685b7204463829118fc6a1d37030e253ed38e1` |
| `node_info_df.csv` | `1aba161961a5a4a1a61aa581383c5e5abe3400b59f8597ba8c4eef7597bc9d18` |

Requests are interpreted per worker; fractional and zero CPU requests are kept as given.

## Reproducing the paper

| Paper item | Command | Main outputs |
|---|---|---|
| Section 2, Table 1, Figures 2–3 | `python src/analyze_question1.py` | `outputs/question1/` |
| Section 3, Table 2 (FCFS, SJF, backfilling) | `python src/run_scheduling_experiments.py --suite question2` | `outputs/question2/` |
| Section 3, aging sweeps and choice of Q2\* | `python src/run_v2_experiments.py --suite q2` then `python src/run_v3_experiments.py --suite q2` | `outputs/question2/comparison_v2.csv`, `selection_v2.json` |
| Section 4.2, Table 3, Figure 6 (runtime limits) | `python src/run_v4_validation.py --phase development` | `outputs/v4_validation/development.csv` |
| Section 4.2 (pool-specific limits) | `python src/run_v5_architecture.py` | `outputs/v5_architecture/summary.csv` |
| Section 4.3–4.4, Table 4, Figure 7 (held-out validation, protected mode) | `python src/run_v6_source_protection.py --output outputs/v6_rerun --phase all` | `validation.csv`, `validation_summary.csv` |
| Section 4.6 (compensation account) | `python src/run_v3_experiments.py --suite q3 --bootstrap-dependencies` | `outputs/question3/v3/compensation.csv` |
| All figures | `python src/plot_paper_figures.py` | `outputs/paper_figures/` |

Each full replay of the trace takes 6–30 seconds; the full experiment set takes a few hours.
Per-job schedules (`schedule.csv`) and per-node allocations (`allocations.csv`) are not
shipped because of their size (about 700 MB); the drivers regenerate them. The figures
that plot individual schedules (stranded capacity and waiting-time tails) need those
regenerated files.

Every saved schedule can be checked independently with

```
python tests/verify_schedules.py --scenario-dir <directory containing schedule.csv>
```

which rebuilds node occupancy in exact decimal arithmetic and confirms capacity limits,
simultaneous worker starts, start times not earlier than arrival, and runtimes.

## Notes

- Experiment drivers refuse to overwrite existing results with different inputs; use a
  new output directory for a fresh replay.
- Hash-seeded eligibility (`p = 0.5`) makes every routing experiment deterministic.
  Development seeds 20261017–20261019 and 20261030–20261032 were used for tuning; held-out
  seeds 20261020–20261029 and 20261033–20261042 were used only for validation.
