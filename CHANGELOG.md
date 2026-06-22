# Changelog

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
