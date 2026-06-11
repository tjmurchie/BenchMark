# GitHub release draft — BenchMark v0.1.0

## Repository description (GitHub "About" one-liner)

> Pipeline-aware CPU, memory, and I/O monitoring for ancient DNA and metagenomic classification workflows — per-step CSVs and publication-quality comparison plots.

(160 characters — fits the GitHub About field.)

---

## Topics / tags

```
ancient-dna  metagenomics  bioinformatics  benchmarking  resource-monitoring
python  r  gnu-screen  pipeline  classification  hakai-institute
```

---

## Initial commit message

```
feat: initial release of BenchMark v0.1.0

Pipeline-aware resource monitoring tool for ancient DNA and metagenomic
classification benchmarking.

Features:
- Background daemon attaches to GNU Screen sessions via psutil process-tree walk
- Per-step CPU time (user + system, all threads), wall-clock time, peak/mean RSS
  memory, disk read/write, thread and process counts
- Automatic idle detection: pauses timer when only shell processes remain in the
  session tree and aggregate CPU < 0.5% for >= 3 s
- Manual step labelling via `BenchMark mark` (sends SIGUSR1 to daemon)
- Single-command wrapping via `BenchMark run -- COMMAND`
- CSV merge across multiple tool runs with derived columns (cpu_efficiency,
  total_io_mb, mem_per_cpu_mb_per_s)
- 8 ggplot2 publication-quality comparison figures + PDF via `BenchMark analyse`
- Validated against GNU time (/usr/bin/time -v): wall 2.7%, CPU 2.4%, mem 1.0%
  mean deviation for steps > 2 s (N=3 replicates, 5 workloads)
- 26 unit tests + integration test suite
- Full supplementary validation section in docs/supplementary_validation.md
```

---

## README badges (already added to README.md)

```markdown
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![Tests](https://github.com/tjmurchie/BenchMark/actions/workflows/tests.yml/badge.svg)](https://github.com/tjmurchie/BenchMark/actions/workflows/tests.yml)
```

---

## Pre-push checklist

- [ ] `git remote add origin https://github.com/tjmurchie/BenchMark.git`
- [ ] Create the repo on GitHub (public, no auto-init — repo already has history)
- [ ] `git push -u origin master`
- [ ] Set the "About" description and topics above in the GitHub UI
- [ ] Confirm CI badge turns green after first push
- [ ] Add ORCID to `CITATION.cff` once you have it
- [ ] Tag the release: `git tag -a v0.1.0 -m "BenchMark v0.1.0" && git push origin v0.1.0`
- [ ] Create a GitHub Release from the tag (paste the commit message body above as release notes)
