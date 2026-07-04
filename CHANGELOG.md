# Changelog

## v0.3.0 (2026-07-03)

### Added
- `BenchMark go --resume` (opt-in): continue the most recent prior session for the
  given `--screen`, carrying its completed steps into a new run. Prints a warning and
  **stamps every CSV row's `notes`** as a NON-CONTIGUOUS run (the last pre-resume step
  may be incomplete; conditions can differ across the gap). For odd cases only — a
  clean restart is still recommended for paper-grade timings.

### Changed
- Documentation reworded to drop "publication-quality" — plots are described plainly
  as comparison/analysis plots (GitHub description, README, CLI help, R comments).

## v0.2.1 (2026-07-03)

### Changed
- Generous startup: the daemon now waits up to **2 hours** (was 5 min) for the screen
  session to appear, so there's no rush to launch your pipeline.
- The `--idle-timeout` no longer fires **before the first command runs** — a freshly
  started monitor will never auto-exit while you're still setting up. It applies only
  after the pipeline has actually started (i.e. between/after steps).

### Docs
- README: added a "Restarting a stopped run" section and clarified the startup grace.

## v0.2.0 (2026-07-03)

### Added
- `BenchMark go --screen NAME` now **auto-creates** the screen session (detached) if
  it isn't already running — you just `screen -r NAME`, run your pipeline, and detach.
  If the named screen already exists it is monitored as before (backward compatible).
- **Auto-finalize on screen exit**: when the monitored screen is killed/exits, the
  daemon detects it and writes the CSV itself — no `BenchMark stop` required. Governed
  by `--orphan-timeout` (default now 3 min; `0` = never).
- `BenchMark list` — alias for `status`; lists every active monitor by name, tool,
  dataset, step count, and daemon health (handy when several run at once).

### Changed
- Default `--orphan-timeout` lowered from 15 → 3 minutes so a killed screen finalizes
  promptly. `--idle-timeout` default unchanged (30 min; use `0` to disable for
  long, mostly-idle interactive runs).
- `go` output now prints the attach command and clarifies stop / auto-finalize behavior.

## v0.1.0 (2026-05-17)

Initial release.

### Features
- Background daemon monitoring of GNU Screen sessions via psutil process tree sampling
- Automatic step detection via idle/active transition (3-second debounce)
- Manual step labelling (`BenchMark mark`) and retroactive renaming (`BenchMark rename`)
- Per-step and TOTAL summary CSV output with 14 resource metrics plus system metadata
- `BenchMark run` subcommand for single-command wrapping without screen
- `BenchMark merge` to combine per-session CSVs from multiple tools/runs
- `BenchMark analyse` to generate 8 publication-quality R plots (PDF + 300 DPI PNGs)
- Multi-replicate accuracy validation against GNU time (`tests/validation_study.py`)
- 26 unit tests + integration test suite

### Validation
- Mean absolute deviation vs. GNU time for workloads >2 s:
  wall 2.7%, CPU 2.4%, memory 1.0% (N=3 replicates, 3 workload types)
- Absolute wall overhead ~0.06 s per step
- Full supplementary validation: `docs/supplementary_validation.md`

### Planned
- Multi-replicate error bars in R plots
- GPU monitoring
