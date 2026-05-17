"""
simulate_workflow.py — integration test for BenchMark without a real screen session.

Simulates a two-step workflow using the `BenchMark run` pathway (direct command wrapping),
then exercises the merge and report generation paths end-to-end.

Run with:
    python3 tests/simulate_workflow.py
"""

import csv
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BENCHMARK_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "BenchMark"
)

GREEN = "\033[92m"
RED   = "\033[91m"
RESET = "\033[0m"
BOLD  = "\033[1m"


def run(cmd, check=True, capture=False):
    """Run a shell command, return CompletedProcess."""
    return subprocess.run(
        cmd, shell=True, check=check,
        capture_output=capture, text=True,
    )


def ok(msg):
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg):
    print(f"  {RED}✗{RESET} {msg}")
    sys.exit(1)


def section(title):
    print(f"\n{BOLD}{title}{RESET}")
    print("─" * 50)


# ── Test 1: direct `run` subcommand (wraps a real process) ───────────────

def test_run_command(outdir):
    section("Test 1: BenchMark run — wrap a short CPU+memory task")

    # A short Python command that allocates some memory and does work
    payload = (
        "python3 -c \""
        "import time, os; "
        "data = list(range(500_000)); "
        "time.sleep(1); "
        "data2 = [x * 2 for x in data]; "
        "time.sleep(0.5)"
        "\""
    )
    cmd = (
        f"BenchMark run "
        f"--tool SimulatedTool_A "
        f"--dataset sim_calculus_100k "
        f"--name sim_run_A "
        f"--output {outdir} "
        f"-- {payload}"
    )
    result = run(cmd, capture=True)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        fail("BenchMark run failed")

    csvs = [f for f in os.listdir(outdir) if f.startswith("benchmark_") and f.endswith(".csv")]
    if not csvs:
        fail("No CSV output found")
    csv_path = os.path.join(outdir, csvs[0])

    with open(csv_path) as f:
        rows = list(csv.DictReader(f))

    if not rows:
        fail("CSV is empty")

    total_row = [r for r in rows if r["is_summary"] == "True"]
    if not total_row:
        fail("No TOTAL summary row in CSV")

    t = total_row[0]
    ok(f"Tool: {t['tool_name']}, dataset: {t['dataset']}")
    ok(f"Wall time: {t['wall_time_s']}s, CPU total: {t['cpu_total_s']}s")
    ok(f"Peak memory: {t['peak_mem_mb']} MB")
    ok(f"Host: {t['hostname']}, CPUs: {t['cpu_count_logical']}")

    assert float(t["wall_time_s"]) > 0, "wall_time_s should be > 0"
    assert float(t["peak_mem_mb"]) > 0, "peak_mem_mb should be > 0"
    ok("Wall time and memory are non-zero")

    return csv_path


# ── Test 2: second simulated run (heavier) ───────────────────────────────

def test_run_heavy(outdir):
    section("Test 2: BenchMark run — heavier task (simulates Tool B)")

    payload = (
        "python3 -c \""
        "import time; "
        "big = list(range(2_000_000)); "
        "time.sleep(2); "
        "result = sorted(big, reverse=True)[:1000]; "
        "time.sleep(0.5)"
        "\""
    )
    cmd = (
        f"BenchMark run "
        f"--tool SimulatedTool_B "
        f"--dataset sim_calculus_100k "
        f"--name sim_run_B "
        f"--output {outdir} "
        f"-- {payload}"
    )
    result = run(cmd, capture=True)
    print(result.stdout)
    if result.returncode != 0:
        fail("BenchMark run (heavy) failed")

    csvs = sorted([
        f for f in os.listdir(outdir)
        if f.startswith("benchmark_SimulatedTool_B") and f.endswith(".csv")
    ])
    if not csvs:
        fail("No CSV for Tool B found")

    csv_path = os.path.join(outdir, csvs[-1])
    with open(csv_path) as f:
        rows = list(csv.DictReader(f))
    total = [r for r in rows if r["is_summary"] == "True"][0]
    ok(f"Tool B wall time: {float(total['wall_time_s']):.1f}s  mem: {float(total['peak_mem_mb']):.0f} MB")
    return csv_path


# ── Test 3: merge ─────────────────────────────────────────────────────────

def test_merge(outdir, csv_a, csv_b):
    section("Test 3: BenchMark merge — combine two run CSVs")

    merged = os.path.join(outdir, "merged.csv")
    cmd = (
        f"BenchMark merge {csv_a} {csv_b} "
        f"-o {merged} "
        f"--pipeline-version v0.1_test "
        f"--run-label simulation"
    )
    result = run(cmd, capture=True)
    print(result.stdout)
    if result.returncode != 0:
        fail("BenchMark merge failed")

    if not os.path.exists(merged):
        fail("Merged CSV not created")

    with open(merged) as f:
        rows = list(csv.DictReader(f))

    tools = {r["tool_name"] for r in rows}
    if "SimulatedTool_A" not in tools or "SimulatedTool_B" not in tools:
        fail(f"Expected both tools in merged CSV, got: {tools}")
    ok(f"Merged {len(rows)} rows from 2 files")
    ok(f"Tools present: {', '.join(sorted(tools))}")
    ok(f"source_file column: {rows[0].get('source_file', 'MISSING')}")
    ok(f"pipeline_version column: {rows[0].get('pipeline_version', 'MISSING')}")
    return merged


# ── Test 4: reporter CSV column checks ───────────────────────────────────

def test_csv_columns(merged_path):
    section("Test 4: CSV column validation")

    required_cols = [
        "run_id", "session_name", "tool_name", "dataset", "run_date",
        "hostname", "step_num", "step_name",
        "wall_time_s", "cpu_user_s", "cpu_system_s", "cpu_total_s",
        "cpu_efficiency", "peak_mem_mb", "avg_mem_mb",
        "max_threads", "peak_processes",
        "disk_read_mb", "disk_write_mb", "is_summary",
    ]
    with open(merged_path) as f:
        reader = csv.DictReader(f)
        actual_cols = reader.fieldnames or []

    missing = [c for c in required_cols if c not in actual_cols]
    if missing:
        fail(f"Missing expected columns: {missing}")
    ok(f"All {len(required_cols)} required columns present")

    with open(merged_path) as f:
        rows = list(csv.DictReader(f))

    for row in rows:
        try:
            float(row["wall_time_s"])
            float(row["cpu_total_s"])
            float(row["peak_mem_mb"])
        except ValueError as e:
            fail(f"Non-numeric value in critical column: {e}")

    ok("Numeric columns are valid floats")


# ── Test 5: state manager round-trip ─────────────────────────────────────

def test_state_roundtrip():
    section("Test 5: StateManager round-trip (JSON serialisation)")

    with tempfile.TemporaryDirectory() as d:
        from benchmark.state import StateManager

        sm = StateManager(d)
        sm.init("rt_test", "ToolX", "ds_rt", d, "notes", {"hostname": "h1"})

        for i in range(1, 4):
            t = time.time()
            sm.start_step(i, t)
            sm.end_step(i, t + 5, 5.0, 20.0, 1.0,
                        512.0, 400.0, 4, 1, 20.0, 5.0)

        sm.set_pending_label("final_step")
        sm.finalize(total_idle_s=12.0)

        # Reload from disk
        sm2 = StateManager(d)
        sm2.load()
        d2 = sm2.data

        assert d2["status"] == "done", f"Expected done, got {d2['status']}"
        assert len(d2["steps"]) == 3, f"Expected 3 steps, got {len(d2['steps'])}"
        assert d2["total_idle_s"] == 12.0
        ok("3 steps serialised and reloaded correctly")
        ok(f"Status after finalize: {d2['status']}")

        # Test CSV generation from reloaded state
        from benchmark.reporter import generate_csv
        csv_path = os.path.join(d, "test_output.csv")
        n = generate_csv(d2, csv_path)
        assert n == 4, f"Expected 4 rows (3 steps + TOTAL), got {n}"
        ok(f"CSV generated: {n} rows (3 steps + TOTAL)")


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    print(f"\n{BOLD}BenchMark — Integration Test Suite{RESET}")
    print("=" * 50)

    # Check BenchMark is callable
    result = run("BenchMark --version", capture=True, check=False)
    if result.returncode != 0:
        fail("BenchMark not found in PATH. Run install.sh first.")
    ok(f"BenchMark found: {result.stdout.strip()}")

    with tempfile.TemporaryDirectory() as outdir:
        csv_a  = test_run_command(outdir)
        csv_b  = test_run_heavy(outdir)
        merged = test_merge(outdir, csv_a, csv_b)
        test_csv_columns(merged)

    test_state_roundtrip()

    print(f"\n{BOLD}{GREEN}All integration tests passed!{RESET}\n")


if __name__ == "__main__":
    main()
