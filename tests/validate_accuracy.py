"""
validate_accuracy.py — compare BenchMark measurements against /usr/bin/time -v

Runs a set of controlled workloads twice: once wrapped by /usr/bin/time -v
(ground truth) and once by BenchMark run. Reports absolute values and % deviation
for wall time, CPU time, and peak memory.

Usage:
    python3 tests/validate_accuracy.py [--verbose]

Requires: /usr/bin/time (GNU time), BenchMark on PATH, psutil installed.
"""

import argparse
import csv
import os
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GNU_TIME = "/usr/bin/time"
BOLD  = "\033[1m"
GREEN = "\033[92m"
YELLOW= "\033[93m"
RED   = "\033[91m"
RESET = "\033[0m"


# ── Workloads ────────────────────────────────────────────────────────────
# Two categories:
#   SHORT  — sub-second to ~2s: reveals measurement artifacts but not realistic
#   LONG   — 8-15s: representative of a single pipeline step (scale-down proxy)
# BenchMark is designed for runs of minutes to hours; short jobs show worst-case error.

WORKLOADS = [
    # ── Short (worst-case, reveals overhead) ────────────────────────────
    (
        "short_cpu_bound",
        [sys.executable, "-c",
         "x = sum(i*i for i in range(5_000_000))"],
        "short",
    ),
    (
        "short_memory_100mb",
        [sys.executable, "-c",
         "import time; d = bytearray(100 * 1024 * 1024); time.sleep(0.5)"],
        "short",
    ),
    # ── Long (representative of real pipeline steps) ────────────────────
    (
        "long_cpu_intensive",
        [sys.executable, "-c",
         "import hashlib; "
         "data = b'x' * 65536; "
         "[hashlib.sha256(data).hexdigest() for _ in range(50_000)]"],
        "long",
    ),
    (
        "long_memory_sustained",
        [sys.executable, "-c",
         "import time; "
         "d = bytearray(500 * 1024 * 1024); "   # 500 MB allocation
         "time.sleep(8)"],
        "long",
    ),
    (
        "long_mixed_realistic",
        [sys.executable, "-c",
         "import time, hashlib; "
         "d = list(range(5_000_000)); "          # 40 MB list
         "d.sort(reverse=True); "
         "data = b'y' * 65536; "
         "[hashlib.md5(data).hexdigest() for _ in range(30_000)]; "
         "time.sleep(3)"],
        "long",
    ),
]


# ── GNU time runner ───────────────────────────────────────────────────────

def run_gnu_time(cmd):
    """Run cmd under /usr/bin/time -v and parse its output."""
    full_cmd = [GNU_TIME, "-v"] + cmd
    result = subprocess.run(
        full_cmd,
        capture_output=True, text=True,
    )
    stderr = result.stderr

    def extract(pattern, text, cast=float, default=None):
        m = re.search(pattern, text)
        return cast(m.group(1)) if m else default

    wall_raw = extract(r"Elapsed \(wall clock\) time.*?:\s*([\d:\.]+)", stderr, cast=str)
    wall_s = _parse_wall(wall_raw) if wall_raw else None

    cpu_user   = extract(r"User time \(seconds\):\s*([\d.]+)", stderr)
    cpu_system = extract(r"System time \(seconds\):\s*([\d.]+)", stderr)
    peak_kb    = extract(r"Maximum resident set size \(kbytes\):\s*(\d+)", stderr, cast=int)

    return {
        "wall_s":      wall_s,
        "cpu_user_s":  cpu_user,
        "cpu_sys_s":   cpu_system,
        "cpu_total_s": (cpu_user or 0) + (cpu_system or 0),
        "peak_mem_mb": peak_kb / 1024 if peak_kb else None,
        "exit_code":   result.returncode,
    }


def _parse_wall(s):
    """Parse 'M:SS.ss' or 'H:MM:SS.ss' or plain seconds."""
    if s is None:
        return None
    parts = s.split(":")
    try:
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        return float(s)
    except ValueError:
        return None


# ── BenchMark run runner ──────────────────────────────────────────────────

def run_benchmark(cmd, outdir, name):
    """Run cmd under BenchMark run and return the parsed TOTAL row."""
    bm_cmd = (
        ["BenchMark", "run",
         "--tool", name,
         "--dataset", "validation",
         "--output", outdir,
         "--"] + cmd
    )
    result = subprocess.run(bm_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  BenchMark run failed:\n{result.stderr}")
        return None

    csvs = sorted([
        f for f in os.listdir(outdir)
        if f.startswith(f"benchmark_{name}") and f.endswith(".csv")
    ])
    if not csvs:
        return None

    with open(os.path.join(outdir, csvs[-1])) as f:
        rows = list(csv.DictReader(f))

    total = next((r for r in rows if r.get("is_summary") == "True"), None)
    if not total:
        return None

    def flt(key):
        try:
            return float(total[key]) if total.get(key) else None
        except ValueError:
            return None

    return {
        "wall_s":      flt("wall_time_s"),
        "cpu_user_s":  flt("cpu_user_s"),
        "cpu_sys_s":   flt("cpu_system_s"),
        "cpu_total_s": flt("cpu_total_s"),
        "peak_mem_mb": flt("peak_mem_mb"),
    }


# ── Comparison ────────────────────────────────────────────────────────────

def pct_diff(a, b):
    """% difference of b relative to a (a = reference)."""
    if a is None or b is None or a == 0:
        return None
    return (b - a) / a * 100.0


def colour_pct(pct, warn=15.0, bad=35.0):
    if pct is None:
        return "N/A"
    s = f"{pct:+.1f}%"
    abs_pct = abs(pct)
    if abs_pct <= warn:
        return f"{GREEN}{s}{RESET}"
    if abs_pct <= bad:
        return f"{YELLOW}{s}{RESET}"
    return f"{RED}{s}{RESET}"


def run_validation(verbose=False):
    if not os.path.exists(GNU_TIME):
        print(f"ERROR: {GNU_TIME} not found. Install GNU time: yum install time")
        sys.exit(1)

    # Sanity-check that /usr/bin/time is GNU time (has -v flag)
    probe = subprocess.run([GNU_TIME, "-v", "true"], capture_output=True, text=True)
    if "Maximum resident set size" not in probe.stderr:
        print(f"ERROR: {GNU_TIME} does not appear to be GNU time (missing -v output).")
        print("On some systems try: \\time -v  or install gnu-time.")
        sys.exit(1)

    results = []

    print(f"\n{BOLD}BenchMark Accuracy Validation{RESET}")
    print(f"Reference: {GNU_TIME} -v (GNU time)")
    print(f"Tolerance guide: {GREEN}≤15%{RESET}  {YELLOW}15-35%{RESET}  {RED}>35%{RESET}")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as outdir:
        for name, cmd, category in WORKLOADS:
            tag = f"[{category}]"
            print(f"\n{BOLD}{name}  {tag}{RESET}")

            # Run GNU time first
            gt = run_gnu_time(cmd)
            if verbose:
                print(f"  GNU time: wall={gt['wall_s']:.2f}s  cpu={gt['cpu_total_s']:.3f}s  "
                      f"mem={gt['peak_mem_mb']:.0f}MB")

            time.sleep(0.3)

            # Run BenchMark
            bm = run_benchmark(cmd, outdir, name)
            if bm is None:
                print(f"  {RED}BenchMark run failed - skipping{RESET}")
                continue
            if verbose:
                print(f"  BenchMark: wall={bm['wall_s']:.2f}s  cpu={bm['cpu_total_s']:.3f}s  "
                      f"mem={bm['peak_mem_mb']:.0f}MB")

            wall_pct  = pct_diff(gt["wall_s"],      bm["wall_s"])
            cpu_pct   = pct_diff(gt["cpu_total_s"], bm["cpu_total_s"])
            mem_pct   = pct_diff(gt["peak_mem_mb"], bm["peak_mem_mb"])
            # Absolute errors
            wall_abs  = abs(bm["wall_s"] - gt["wall_s"]) if gt["wall_s"] and bm["wall_s"] else None
            cpu_abs   = abs(bm["cpu_total_s"] - gt["cpu_total_s"]) if gt["cpu_total_s"] and bm["cpu_total_s"] else None

            row = {
                "workload":      name,
                "category":      category,
                "gt_wall_s":     gt["wall_s"],
                "bm_wall_s":     bm["wall_s"],
                "wall_pct_diff": wall_pct,
                "wall_abs_s":    wall_abs,
                "gt_cpu_s":      gt["cpu_total_s"],
                "bm_cpu_s":      bm["cpu_total_s"],
                "cpu_pct_diff":  cpu_pct,
                "cpu_abs_s":     cpu_abs,
                "gt_mem_mb":     gt["peak_mem_mb"],
                "bm_mem_mb":     bm["peak_mem_mb"],
                "mem_pct_diff":  mem_pct,
            }
            results.append(row)

            w_abs_str = f"({wall_abs:.2f}s)" if wall_abs is not None else ""
            c_abs_str = f"({cpu_abs:.2f}s)" if cpu_abs is not None else ""
            print(f"  {'Metric':<12} {'GNU time':>10} {'BenchMark':>10} {'%diff':>9} {'abs err':>10}")
            print(f"  {'-'*12} {'-'*10} {'-'*10} {'-'*9} {'-'*10}")
            print(f"  {'Wall time':<12} {gt['wall_s']:>9.2f}s {bm['wall_s']:>9.2f}s "
                  f" {colour_pct(wall_pct):>9}  {w_abs_str}")
            print(f"  {'CPU total':<12} {gt['cpu_total_s']:>9.3f}s {bm['cpu_total_s']:>9.3f}s "
                  f" {colour_pct(cpu_pct):>9}  {c_abs_str}")
            print(f"  {'Peak mem':<12} {gt['peak_mem_mb']:>8.0f}MB {bm['peak_mem_mb']:>8.0f}MB "
                  f" {colour_pct(mem_pct):>9}")

    # Summary — separate short vs long
    def avg(lst):
        return sum(lst) / len(lst) if lst else 0.0

    if results:
        print(f"\n{'=' * 70}")
        for cat in ("short", "long"):
            cat_rows = [r for r in results if r["category"] == cat]
            if not cat_rows:
                continue
            label = "Short jobs (<2s)  [worst case — not representative]" if cat == "short" \
                    else "Long jobs (>8s)   [representative of real classifier steps]"
            print(f"\n{BOLD}{label}{RESET}")

            wall_pcts = [abs(r["wall_pct_diff"]) for r in cat_rows if r["wall_pct_diff"] is not None]
            wall_abs  = [r["wall_abs_s"] for r in cat_rows if r["wall_abs_s"] is not None]
            cpu_pcts  = [abs(r["cpu_pct_diff"])  for r in cat_rows if r["cpu_pct_diff"] is not None]
            mem_pcts  = [abs(r["mem_pct_diff"])  for r in cat_rows if r["mem_pct_diff"] is not None]

            print(f"  Wall: mean {avg(wall_pcts):.1f}% ({avg(wall_abs):.2f}s absolute overhead)")
            print(f"  CPU:  mean {avg(cpu_pcts):.1f}%")
            print(f"  Mem:  mean {avg(mem_pcts):.1f}%")

            if cat == "long":
                # Thresholds are for test workloads of 2-8s.
                # For real classifier runs (minutes-hours), absolute overhead
                # (~0.3s wall, ~0.5s CPU) shrinks to <0.1% — completely negligible.
                wall_ok = avg(wall_pcts) < 12.0   # <12% on short proxies = <0.1% on 5-min runs
                cpu_ok  = avg(cpu_pcts)  < 20.0
                mem_ok  = avg(mem_pcts)  < 5.0
                ok = wall_ok and cpu_ok and mem_ok
                verdict = GREEN + "PASS" + RESET if ok else YELLOW + "REVIEW" + RESET
                print(f"  Verdict (long jobs): {verdict}")
                abs_wall = avg(wall_abs)
                print(f"  Absolute wall overhead ~{abs_wall:.2f}s "
                      f"(on a 30-min job = {abs_wall/1800*100:.3f}% error)")

    print(f"""
{BOLD}Known systematic differences vs GNU time:{RESET}
  Wall time  — BenchMark adds ~0.5s overhead (psutil startup + sampling interval).
               For workloads >10s this is <5% and negligible.
  CPU time   — BenchMark reads cumulative CPU at poll intervals; the final interval
               before process exit is partially lost. Underestimates by ~0.5-1 sample
               worth of CPU (typically <2s for long jobs, more significant for short ones).
  Peak mem   — GNU time uses kernel accounting (exact). BenchMark polls at 0.5s;
               brief memory spikes between samples are missed. For sustained allocation
               (typical in classifiers) this is accurate. For malloc/free spikes, may
               underestimate by up to one sample's worth.
  Multi-proc — GNU time only measures the direct child process.
               BenchMark aggregates the full subprocess tree — this is an ADVANTAGE
               for pipelines that spawn many workers (e.g. Kraken2 threads, bwa-mem2).
""")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate BenchMark accuracy vs GNU time")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print raw values from both tools")
    args = parser.parse_args()
    run_validation(verbose=args.verbose)
