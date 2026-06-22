# BenchMark

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![Tests](https://github.com/tjmurchie/BenchMark/actions/workflows/tests.yml/badge.svg)](https://github.com/tjmurchie/BenchMark/actions/workflows/tests.yml)

**Pipeline-aware resource monitoring for ancient DNA and metagenomic classification tools.**

BenchMark attaches to a running `screen` terminal session and tracks CPU time,
memory, disk I/O, and wall-clock time across every step of a pipeline — pausing
the timer automatically when the session is idle between commands. Output is a
per-step CSV that can be merged across multiple tool runs and rendered into
publication-quality comparison plots.

Developed at the [Hakai Institute](https://www.hakai.org/) by Tyler Murchie.

---

## Quick start

```bash
# Install
cd ~/software/BenchMark && bash install.sh && source ~/.bashrc

# 1. Start your screen session
screen -S fillet_run

# 2. Start monitoring (runs in background immediately)
BenchMark go \
  --screen fillet_run \
  --tool   Fillet \
  --dataset calculus_sim_100k \
  --output ~/results/benchmark/

# 3. Label each step before running it in screen (optional but recommended)
BenchMark mark "adapter_trim"
# → run trimming in screen ...
BenchMark mark "alignment"
# → run alignment in screen ...
BenchMark mark "classification"
# → run classification in screen ...

# 4. Stop and write CSV
BenchMark stop

# 5. Combine runs from multiple tools
BenchMark merge fillet.csv kraken2.csv megan7.csv holi.csv \
  -o comparison.csv --pipeline-version v1.0

# 6. Generate publication plots
BenchMark analyse comparison.csv \
  --output-dir ./plots \
  --title "Ancient DNA Classifier Benchmark"
```

---

## Installation

```bash
git clone https://github.com/tjmurchie/BenchMark ~/software/BenchMark
cd ~/software/BenchMark && bash install.sh && source ~/.bashrc
```

`install.sh` will:
- Check Python ≥ 3.8
- Install `psutil` via pip if not present
- Make `BenchMark` executable and add it to `PATH`
- Check R and install required packages (ggplot2, dplyr, tidyr, scales,
  gridExtra, RColorBrewer)
- Create `~/.benchmark/` state directory

**Requirements:**
- Python ≥ 3.8
- `psutil` ≥ 5.9 (installed automatically)
- `screen` (GNU Screen)
- R ≥ 4.0 with ggplot2 (for `BenchMark analyse`)
- `/usr/bin/time` (GNU time; for accuracy validation only)

---

## Commands

### `BenchMark go` — start monitoring

```
BenchMark go --tool NAME --dataset NAME [options]

Required:
  --tool, -t NAME       Tool or pipeline name (label only, e.g. "Kraken2")
  --dataset, -d NAME    Dataset name (e.g. "calculus_sim_100k")

Optional:
  --screen, -s NAME     Screen session to monitor
                        (auto-detected if only one session is active)
  --output, -o DIR      Directory for CSV output (default: current directory)
  --notes, -n TEXT      Free-text notes — database version, parameters, etc.
```

Starts a background daemon that monitors the named screen session and returns
immediately. The daemon waits up to 5 minutes for the screen session to appear
if it hasn't started yet.

**Step detection is automatic.** When a command starts running in screen, a new
step begins. When it finishes and the shell returns to the prompt (idle for 3 s),
the step ends and its metrics are recorded. No workflow changes are required.

**Example:**
```bash
BenchMark go --screen kr2 --tool Kraken2 --dataset dental_calculus \
             --notes "k=35, db=2024-01" --output ~/bench_results/
```

---

### `BenchMark mark` — label the next step

```
BenchMark mark [LABEL] [--screen NAME]
```

Queues a label for the next auto-detected step. Call this *before* running the
step in screen. Also forces a step boundary if a step is currently active.

```bash
BenchMark mark "database_build"
# then run the database-building command in screen
BenchMark mark "read_classification"
# then run classification in screen
```

If your pipeline is a single invocation (e.g. `python3 fillet.py`), you can also
have Fillet call BenchMark mark internally:

```python
import subprocess
subprocess.run(["BenchMark", "mark", "adapter_trim"], check=False)
```

The `check=False` means it silently does nothing if BenchMark is not running.

---

### `BenchMark rename` — rename a completed step

```
BenchMark rename STEP_NUM NEW_NAME [--screen NAME]
```

Retroactively rename a step by number (visible in `BenchMark status`).

```bash
BenchMark rename 1 "adapter_trim"
BenchMark rename 2 "bowtie2_alignment"
```

---

### `BenchMark stop` — stop monitoring and write CSV

```
BenchMark stop [--screen NAME] [--output DIR]
```

Sends stop signal to the daemon, waits for it to finalise, prints a summary
table, and writes the CSV to the output directory.

---

### `BenchMark status` — show active sessions

```
BenchMark status
```

Shows all currently monitored sessions with tool name, dataset, step count,
and daemon health.

---

### `BenchMark run` — wrap a single command

For simple single-step tools that don't need screen session monitoring:

```
BenchMark run --tool NAME --dataset NAME [--output DIR] [--notes TEXT] -- COMMAND
```

```bash
BenchMark run --tool Kraken2 --dataset test_100 -- \
  kraken2 --db /databases/k2_standard --output out.tsv reads.fastq.gz
```

---

### `BenchMark merge` — combine multiple run CSVs

```
BenchMark merge FILE [FILE ...] -o OUTPUT [options]

Options:
  -o, --output FILE           Output path (default: benchmark_merged.csv)
  --pipeline-version VERSION  Version tag added to all rows
  --run-label LABEL           Run label tag added to all rows
```

Merges per-session CSVs from multiple tool runs into one file for comparison.
Automatically adds `source_file`, `cpu_efficiency`, and `total_io_mb` columns.

```bash
BenchMark merge \
  results/fillet_v1_calculus.csv \
  results/kraken2_calculus.csv \
  results/megan7_calculus.csv \
  results/holi_calculus.csv \
  -o results/comparison_v1.csv \
  --pipeline-version "v1.0" \
  --run-label "dental_calculus_dataset"
```

---

### `BenchMark analyse` — generate R comparison plots

```
BenchMark analyse INPUT_CSV [--output-dir DIR] [--title TITLE]
```

Runs the R analysis script and generates:

| Output | Description |
|---|---|
| `benchmark_comparison.pdf` | All plots in one PDF (title page + 8 figures) |
| `01_wall_time.png` | Active wall-clock time per tool |
| `02_cpu_time.png` | Total CPU time per tool |
| `03_peak_memory.png` | Peak RSS memory per tool |
| `04_cpu_efficiency.png` | CPU efficiency (parallelism ratio) |
| `05_resource_heatmap.png` | Normalised resource comparison heatmap |
| `06_speed_vs_memory.png` | Speed vs. memory scatter |
| `07_step_breakdown.png` | Wall time stacked by step (if steps are labelled) |
| `08_io_footprint.png` | Disk read/write per tool |
| `benchmark_summary_table.csv` | Clean summary table for supplementary materials |

All plots use ggplot2 at 300 DPI with clean, publication-appropriate styling.

```bash
BenchMark analyse comparison.csv \
  --output-dir ./plots \
  --title "Ancient DNA Classifier Benchmark — Dental Calculus Dataset"
```

---

## What is measured

Each CSV row corresponds to one pipeline step (or a TOTAL summary row where
`is_summary = True`).

| Column | Description |
|---|---|
| `wall_time_s` | Active wall-clock seconds (idle time excluded) |
| `cpu_user_s` | User-mode CPU seconds, summed across all threads |
| `cpu_system_s` | Kernel-mode CPU seconds, summed across all threads |
| `cpu_total_s` | Total CPU time (user + system) |
| `cpu_efficiency` | `cpu_total / wall_time` — values >1 indicate multi-threading |
| `peak_mem_mb` | Maximum RSS memory observed during the step |
| `avg_mem_mb` | Mean RSS memory during the active period |
| `max_threads` | Maximum concurrent thread count |
| `peak_processes` | Maximum concurrent process count |
| `disk_read_mb` | Total data read from disk |
| `disk_write_mb` | Total data written to disk |
| `total_io_mb` | `disk_read + disk_write` (added on merge) |

System metadata recorded per session: hostname, OS, CPU model, logical/physical
core count, total RAM. This ensures measurements from different machines are
clearly identified in merged datasets.

---

## How idle detection works

BenchMark pauses the step timer when the screen session is idle:

1. Only shell processes (`bash`, `zsh`, etc.) remain in the session's process tree
2. AND aggregate CPU usage is below 0.5% for ≥3 seconds

The next command run in screen starts a new step automatically. This means time
spent reading output, reviewing intermediate results, or preparing the next command
is excluded from all measurements.

The 3-second debounce prevents brief pauses within a tool (e.g., between
index-building and classification phases) from being split into separate steps
unless the tool truly returns to the shell prompt.

---

## Per-step breakdown vs. total comparison

**Automatic step splitting** works when each pipeline stage is run as a separate
command in the screen session (e.g., a shell script where each step is run
individually, or a workflow where the user manually starts each stage). Each
command = one step row in the CSV.

**Manual step marking** via `BenchMark mark` is required for pipelines that run
as a single invocation (e.g., `python3 fillet.py`) to get per-stage breakdowns.
Without marks, a single-invocation pipeline will appear as one step.

Either way, the **TOTAL summary row** always gives correct end-to-end metrics for
cross-tool comparison, regardless of how many internal steps were detected.

---

## Session state

BenchMark stores session state in `~/.benchmark/sessions/<session>_<timestamp>/`:

```
state.json       — live session state (flushed every 15 s)
daemon.pid       — daemon process ID
daemon.log       — daemon log
pending_mark.json — queued step label (transient)
```

---

## Accuracy validation

BenchMark was validated against GNU `time` (`/usr/bin/time -v`) across five
controlled workloads representing bioinformatics resource profiles (N=3 replicates
each). For workloads with durations representative of production pipeline steps
(>2 s), mean absolute deviations were:

- Wall-clock time: **2.7%** (absolute overhead ~0.06 s)
- CPU time: **2.4%**
- Peak RSS memory: **1.0%**

For a 30-minute pipeline step, the absolute wall-time overhead of ~0.06 s
represents a **0.003% measurement error**. Full validation methodology and results
are in `docs/supplementary_validation.md`.

To reproduce the validation:
```bash
python3 tests/validation_study.py --reps 3 --output-csv validation_results.csv
```

---

## Running tests

```bash
# Unit tests (26 tests, <1 s)
python3 -m pytest tests/test_core.py -v

# Integration test (real subprocesses, merge, CSV round-trip)
python3 tests/simulate_workflow.py

# Accuracy validation (requires GNU time; ~15 min for N=3)
python3 tests/validation_study.py --reps 3
```

---

## Planned additions

- Multi-replicate averaging with error bars in R plots
- Optional GPU monitoring via `nvidia-smi`
- nf-core/Snakemake workflow integration
