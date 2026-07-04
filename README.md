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

# 1. Start monitoring — this AUTO-CREATES a detached screen named 'fillet_run'
BenchMark go \
  --screen  fillet_run \
  --tool    Fillet \
  --dataset calculus_sim_100k \
  --idle-timeout 0 \
  --output  ~/results/benchmark/

# 2. Attach to that screen, run your pipeline, detach (Ctrl-A D) whenever
screen -r fillet_run
#   ... run your commands; BenchMark mark "step" before each if you want labels ...

# 3. See what's being monitored
BenchMark list

# 4. Finish: stop it yourself, OR just kill the screen (CSV auto-writes either way)
BenchMark stop --screen fillet_run

# 5. Combine runs from multiple tools (one CSV each)
BenchMark merge fillet.csv kraken2.csv megan7.csv holi.csv \
  -o comparison.csv --pipeline-version v1.0

# 6. Generate publication plots
BenchMark analyse comparison.csv \
  --output-dir ./plots \
  --title "Ancient DNA Classifier Benchmark"
```

> `go` no longer requires you to create the screen first — it makes one for you.
> If a screen with that name already exists, it monitors it instead.

---

## Benchmarking a classifier — step by step

This is the exact recipe for timing one classifier method (Fillet, Kraken2, a
`eager → filtering → blastn → MEGAN` pipeline, HOLI, …) on one dataset. Run it
once per method; each produces one CSV that you later `merge` and `analyse` for
the comparison. Everything below is copy-paste — no scripting required.

**1. Start the monitor (it creates the screen for you).**
```bash
BenchMark go \
  --screen  megan_run \                 # screen name — also the run's handle in `list`/`stop`
  --tool    MEGAN-pipeline \            # ID: which method this run is (shown in plots)
  --dataset garg_up_bac_euk_damage \    # ID: which dataset this run used
  --notes   "eager→filter→blastn→MEGAN, 40 threads, nt 2026-06-11" \  # free text for your records
  --idle-timeout 0 \                    # never auto-stop on idle — you'll run steps by hand over hours/days
  --output  ~/results/benchmark/        # where the CSV is written
```
`--tool` and `--dataset` are just **labels** used to name the CSV and group/colour
the comparison — pick consistent names across methods (e.g. always `--dataset
garg_up_bac_euk_damage`). `--idle-timeout 0` is important for a hand-run pipeline:
without it the daemon would exit after 30 min of you not typing. Orphan detection
stays on (default 3 min) so that **killing the screen finalizes the run** (see step 4).

**2. Attach and run your pipeline by hand.**
```bash
screen -r megan_run
```
Inside the screen, optionally label each stage right before you launch it, then run it:
```bash
BenchMark mark "eager";      eager ...           # (mark is optional; without it steps are auto-numbered)
BenchMark mark "filtering";  filter ...
BenchMark mark "blastn";     blastn ...
BenchMark mark "megan";      megan ...
```
Detach anytime with **Ctrl-A then D** — the daemon keeps timing. Idle time between
steps (you thinking, or away entirely) is **not** counted; only active run time is.
Re-attach later with `screen -r megan_run` and continue. Steps are detected
automatically, so even without `mark` you still capture every command.

**3. Check on it from anywhere.**
```bash
BenchMark list        # every active monitor: name, tool/dataset, steps done, ● running / ○ stopped
```
Live daemon log if you want detail: `~/.benchmark/sessions/<screen>_<timestamp>/daemon.log`.

**4. Finish — two ways, both write the CSV:**
```bash
BenchMark stop --screen megan_run     # explicit: prints a summary and writes the CSV
```
…or just **kill/exit the screen** (`exit` inside it, or `screen -S megan_run -X quit`) —
the daemon notices within ~3 min and auto-writes the CSV. No `stop` needed.

**5. The key file.** Each run writes one CSV:
```
~/results/benchmark/benchmark_<tool>_<dataset>_<timestamp>.csv
```
That's the file to keep. It has one row per detected step plus a **TOTAL summary
row** (`is_summary=True`) with correct end-to-end `wall_time_s` (active time, idle
excluded) and `cpu_total_s`. Collect one CSV per method, then:
```bash
BenchMark merge fillet.csv kraken2.csv megan.csv holi.csv \
  -o comparison.csv --pipeline-version v1.0
BenchMark analyse comparison.csv --output-dir ./plots \
  --title "aDNA classifier benchmark — garg_up_bac_euk_damage"
```
`merge` combines them (adding `cpu_efficiency`, `total_io_mb`); `analyse` renders the
comparison PDF + PNGs. For a fair comparison, run every method on the **same machine**
with the **same thread budget** — `wall_time_s` is the headline speed, `cpu_total_s`
the thread-robust cross-check.

### Setup grace — no rush to launch

After `BenchMark go` you have plenty of time to get organised before running anything:

- The daemon waits up to **2 hours** for the screen to appear (irrelevant when `go`
  auto-creates it, but generous if you point it at a screen you'll make later).
- The `--idle-timeout` does **not** start counting until your **first command runs**,
  so a freshly-started monitor never auto-exits while you're still setting up — no
  matter how long you take. (After the first step, `--idle-timeout` behaves normally;
  for a hand-run pipeline with long gaps *between* steps, still pass `--idle-timeout 0`.)

### Restarting a stopped run

A monitor can end for several reasons: you ran `BenchMark stop`; the screen was killed
(auto-finalize); the `--idle-timeout` fired; or the machine rebooted / the daemon
crashed. Check which sessions are alive with `BenchMark list` (`●` running / `○` stopped).

To restart, just run `BenchMark go ...` again with the same details:

```bash
BenchMark go --screen megan_run --tool MEGAN-pipeline --dataset garg_up_bac_euk_damage \
  --idle-timeout 0 --output ~/results/benchmark/
```

Important: each `go` is a **fresh session → a fresh CSV** — there is no resume-in-place,
and timings do not carry over. So:
- If it stopped **after finishing**, you already have the CSV; nothing to do.
- If it stopped **mid-run** and you need clean end-to-end timings, re-run the whole
  method from the start (a fair TOTAL must be one contiguous run). Delete or ignore the
  partial CSV, or keep it and pick the complete one at `merge` time.
- If the **screen** is still alive but only the daemon died, `go` re-attaches to that
  same screen; if the screen is gone too, `go` creates a new one for you.

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
  --screen, -s NAME     Screen session to monitor. Auto-CREATED (detached) if it
                        isn't running yet; monitored as-is if it already exists.
                        Auto-detected if only one session is active and no name given.
  --output, -o DIR      Directory for CSV output (default: current directory)
  --notes, -n TEXT      Free-text notes — database version, parameters, etc.
  --idle-timeout MIN    Auto-exit after MIN minutes of continuous idle (default 30;
                        use 0 to disable — recommended for long, hand-run pipelines).
  --orphan-timeout MIN  Auto-exit + write the CSV MIN minutes after the screen is
                        killed/exits (default 3; 0 = never).
```

Starts a background daemon that monitors the named screen session and returns
immediately. It auto-creates the screen if needed, waits up to 5 minutes for the
session to appear, and — when the screen is later killed — auto-writes the CSV
without needing `BenchMark stop`.

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

You don't strictly need `stop`: if you simply kill/exit the monitored screen, the
daemon auto-finalises and writes the CSV on its own (within `--orphan-timeout`,
default 3 min). Use `stop` when you want an immediate finish and the printed summary.

---

### `BenchMark status` / `BenchMark list` — show active sessions

```
BenchMark status      # (BenchMark list is an identical alias)
```

Lists all monitored sessions with tool name, dataset, step count, and daemon
health (`●` running / `○` stopped). Handy when several benchmarks run at once —
find a name here, then `BenchMark stop --screen NAME` to finalise a specific one.
Note this lists **BenchMark** sessions only (ones started with `go`); a plain
`screen` you didn't attach BenchMark to won't appear — use `screen -ls` for those.

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

**Pausing the timer vs. exiting.** Idle *pauses* the step timer (idle time is never
counted). Separately, the daemon *exits* on two timeouts:
- `--idle-timeout` (default 30 min): if idle continuously for this long, assume the
  run is over and exit. For a long, hand-run pipeline with gaps, set `--idle-timeout 0`.
- `--orphan-timeout` (default 3 min): if the monitored screen is killed/exits, exit
  this long after and **auto-write the CSV** (so killing the screen finishes the run).

On an idle-timeout or orphan-timeout exit the CSV is written automatically; a manual
`BenchMark stop` also writes it (and prints the summary).

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
