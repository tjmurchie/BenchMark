"""
validation_study.py — Rigorous multi-replicate accuracy validation for BenchMark.

Runs a set of bioinformatics-representative workloads N times each under both
/usr/bin/time -v (reference) and BenchMark run (test), then computes statistics
and writes results to a CSV suitable for import into the supplementary document.

Usage:
    python3 tests/validation_study.py [--reps N] [--output-csv results.csv] [--verbose]

Output:
    - Console: formatted per-workload tables with statistics
    - CSV: machine-readable results for the supplementary document
"""

import argparse
import csv
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GNU_TIME = "/usr/bin/time"

BOLD   = "\033[1m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
RESET  = "\033[0m"

# ── Workload definitions ──────────────────────────────────────────────────────
# Each represents a scaled analogue of a real bioinformatics operation.
# Designed for 5-20 s duration so the study completes in ~15 min for N=3.

WORKLOADS = [
    {
        "id":          "cpu_kmer_hash",
        "label":       "CPU: k-mer hashing",
        "analogue":    "Kraken2/Kaiju k-mer lookup during read classification",
        "profile":     "CPU-intensive",
        "cmd": [sys.executable, "-c",
            "import hashlib; "
            "data = b'ACGTACGTACGT' * 512; "
            "[hashlib.sha256(data[i:i+32]).digest() for i in range(0, 1_500_000, 1)]"
            # ~5 s on modern hardware
        ],
    },
    {
        "id":          "mem_db_load",
        "label":       "Memory: database load",
        "analogue":    "Kraken2/DIAMOND loading a reference database into RAM",
        "profile":     "Memory-intensive",
        "cmd": [sys.executable, "-c",
            "import time; "
            "db = bytearray(512 * 1024 * 1024); "   # 512 MB
            "db[0] = 1; db[-1] = 1; "               # touch to force allocation
            "time.sleep(6)"
        ],
    },
    {
        "id":          "io_sequence_files",
        "label":       "I/O: sequence file read/write",
        "analogue":    "Reading FASTQ input and writing classified output",
        "profile":     "I/O-intensive",
        "cmd": [sys.executable, "-c",
            "import tempfile, os; "
            "f = tempfile.mktemp(suffix='.fq'); "
            # Write ~600 MB: 60-byte FASTQ records, 10 000 000 of them
            "rec = b'@seq\\nACGTACGTACGTACGTACGT\\n+\\nIIIIIIIIIIIIIIIIIIII\\n'; "
            "with open(f, 'wb') as fh: "
            "  [fh.write(rec * 50_000) for _ in range(200)]; "
            "with open(f, 'rb') as fh: _ = fh.read(); "
            "os.unlink(f)"
        ],
    },
    {
        "id":          "cpu_mem_mixed",
        "label":       "Mixed: CPU + memory",
        "analogue":    "MEGAN/DIAMOND alignment scoring with in-memory database",
        "profile":     "CPU + memory",
        "cmd": [sys.executable, "-c",
            "import hashlib, time; "
            "mem = bytearray(256 * 1024 * 1024); "        # 256 MB in RAM
            "data = b'ACGTACGT' * 256; "
            "[hashlib.sha256(data + bytes([i % 256])).digest() for i in range(120_000)]; "
            "time.sleep(2)"
        ],
    },
    {
        "id":          "sustained_classify",
        "label":       "Sustained: long classification",
        "analogue":    "Full-sample metagenomic classification run",
        "profile":     "Sustained CPU",
        "cmd": [sys.executable, "-c",
            "import hashlib; "
            "data = b'GATTACA' * 512; "
            "[hashlib.sha512(data + i.to_bytes(4,'big')).hexdigest() "
            " for i in range(3_000_000)]"
            # ~18-20 s: most representative of a real classification step
        ],
    },
]


# ── GNU time runner ───────────────────────────────────────────────────────────

def run_gnu_time(cmd):
    result = subprocess.run([GNU_TIME, "-v"] + cmd, capture_output=True, text=True)
    err = result.stderr

    def get(pattern, cast=float):
        m = re.search(pattern, err)
        return cast(m.group(1)) if m else None

    raw_wall = get(r"Elapsed \(wall clock\) time.*?:\s*([\d:\.]+)", cast=str)
    wall_s = _parse_wall(raw_wall)
    cpu_user = get(r"User time \(seconds\):\s*([\d.]+)")
    cpu_sys  = get(r"System time \(seconds\):\s*([\d.]+)")
    peak_kb  = get(r"Maximum resident set size \(kbytes\):\s*(\d+)", cast=int)
    return {
        "wall_s":      wall_s,
        "cpu_user_s":  cpu_user,
        "cpu_sys_s":   cpu_sys,
        "cpu_total_s": (cpu_user or 0) + (cpu_sys or 0),
        "peak_mem_mb": peak_kb / 1024 if peak_kb else None,
        "exit_code":   result.returncode,
    }


def _parse_wall(s):
    if s is None:
        return None
    parts = s.strip().split(":")
    try:
        if len(parts) == 3:
            return float(parts[0])*3600 + float(parts[1])*60 + float(parts[2])
        if len(parts) == 2:
            return float(parts[0])*60 + float(parts[1])
        return float(s)
    except ValueError:
        return None


# ── BenchMark runner ──────────────────────────────────────────────────────────

def run_benchmark(cmd, outdir, wid, rep):
    name = f"{wid}_rep{rep}"
    bm_cmd = ["BenchMark", "run",
               "--tool", name,
               "--dataset", "validation",
               "--output", outdir,
               "--"] + cmd
    result = subprocess.run(bm_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    csvs = sorted([f for f in os.listdir(outdir)
                   if f.startswith(f"benchmark_{name}") and f.endswith(".csv")])
    if not csvs:
        return None
    with open(os.path.join(outdir, csvs[-1])) as f:
        rows = list(csv.DictReader(f))
    total = next((r for r in rows if r.get("is_summary") == "True"), None)
    if not total:
        return None
    def flt(k):
        try: return float(total[k]) if total.get(k) else None
        except ValueError: return None
    return {
        "wall_s":      flt("wall_time_s"),
        "cpu_user_s":  flt("cpu_user_s"),
        "cpu_sys_s":   flt("cpu_system_s"),
        "cpu_total_s": flt("cpu_total_s"),
        "peak_mem_mb": flt("peak_mem_mb"),
    }


# ── Statistics ────────────────────────────────────────────────────────────────

def pct_dev(ref, meas):
    if ref and meas and ref != 0:
        return (meas - ref) / ref * 100.0
    return None


def mean_sd(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None, None
    return statistics.mean(vals), (statistics.stdev(vals) if len(vals) > 1 else 0.0)


def colour(pct):
    if pct is None: return "N/A"
    s = f"{pct:+.1f}%"
    if abs(pct) <= 5:  return GREEN  + s + RESET
    if abs(pct) <= 15: return YELLOW + s + RESET
    return RED + s + RESET


# ── Main study ────────────────────────────────────────────────────────────────

def run_study(n_reps=3, output_csv=None, verbose=False):
    if not os.path.exists(GNU_TIME):
        print(f"ERROR: {GNU_TIME} not found.")
        sys.exit(1)

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}BenchMark Validation Study{RESET}")
    print(f"Date: {ts}   Replicates: {n_reps}   Reference: {GNU_TIME} -v")
    print(f"{'='*70}{RESET}")

    all_rows = []   # for CSV output

    with tempfile.TemporaryDirectory() as outdir:
        for wl in WORKLOADS:
            wid   = wl["id"]
            label = wl["label"]
            cmd   = wl["cmd"]
            print(f"\n{BOLD}{CYAN}── {label} ──{RESET}")
            print(f"  Profile: {wl['profile']}  |  Analogue: {wl['analogue']}")

            gt_walls, gt_cpus, gt_mems = [], [], []
            bm_walls, bm_cpus, bm_mems = [], [], []
            wall_devs, cpu_devs, mem_devs = [], [], []

            for rep in range(1, n_reps + 1):
                sys.stdout.write(f"  Rep {rep}/{n_reps} ... ")
                sys.stdout.flush()

                # Alternate order each rep to reduce caching bias
                if rep % 2 == 1:
                    gt = run_gnu_time(cmd);       time.sleep(0.5)
                    bm = run_benchmark(cmd, outdir, wid, rep)
                else:
                    bm = run_benchmark(cmd, outdir, wid, rep); time.sleep(0.5)
                    gt = run_gnu_time(cmd)

                if gt is None or bm is None:
                    print("FAILED")
                    continue

                gt_walls.append(gt["wall_s"])
                gt_cpus.append(gt["cpu_total_s"])
                gt_mems.append(gt["peak_mem_mb"])
                bm_walls.append(bm["wall_s"])
                bm_cpus.append(bm["cpu_total_s"])
                bm_mems.append(bm["peak_mem_mb"])
                wall_devs.append(pct_dev(gt["wall_s"], bm["wall_s"]))
                cpu_devs.append(pct_dev(gt["cpu_total_s"], bm["cpu_total_s"]))
                mem_devs.append(pct_dev(gt["peak_mem_mb"], bm["peak_mem_mb"]))

                if verbose:
                    print(f"wall={gt['wall_s']:.2f}/{bm['wall_s']:.2f}s  "
                          f"cpu={gt['cpu_total_s']:.2f}/{bm['cpu_total_s']:.2f}s  "
                          f"mem={gt['peak_mem_mb']:.0f}/{bm['peak_mem_mb']:.0f}MB")
                else:
                    print("done")

                time.sleep(0.5)

            if not gt_walls:
                continue

            # Compute statistics
            gt_w_m,  gt_w_sd  = mean_sd(gt_walls)
            bm_w_m,  bm_w_sd  = mean_sd(bm_walls)
            gt_c_m,  gt_c_sd  = mean_sd(gt_cpus)
            bm_c_m,  bm_c_sd  = mean_sd(bm_cpus)
            gt_mm_m, gt_mm_sd = mean_sd(gt_mems)
            bm_mm_m, bm_mm_sd = mean_sd(bm_mems)
            wd_m, wd_sd = mean_sd(wall_devs)
            cd_m, cd_sd = mean_sd(cpu_devs)
            md_m, md_sd = mean_sd(mem_devs)

            # Absolute overhead
            wall_abs = bm_w_m - gt_w_m if (bm_w_m and gt_w_m) else None

            print(f"\n  {'Metric':<14} {'GNU time (ref)':>18} {'BenchMark':>18} "
                  f"{'Mean dev':>12} {'SD dev':>8}")
            print(f"  {'-'*14} {'-'*18} {'-'*18} {'-'*12} {'-'*8}")
            print(f"  {'Wall time':<14} {gt_w_m:.2f} ± {gt_w_sd:.2f} s".ljust(33)
                  + f"{bm_w_m:.2f} ± {bm_w_sd:.2f} s".rjust(18)
                  + f"  {colour(wd_m):>12}  ±{abs(wd_sd):.1f}%")
            print(f"  {'CPU time':<14} {gt_c_m:.2f} ± {gt_c_sd:.2f} s".ljust(33)
                  + f"{bm_c_m:.2f} ± {bm_c_sd:.2f} s".rjust(18)
                  + f"  {colour(cd_m):>12}  ±{abs(cd_sd):.1f}%")
            print(f"  {'Peak mem':<14} {gt_mm_m:.0f} ± {gt_mm_sd:.0f} MB".ljust(33)
                  + f"{bm_mm_m:.0f} ± {bm_mm_sd:.0f} MB".rjust(18)
                  + f"  {colour(md_m):>12}  ±{abs(md_sd):.1f}%")
            if wall_abs is not None:
                print(f"  Wall overhead: +{wall_abs:.2f} s absolute  "
                      f"({wall_abs/gt_w_m*100:.1f}% of reference duration)")

            # Store CSV row
            all_rows.append({
                "workload_id":    wid,
                "workload_label": label,
                "profile":        wl["profile"],
                "analogue":       wl["analogue"],
                "n_reps":         n_reps,
                "gt_wall_mean":   f"{gt_w_m:.3f}",
                "gt_wall_sd":     f"{gt_w_sd:.3f}",
                "bm_wall_mean":   f"{bm_w_m:.3f}",
                "bm_wall_sd":     f"{bm_w_sd:.3f}",
                "wall_dev_mean":  f"{wd_m:.2f}",
                "wall_dev_sd":    f"{wd_sd:.2f}",
                "wall_abs_s":     f"{wall_abs:.3f}" if wall_abs else "",
                "gt_cpu_mean":    f"{gt_c_m:.3f}",
                "gt_cpu_sd":      f"{gt_c_sd:.3f}",
                "bm_cpu_mean":    f"{bm_c_m:.3f}",
                "bm_cpu_sd":      f"{bm_c_sd:.3f}",
                "cpu_dev_mean":   f"{cd_m:.2f}",
                "cpu_dev_sd":     f"{cd_sd:.2f}",
                "gt_mem_mean":    f"{gt_mm_m:.1f}",
                "gt_mem_sd":      f"{gt_mm_sd:.1f}",
                "bm_mem_mean":    f"{bm_mm_m:.1f}",
                "bm_mem_sd":      f"{bm_mm_sd:.1f}",
                "mem_dev_mean":   f"{md_m:.2f}",
                "mem_dev_sd":     f"{md_sd:.2f}",
            })

    # Overall summary
    print(f"\n{BOLD}{'='*70}")
    print("Overall Summary (all workloads)")
    print(f"{'='*70}{RESET}")
    all_wd = [float(r["wall_dev_mean"]) for r in all_rows]
    all_cd = [float(r["cpu_dev_mean"])  for r in all_rows]
    all_md = [float(r["mem_dev_mean"])  for r in all_rows]
    print(f"  Wall time: mean deviation {statistics.mean(all_wd):+.1f}%  "
          f"max |dev| {max(abs(x) for x in all_wd):.1f}%")
    print(f"  CPU time:  mean deviation {statistics.mean(all_cd):+.1f}%  "
          f"max |dev| {max(abs(x) for x in all_cd):.1f}%")
    print(f"  Peak mem:  mean deviation {statistics.mean(all_md):+.1f}%  "
          f"max |dev| {max(abs(x) for x in all_md):.1f}%")

    abs_walls = [float(r["wall_abs_s"]) for r in all_rows if r["wall_abs_s"]]
    if abs_walls:
        print(f"\n  Mean absolute wall overhead: {statistics.mean(abs_walls):.2f} s")
        print(f"  (Equivalent error on a 30-min run: "
              f"{statistics.mean(abs_walls)/1800*100:.3f}%)")

    # Write CSV
    if output_csv and all_rows:
        with open(output_csv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
            writer.writeheader()
            writer.writerows(all_rows)
        print(f"\n  Results written to: {output_csv}")

    return all_rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Multi-replicate BenchMark accuracy validation study"
    )
    parser.add_argument("--reps", type=int, default=3, metavar="N",
                        help="Number of replicates per workload (default: 3)")
    parser.add_argument("--output-csv", metavar="FILE",
                        default="benchmark_validation_results.csv",
                        help="Output CSV path")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    run_study(n_reps=args.reps, output_csv=args.output_csv, verbose=args.verbose)
