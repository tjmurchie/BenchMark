# BenchMark

A benchmarking tool for ancient DNA and metagenomic classification pipelines.
Monitors resource usage per pipeline step, generates comparison CSVs, and produces
publication-quality R plots. Part of the Workbench toolkit (Hakai Institute).

---

## Installation

```bash
cd ~/software/Workbench/BenchMark
bash install.sh
source ~/.bashrc   # or ~/.zshrc
```

Requirements: Python ≥ 3.8, `psutil` (auto-installed), R ≥ 4.0 with `ggplot2`.

---

## Typical workflow

### 1. Start a screen session for your tool

```bash
screen -S fillet_run
# (detach with Ctrl+A, D)
```

### 2. Start BenchMark monitoring (in your normal shell)

```bash
BenchMark go \
  --screen fillet_run \
  --tool Fillet \
  --dataset calculus_sim_100k \
  --output ~/results/benchmark/ \
  --notes "v0.3, standard params"
```

BenchMark starts a background daemon and returns immediately.
It waits for the screen session to appear if it hasn't started yet.

### 3. Run your pipeline in the screen session

Each time a program runs in the screen, BenchMark auto-detects it as a new step.
For Fillet's multi-step workflow, each command you run becomes a tracked step.

**Label steps before running them** (optional but recommended):

```bash
BenchMark mark "adapter_trim"
# → then run adapter trimming in screen
BenchMark mark "alignment"
# → then run alignment in screen
BenchMark mark "damage_assessment"
# → then run mapDamage / damage assessment in screen
```

You can also rename completed steps retroactively:
```bash
BenchMark rename 1 "adapter_trim"
BenchMark rename 2 "alignment"
```

### 4. Check progress

```bash
BenchMark status
```

### 5. Stop monitoring and get the CSV

```bash
BenchMark stop
```

Outputs `benchmark_Fillet_calculus_sim_100k_<timestamp>.csv` to your output directory
and prints a summary table.

---

## Comparing multiple tools

### Merge run CSVs

```bash
BenchMark merge \
  fillet_run.csv \
  kraken2_run.csv \
  megan7_run.csv \
  holi_run.csv \
  -o benchmark_comparison.csv \
  --pipeline-version v1.0
```

### Generate R plots

```bash
BenchMark analyse benchmark_comparison.csv \
  --output-dir ./plots \
  --title "Ancient DNA Classifier Benchmark"
```

Outputs to `./plots/`:
- `benchmark_comparison.pdf` — all plots in one PDF (title page + 8 figures)
- `01_wall_time.png` through `08_io_footprint.png` — individual 300 DPI PNGs
- `benchmark_summary_table.csv` — clean summary table for supplementary materials

---

## Wrapping a single command (no screen needed)

For simple one-step tools:

```bash
BenchMark run \
  --tool Kraken2 \
  --dataset calculus_sim_100k \
  -- kraken2 --db /path/to/db --output out.tsv reads.fastq.gz
```

---

## What is measured

| Metric | Description |
|--------|-------------|
| `wall_time_s` | Active wall-clock time (idle/waiting time excluded) |
| `cpu_user_s` | User-mode CPU time across all threads |
| `cpu_system_s` | Kernel-mode CPU time across all threads |
| `cpu_total_s` | Total CPU time (user + system) |
| `cpu_efficiency` | `cpu_total / wall_time` — values >1 = multi-threaded |
| `peak_mem_mb` | Maximum RSS memory during the step |
| `avg_mem_mb` | Average RSS memory during the active period |
| `max_threads` | Maximum thread count observed |
| `peak_processes` | Maximum number of spawned processes |
| `disk_read_mb` | Total data read from disk |
| `disk_write_mb` | Total data written to disk |
| `total_io_mb` | `read + write` (added on merge) |

System metadata captured per run: hostname, OS, CPU model, logical/physical
CPU count, total RAM.

---

## Idle detection

BenchMark pauses the wall-clock timer when the screen session is idle (shell
waiting for input). A step ends when:
- Only shell processes (`bash`, `zsh`, etc.) remain in the process tree, **and**
- CPU usage has been below 0.5% for at least 3 seconds

The next command you run in the screen starts a new step automatically.
This ensures that time spent between commands (reading logs, preparing the
next command) is not counted against any tool.

---

## CSV schema

Each CSV has one row per step plus a `TOTAL` summary row (where `is_summary=True`).
The TOTAL row aggregates: sum for time/IO metrics, max for memory/threads.

Example rows:
```
tool_name, dataset, step_name, wall_time_s, cpu_total_s, peak_mem_mb, is_summary
Fillet,    calculus, adapter_trim,  45.2, 180.8,  4096.0, False
Fillet,    calculus, alignment,    302.1, 1208.4, 12288.0, False
Fillet,    calculus, TOTAL,        347.3, 1389.2, 12288.0, True
```

---

## Adding TP/FP/FN data

Once your colleague's accuracy scripts are ready, add their output columns to
the merged CSV manually or via a script. The R analysis script automatically
plots any extra numeric columns it finds, so accuracy metrics will appear
alongside resource metrics once they're in the CSV.

---

## Session state

BenchMark stores session state in `~/.benchmark/sessions/<session>_<timestamp>/`:
- `state.json` — live session state (updated every 15s)
- `daemon.pid` — daemon process ID
- `daemon.log` — daemon log
- `pending_mark.json` — queued step label (transient)

---

## Running tests

```bash
# Unit tests
python3 -m pytest tests/test_core.py -v

# Integration test (runs real subprocesses)
python3 tests/simulate_workflow.py
```

---

## Planned additions

- TP/FP/TN/FN integration (accuracy metrics alongside resource metrics)
- Multi-replicate averaging with error bars in R plots
- Optional GPU monitoring
