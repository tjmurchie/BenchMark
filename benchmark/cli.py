"""BenchMark CLI — entry point for all subcommands.

Subcommands
-----------
  go       Start monitoring a screen session (background daemon)
  stop     Stop monitoring, print summary, write CSV
  status   Show status of active sessions
  mark     Label the next step that auto-starts
  rename   Rename a completed step by number
  run      Wrap a single command (no screen session needed)
  merge    Combine multiple run CSVs into one
  analyse  Generate R-based comparison plots
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from datetime import datetime

# Ensure package root on path when invoked directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark import __version__
from benchmark.merger import merge_csvs
from benchmark.reporter import generate_csv, print_summary
from benchmark.state import (
    SESSIONS_DIR,
    StateManager,
    get_session_dir,
    list_active_sessions,
    register_session,
    unregister_session,
)

BENCHMARK_HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAEMON_SCRIPT = os.path.join(BENCHMARK_HOME, "benchmark", "daemon.py")
R_SCRIPT = os.path.join(BENCHMARK_HOME, "R", "benchmark_plots.R")


# ── Helpers ────────────────────────────────────────────────────────────

def _check_psutil():
    try:
        import psutil
    except ImportError:
        print("ERROR: psutil is required. Install it with:")
        print("  pip install psutil")
        print("  or: pip3 install psutil --user")
        sys.exit(1)


def _resolve_session(args) -> str:
    """Return session name from args, auto-detecting if only one is active."""
    name = getattr(args, "screen", None) or getattr(args, "session", None)
    if name:
        return name
    active = list_active_sessions()
    if len(active) == 1:
        return list(active.keys())[0]
    if len(active) == 0:
        print("No active BenchMark sessions. Start one with: BenchMark go")
        sys.exit(1)
    print("Multiple active sessions — specify one with --screen:")
    for name in active:
        print(f"  {name}")
    sys.exit(1)


def _make_session_dir(session_name: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(SESSIONS_DIR, f"{session_name}_{ts}")
    os.makedirs(path, exist_ok=True)
    return path


def _daemon_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


# ── Subcommand implementations ─────────────────────────────────────────

def cmd_go(args):
    """Start monitoring a screen session in the background."""
    _check_psutil()

    session_name = args.screen or _auto_session_name()
    output_dir = args.output or os.getcwd()
    os.makedirs(output_dir, exist_ok=True)

    # Check for already-active session with the same name
    existing = get_session_dir(session_name)
    if existing:
        sm = StateManager(existing)
        pid = sm.read_pid()
        if pid and _daemon_pid_alive(pid):
            print(f"Session '{session_name}' is already active (daemon PID {pid}).")
            print("Use 'BenchMark stop' to stop it first, or choose a different --screen name.")
            sys.exit(1)

    session_dir = _make_session_dir(session_name)
    register_session(session_name, session_dir)

    cmd = [
        sys.executable, DAEMON_SCRIPT,
        "--session-name", session_name,
        "--session-dir", session_dir,
        "--tool", args.tool,
        "--dataset", args.dataset,
        "--output-dir", output_dir,
        "--notes", args.notes or "",
    ]

    log_path = os.path.join(session_dir, "daemon.log")
    with open(log_path, "w") as logf:
        proc = subprocess.Popen(
            cmd,
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )

    # Wait briefly for daemon to write its PID file
    pid_file = os.path.join(session_dir, "daemon.pid")
    for _ in range(20):
        if os.path.exists(pid_file):
            break
        time.sleep(0.1)

    print(f"\nBenchMark monitoring started")
    print(f"  Session : {session_name}")
    print(f"  Tool    : {args.tool}")
    print(f"  Dataset : {args.dataset}")
    print(f"  Output  : {output_dir}")
    print(f"  Log     : {log_path}")
    print(f"\nWaiting for screen session '{session_name}'...")
    print("Steps are detected automatically when programs start/stop running.")
    print(f"Label the next step with:  BenchMark mark \"step description\"")
    print(f"Stop with:                 BenchMark stop")
    if not args.screen:
        print(f"\nTip: start your screen session as: screen -S {session_name}")


def _auto_session_name():
    from datetime import date
    return f"benchmark_{date.today().strftime('%Y%m%d')}"


def cmd_stop(args):
    """Stop monitoring and generate the CSV report."""
    session_name = _resolve_session(args)
    session_dir = get_session_dir(session_name)
    if not session_dir:
        print(f"No active session named '{session_name}'.")
        sys.exit(1)

    sm = StateManager(session_dir)
    pid = sm.read_pid()

    if pid and _daemon_pid_alive(pid):
        print(f"Stopping daemon (PID {pid})...")
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            if not _daemon_pid_alive(pid):
                break
            time.sleep(0.2)

    # Load final state and generate CSV
    sm.load()
    state = sm.data

    if not state:
        print("Error: could not load session state.")
        sys.exit(1)

    output_dir = args.output or state.get("output_dir") or os.getcwd()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_tool = state.get("tool_name", "tool").replace(" ", "_")
    safe_dataset = state.get("dataset", "dataset").replace(" ", "_")
    csv_name = f"benchmark_{safe_tool}_{safe_dataset}_{ts}.csv"
    csv_path = os.path.join(output_dir, csv_name)

    n = generate_csv(state, csv_path)
    print_summary(state)
    print(f"CSV written: {csv_path}  ({n} rows)")

    unregister_session(session_name)


def cmd_status(args):
    """Show all active monitoring sessions."""
    active = list_active_sessions()
    if not active:
        print("No active BenchMark sessions.")
        return

    print(f"\nActive sessions ({len(active)}):")
    for name, session_dir in active.items():
        sm = StateManager(session_dir)
        pid = sm.read_pid()
        alive = _daemon_pid_alive(pid) if pid else False
        sm.load()
        state = sm.data
        tool = state.get("tool_name", "?")
        dataset = state.get("dataset", "?")
        steps_done = len([s for s in state.get("steps", []) if s.get("status") == "done"])
        current_step = state.get("current_step_num", 0)
        status = state.get("status", "?")
        indicator = "●" if alive else "○"
        print(f"  {indicator} {name:<30} {tool}/{dataset}  steps: {steps_done}  status: {status}")
        if not alive and status not in ("done", "error"):
            print(f"    ⚠ Daemon not running. Session may have been interrupted.")


def cmd_mark(args):
    """Label the next auto-detected step."""
    session_name = _resolve_session(args)
    session_dir = get_session_dir(session_name)
    if not session_dir:
        print(f"No active session '{session_name}'.")
        sys.exit(1)

    label = args.label or args.name or "unnamed_step"
    sm = StateManager(session_dir)
    sm.set_pending_label(label)

    # Also send SIGUSR1 to force a step boundary right now if session is active
    pid = sm.read_pid()
    if pid and _daemon_pid_alive(pid):
        os.kill(pid, signal.SIGUSR1)
        print(f"Step boundary marked. Next step will be labelled: '{label}'")
    else:
        print(f"Label '{label}' queued for next step (daemon not currently running).")


def cmd_rename(args):
    """Rename a completed step by its step number."""
    session_name = _resolve_session(args)
    session_dir = get_session_dir(session_name)
    if not session_dir:
        print(f"No active session '{session_name}'.")
        sys.exit(1)

    sm = StateManager(session_dir)
    sm.load()
    sm.rename_step(args.step_num, args.new_name)
    print(f"Step {args.step_num} renamed to '{args.new_name}'.")


def cmd_run(args):
    """Wrap a single command — monitor it directly without needing a screen session."""
    _check_psutil()
    import psutil

    tool_args = [a for a in args.command if a != "--"]
    if not tool_args:
        print("Error: provide a command after --")
        sys.exit(1)

    session_name = args.name or f"run_{datetime.now().strftime('%H%M%S')}"
    output_dir = args.output or os.getcwd()
    os.makedirs(output_dir, exist_ok=True)
    session_dir = _make_session_dir(session_name)

    from benchmark.process_utils import collect_snapshot, get_descendants, get_system_info
    from benchmark.state import StateManager

    sm = StateManager(session_dir)
    sm.init(
        session_name=session_name,
        tool_name=args.tool,
        dataset=args.dataset,
        output_dir=output_dir,
        notes=args.notes or "",
        system_info=get_system_info(),
    )

    step_num = 1
    sm.start_step(step_num, time.time())

    mem_samples = []
    peak_mem = 0.0
    max_threads = 0
    max_procs = 1
    last_cpu_user = 0.0
    last_cpu_sys = 0.0
    last_disk_r = 0
    last_disk_w = 0

    start = time.time()
    proc = subprocess.Popen(tool_args)
    ps_proc = psutil.Process(proc.pid)

    # Monitor without calling proc.poll() so the zombie isn't reaped until we
    # have read its final CPU times. We detect exit via psutil status instead.
    while True:
        try:
            all_procs = [ps_proc] + ps_proc.children(recursive=True)
            mem = sum(p.memory_info().rss for p in all_procs) / (1024 * 1024)
            threads = sum(p.num_threads() for p in all_procs)
            mem_samples.append(mem)
            if mem > peak_mem:
                peak_mem = mem
            if threads > max_threads:
                max_threads = threads
            if len(all_procs) > max_procs:
                max_procs = len(all_procs)
            # Capture CPU and I/O on every sample
            cpu = ps_proc.cpu_times()
            last_cpu_user = cpu.user
            last_cpu_sys  = cpu.system
            try:
                io = ps_proc.io_counters()
                last_disk_r = io.read_bytes
                last_disk_w = io.write_bytes
            except (psutil.AccessDenied, AttributeError):
                pass
            # Detect zombie: process exited but not yet reaped — read final CPU here
            if ps_proc.status() == psutil.STATUS_ZOMBIE:
                final = ps_proc.cpu_times()
                last_cpu_user = final.user
                last_cpu_sys  = final.system
                break
        except psutil.NoSuchProcess:
            break   # process already reaped by OS (race — use last captured values)
        except psutil.AccessDenied:
            pass
        time.sleep(0.2)     # shorter interval for better resolution on fast steps

    end = time.time()
    proc.wait()             # reap the zombie now
    exit_code = proc.returncode

    cpu_user = last_cpu_user
    cpu_sys  = last_cpu_sys
    disk_r   = last_disk_r / (1024 * 1024)
    disk_w   = last_disk_w / (1024 * 1024)

    avg_mem = sum(mem_samples) / len(mem_samples) if mem_samples else 0.0

    sm.end_step(
        step_num=1,
        end_time=end,
        wall_time_s=end - start,
        cpu_user_s=cpu_user,
        cpu_system_s=cpu_sys,
        peak_mem_mb=peak_mem,
        avg_mem_mb=avg_mem,
        max_threads=max_threads,
        peak_processes=max_procs,
        disk_read_mb=disk_r,
        disk_write_mb=disk_w,
    )
    sm.finalize()

    sm.load()
    state = sm.data
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_tool = args.tool.replace(" ", "_")
    csv_path = os.path.join(output_dir, f"benchmark_{safe_tool}_{ts}.csv")
    n = generate_csv(state, csv_path)
    print_summary(state)
    print(f"CSV written: {csv_path}  ({n} rows)  exit code: {exit_code}")


def cmd_merge(args):
    """Merge multiple benchmark CSVs."""
    extra = {}
    if args.pipeline_version:
        extra["pipeline_version"] = args.pipeline_version
    if args.run_label:
        extra["run_label"] = args.run_label

    output = args.output
    if not output:
        output = "benchmark_merged.csv"

    n = merge_csvs(args.inputs, output, extra_meta=extra or None)
    print(f"Merged {len(args.inputs)} file(s) → {output}  ({n} rows)")


def cmd_analyse(args):
    """Run R analysis and generate publication-quality plots."""
    if not os.path.isfile(args.input):
        print(f"Error: input file not found: {args.input}")
        sys.exit(1)

    output_dir = args.output_dir or os.path.dirname(os.path.abspath(args.input))
    os.makedirs(output_dir, exist_ok=True)

    if not os.path.isfile(R_SCRIPT):
        print(f"Error: R script not found at {R_SCRIPT}")
        sys.exit(1)

    r_args = [args.input, output_dir]
    if args.title:
        r_args.append(args.title)

    print(f"Running R analysis on {args.input}...")
    print(f"Plots → {output_dir}")

    result = subprocess.run(
        ["Rscript", "--vanilla", R_SCRIPT] + r_args,
        capture_output=False,
    )
    if result.returncode != 0:
        print("R script exited with errors. Check output above.")
        sys.exit(result.returncode)
    print("Analysis complete.")


# ── Argument parser ────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="BenchMark",
        description=(
            "Pipeline-aware resource monitoring for ancient DNA and metagenomic "
            "classification tools.\n\n"
            "BenchMark attaches to a screen session, tracks CPU time, memory, "
            "disk I/O, and wall-clock time per pipeline step (pausing the timer "
            "automatically when the session is idle), and outputs a per-step CSV "
            "suitable for cross-tool comparison and publication figures."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
WORKFLOW OVERVIEW
-----------------
  1. Start your screen session:
       screen -S fillet_run

  2. Start monitoring (returns to shell immediately):
       BenchMark go --screen fillet_run --tool Fillet --dataset calculus_100k

  3. Label steps before running them in screen (optional but recommended):
       BenchMark mark "adapter_trim"
       BenchMark mark "alignment"

  4. Check progress:
       BenchMark status

  5. Stop and write CSV:
       BenchMark stop

  6. Combine runs from multiple tools:
       BenchMark merge fillet.csv kraken2.csv megan7.csv -o comparison.csv

  7. Generate publication plots:
       BenchMark analyse comparison.csv --output-dir ./plots

STEP DETECTION
--------------
  Steps are detected automatically: when a command starts in screen a new step
  begins; when the shell goes idle (only bash/zsh running, CPU <0.5%) for 3 s
  the step ends. No changes to your pipeline scripts are required.

  To label steps, call:  BenchMark mark "step_name"  BEFORE running each step.
  To rename retroactively: BenchMark rename 1 "adapter_trim"

WHAT IS MEASURED (per step + TOTAL summary row)
------------------------------------------------
  wall_time_s     Active wall time (idle time excluded)
  cpu_user_s      User-mode CPU time, all threads summed
  cpu_system_s    Kernel-mode CPU time, all threads summed
  cpu_total_s     Total CPU time (user + system)
  cpu_efficiency  cpu_total / wall_time  (>1 = multi-threaded)
  peak_mem_mb     Maximum RSS during the step
  avg_mem_mb      Mean RSS during the step
  max_threads     Maximum thread count
  disk_read_mb    Total bytes read from disk
  disk_write_mb   Total bytes written to disk

SESSION STATE
-------------
  Active sessions and daemon state are stored in ~/.benchmark/sessions/
  Daemon logs:  ~/.benchmark/sessions/<session>/daemon.log

ACCURACY
--------
  Validated against GNU time (-v) across 5 workload types (N=3 replicates).
  For workloads >2 s: mean deviation 2.7% wall, 2.4% CPU, 1.0% memory.
  Absolute overhead ~0.06 s per step (0.003% error on a 30-min step).
  Full validation: docs/supplementary_validation.md
  Reproduce: python3 tests/validation_study.py --reps 3

EXAMPLES
--------
  BenchMark go --screen kr2_run --tool Kraken2 --dataset calculus_sim \\
               --notes "db=k2_standard_2024" --output ~/bench/
  BenchMark mark "database_build"
  BenchMark stop --screen kr2_run
  BenchMark merge kr2.csv fillet.csv megan.csv -o combined.csv \\
             --pipeline-version v1.0
  BenchMark analyse combined.csv --output-dir ./plots \\
             --title "Ancient DNA Classifier Comparison"
  BenchMark run --tool Kraken2 --dataset test -- \\
             kraken2 --db /db --output out.tsv reads.fastq.gz
""",
    )
    parser.add_argument("--version", action="version", version=f"BenchMark {__version__}")

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # ── go ──
    p_go = sub.add_parser("go", help="Start monitoring a screen session")
    p_go.add_argument("--screen", "-s", metavar="NAME",
                      help="Screen session name (auto-detected if only one active)")
    p_go.add_argument("--tool", "-t", required=True, metavar="NAME",
                      help="Tool/pipeline being benchmarked (e.g. Kraken2, Fillet)")
    p_go.add_argument("--dataset", "-d", required=True, metavar="NAME",
                      help="Dataset name (e.g. calculus_sim_100k)")
    p_go.add_argument("--output", "-o", metavar="DIR",
                      help="Directory for CSV output (default: current directory)")
    p_go.add_argument("--notes", "-n", metavar="TEXT",
                      help="Free-text notes (e.g. database version, parameters)")
    p_go.set_defaults(func=cmd_go)

    # ── stop ──
    p_stop = sub.add_parser("stop", help="Stop monitoring and write CSV report")
    p_stop.add_argument("--screen", "-s", metavar="NAME",
                        help="Screen session name (auto-detected if only one active)")
    p_stop.add_argument("--output", "-o", metavar="DIR",
                        help="Override CSV output directory")
    p_stop.set_defaults(func=cmd_stop)

    # ── status ──
    p_status = sub.add_parser("status", help="Show active monitoring sessions")
    p_status.set_defaults(func=cmd_status)

    # ── mark ──
    p_mark = sub.add_parser("mark", help="Label the next step (run before starting a pipeline step)")
    p_mark.add_argument("label", nargs="?", metavar="LABEL",
                        help="Name for the next step (e.g. 'database_build')")
    p_mark.add_argument("--name", metavar="LABEL", help="Alternative to positional label")
    p_mark.add_argument("--screen", "-s", metavar="NAME")
    p_mark.set_defaults(func=cmd_mark)

    # ── rename ──
    p_rename = sub.add_parser("rename", help="Retroactively rename a completed step")
    p_rename.add_argument("step_num", type=int, metavar="STEP_NUM",
                          help="Step number to rename (from status output)")
    p_rename.add_argument("new_name", metavar="NEW_NAME")
    p_rename.add_argument("--screen", "-s", metavar="NAME")
    p_rename.set_defaults(func=cmd_rename)

    # ── run ──
    p_run = sub.add_parser("run", help="Wrap a single command (no screen session needed)")
    p_run.add_argument("--tool", "-t", required=True, metavar="NAME")
    p_run.add_argument("--dataset", "-d", required=True, metavar="NAME")
    p_run.add_argument("--name", metavar="SESSION_NAME",
                       help="Session name for the output CSV")
    p_run.add_argument("--output", "-o", metavar="DIR")
    p_run.add_argument("--notes", "-n", metavar="TEXT")
    p_run.add_argument("command", nargs=argparse.REMAINDER,
                       help="Command to run (after --)")
    p_run.set_defaults(func=cmd_run)

    # ── merge ──
    p_merge = sub.add_parser("merge", help="Combine multiple benchmark CSVs")
    p_merge.add_argument("inputs", nargs="+", metavar="CSV_FILE",
                         help="Input CSV files to merge")
    p_merge.add_argument("-o", "--output", metavar="OUTPUT_CSV",
                         help="Output path (default: benchmark_merged.csv)")
    p_merge.add_argument("--pipeline-version", metavar="VER",
                         help="Pipeline version tag added to all rows")
    p_merge.add_argument("--run-label", metavar="LABEL",
                         help="Run label tag added to all rows")
    p_merge.set_defaults(func=cmd_merge)

    # ── analyse ──
    p_analyse = sub.add_parser("analyse", aliases=["analyze"],
                               help="Generate R comparison plots from merged CSV")
    p_analyse.add_argument("input", metavar="CSV_FILE",
                           help="Merged CSV to analyse (from BenchMark merge)")
    p_analyse.add_argument("--output-dir", "-o", metavar="DIR",
                           help="Directory for plot output (default: same as CSV)")
    p_analyse.add_argument("--title", metavar="TITLE",
                           help="Title for plots (e.g. 'Ancient DNA Classifier Comparison')")
    p_analyse.set_defaults(func=cmd_analyse)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
