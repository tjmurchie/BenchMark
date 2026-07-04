"""Background monitoring daemon.

Spawned by `BenchMark go` with start_new_session=True so it survives terminal close.
Communicates via JSON state files in ~/.benchmark/sessions/<session>/
Receives SIGTERM to stop cleanly, SIGUSR1 to mark a step boundary.
"""

import argparse
import logging
import os
import signal
import sys
import time
from typing import Dict, List, Optional

# Ensure the package root is importable regardless of CWD
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark.process_utils import (
    HAS_PSUTIL,
    collect_snapshot,
    find_screen_pid,
    get_cpu_percent_for,
    get_descendants,
    get_system_info,
    record_cpu_baselines,
)
from benchmark.state import StateManager

# ── Tuning constants ────────────────────────────────────────────────────
SAMPLE_INTERVAL_S = 1.0       # how often to sample
IDLE_DEBOUNCE_S = 3.0         # quiet time before declaring idle
ACTIVE_DEBOUNCE_S = 0.5       # activity time before declaring active
IDLE_CPU_THRESH = 0.5         # % CPU threshold for "idle"
STATE_FLUSH_S = 15.0          # max seconds between state flushes
SCREEN_WAIT_TIMEOUT_S = 7200  # wait up to 2 h for the screen session to appear (generous
                              # setup grace; with go's auto-create the screen is there at once)
DEFAULT_IDLE_TIMEOUT_M = 30   # exit after this many minutes of continuous idle
DEFAULT_ORPHAN_TIMEOUT_M = 3  # exit (and auto-write CSV) this long after the screen is killed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("benchmark.daemon")


class MonitorDaemon:
    def __init__(
        self,
        session_name: str,
        state: StateManager,
        idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_M * 60,
        orphan_timeout_s: float = DEFAULT_ORPHAN_TIMEOUT_M * 60,
    ):
        self.session_name = session_name
        self.state = state
        self.screen_pid: Optional[int] = None
        self._running = True
        self._mark_requested = False
        # Set when the daemon exits on its own (idle/orphan timeout) rather than via an
        # explicit `BenchMark stop` (SIGTERM) — triggers auto-writing the CSV on shutdown.
        self._auto_finalize = False

        # Timeout configuration
        self._idle_timeout_s = idle_timeout_s
        self._orphan_timeout_s = orphan_timeout_s
        self._screen_lost_since: Optional[float] = None  # when screen disappeared

        # Active/idle tracking
        self._is_idle = True
        # The idle timeout must not fire before the pipeline has actually started —
        # otherwise a fresh monitor would auto-exit while you're still setting up.
        self._ever_active = False
        self._idle_candidate_since: Optional[float] = None
        self._active_candidate_since: Optional[float] = None

        # Per-step accumulators
        self._step_wall_start: Optional[float] = None
        self._step_cpu_user_base: float = 0.0
        self._step_cpu_sys_base: float = 0.0
        self._step_disk_read_base: int = 0
        self._step_disk_write_base: int = 0
        self._step_mem_samples: List[float] = []
        self._step_peak_mem: float = 0.0
        self._step_max_threads: int = 0
        self._step_max_procs: int = 0

        # Total idle accumulation
        self._total_idle_s: float = 0.0
        self._idle_start: Optional[float] = None

        # CPU% computation baseline
        self._cpu_baselines: Dict[int, float] = {}
        self._cpu_sample_time: float = 0.0

        # State flush
        self._last_flush: float = 0.0

    # ── Signal handlers ───────────────────────────────────────────────

    def _handle_sigterm(self, signum, frame):
        log.info("SIGTERM received — stopping")
        self._running = False

    def _handle_sigusr1(self, signum, frame):
        log.info("SIGUSR1 received — marking step boundary")
        self._mark_requested = True

    # ── Screen discovery ──────────────────────────────────────────────

    def _find_screen(self) -> bool:
        pid = find_screen_pid(self.session_name)
        if pid:
            self.screen_pid = pid
            log.info(f"Screen session '{self.session_name}' found at PID {pid}")
            return True
        return False

    def _wait_for_screen(self) -> bool:
        log.info(f"Waiting for screen session '{self.session_name}' (timeout {SCREEN_WAIT_TIMEOUT_S}s)...")
        deadline = time.time() + SCREEN_WAIT_TIMEOUT_S
        while time.time() < deadline:
            if self._find_screen():
                return True
            time.sleep(2.0)
        return False

    # ── Step lifecycle ────────────────────────────────────────────────

    def _begin_step(self, now: float, snap):
        step_num = self.state.get_next_step_num()
        log.info(f"Step {step_num} starting")
        self._step_wall_start = now
        self._step_cpu_user_base = snap.cpu_user_s
        self._step_cpu_sys_base = snap.cpu_system_s
        self._step_disk_read_base = snap.disk_read_bytes
        self._step_disk_write_base = snap.disk_write_bytes
        self._step_mem_samples = [snap.mem_rss_mb]
        self._step_peak_mem = snap.mem_rss_mb
        self._step_max_threads = snap.num_threads
        self._step_max_procs = snap.num_processes
        # Track idle duration that preceded this step
        if self._idle_start is not None:
            self._total_idle_s += now - self._idle_start
            self._idle_start = None
        self.state.start_step(step_num, now)

    def _close_step(self, now: float, snap):
        if self._step_wall_start is None:
            return
        step_num = self.state.get_current_step_num()
        wall = now - self._step_wall_start
        cpu_user = max(0.0, snap.cpu_user_s - self._step_cpu_user_base)
        cpu_sys = max(0.0, snap.cpu_system_s - self._step_cpu_sys_base)
        disk_r = max(0, snap.disk_read_bytes - self._step_disk_read_base) / (1024 * 1024)
        disk_w = max(0, snap.disk_write_bytes - self._step_disk_write_base) / (1024 * 1024)
        avg_mem = (
            sum(self._step_mem_samples) / len(self._step_mem_samples)
            if self._step_mem_samples else 0.0
        )
        log.info(
            f"Step {step_num} done — wall={wall:.1f}s "
            f"CPU={cpu_user + cpu_sys:.1f}s mem={self._step_peak_mem:.0f}MB"
        )
        self.state.end_step(
            step_num=step_num,
            end_time=now,
            wall_time_s=wall,
            cpu_user_s=cpu_user,
            cpu_system_s=cpu_sys,
            peak_mem_mb=self._step_peak_mem,
            avg_mem_mb=avg_mem,
            max_threads=self._step_max_threads,
            peak_processes=self._step_max_procs,
            disk_read_mb=disk_r,
            disk_write_mb=disk_w,
        )
        self._step_wall_start = None
        self._idle_start = now

    def _update_step_accumulators(self, snap):
        self._step_mem_samples.append(snap.mem_rss_mb)
        if snap.mem_rss_mb > self._step_peak_mem:
            self._step_peak_mem = snap.mem_rss_mb
        if snap.num_threads > self._step_max_threads:
            self._step_max_threads = snap.num_threads
        if snap.num_processes > self._step_max_procs:
            self._step_max_procs = snap.num_processes

    # ── Manual mark ──────────────────────────────────────────────────

    def _apply_mark(self, now: float, snap):
        """Force a step boundary (from SIGUSR1 or pending mark file)."""
        if not self._is_idle and self._step_wall_start is not None:
            self._close_step(now, snap)
            self._is_idle = True
            self._idle_candidate_since = None
            self._active_candidate_since = None
        # Next step will pick up the pending label from the mark file
        log.info("Step boundary marked manually")

    # ── Main loop ─────────────────────────────────────────────────────

    def run(self):
        signal.signal(signal.SIGTERM, self._handle_sigterm)
        signal.signal(signal.SIGINT, self._handle_sigterm)
        signal.signal(signal.SIGUSR1, self._handle_sigusr1)

        if not HAS_PSUTIL:
            log.error("psutil is not installed. Run: pip install psutil")
            self.state.set_error("psutil not installed")
            return

        if not self._wait_for_screen():
            log.error(f"Screen session '{self.session_name}' never appeared")
            self.state.set_error("screen session not found")
            return

        self.state.set_running(self.screen_pid)
        self._idle_start = time.time()

        # Prime CPU baselines
        processes = get_descendants(self.screen_pid)
        self._cpu_baselines = record_cpu_baselines(processes)
        self._cpu_sample_time = time.time()
        self._last_flush = time.time()

        while self._running:
            loop_start = time.time()

            try:
                # Re-discover screen PID if lost
                if not self.screen_pid or not _pid_alive(self.screen_pid):
                    if not self._find_screen():
                        now = time.time()
                        if self._screen_lost_since is None:
                            self._screen_lost_since = now
                            log.warning(
                                f"Screen session lost — will exit in "
                                f"{self._orphan_timeout_s / 60:.0f} min if not found"
                            )
                        elif now - self._screen_lost_since >= self._orphan_timeout_s:
                            log.warning(
                                f"Screen session '{self.session_name}' gone for "
                                f"{(now - self._screen_lost_since) / 60:.0f} min — exiting"
                            )
                            self._running = False
                            self._auto_finalize = True   # screen killed → write the CSV
                        time.sleep(2.0)
                        continue
                    else:
                        self._screen_lost_since = None  # session found again

                processes = get_descendants(self.screen_pid)
                if not processes:
                    time.sleep(1.0)
                    continue

                now = time.time()
                snap = collect_snapshot(processes)
                interval = now - self._cpu_sample_time
                cpu_pct = get_cpu_percent_for(processes, self._cpu_baselines, interval)

                # Refresh CPU baselines for next iteration
                self._cpu_baselines = record_cpu_baselines(processes)
                self._cpu_sample_time = now

                # Idle/active detection with debounce
                currently_idle = snap.is_idle or cpu_pct < IDLE_CPU_THRESH

                if self._is_idle and not currently_idle:
                    if self._active_candidate_since is None:
                        self._active_candidate_since = now
                    elif now - self._active_candidate_since >= ACTIVE_DEBOUNCE_S:
                        self._is_idle = False
                        self._ever_active = True   # first real command has run
                        self._begin_step(now, snap)
                        self._active_candidate_since = None
                        self._idle_candidate_since = None
                else:
                    self._active_candidate_since = None

                if not self._is_idle and currently_idle:
                    if self._idle_candidate_since is None:
                        self._idle_candidate_since = now
                    elif now - self._idle_candidate_since >= IDLE_DEBOUNCE_S:
                        self._close_step(now, snap)
                        self._is_idle = True
                        self._idle_candidate_since = None
                        self._active_candidate_since = None
                elif not self._is_idle:
                    self._idle_candidate_since = None

                # Update running accumulators during active step
                if not self._is_idle:
                    self._update_step_accumulators(snap)

                # Handle manual mark request (SIGUSR1)
                if self._mark_requested:
                    self._apply_mark(now, snap)
                    self._mark_requested = False

                # Idle timeout: exit if continuously idle for too long — but only once
                # the pipeline has actually started, so pre-launch setup time is unlimited.
                if (
                    self._is_idle
                    and self._ever_active
                    and self._idle_start is not None
                    and self._idle_timeout_s > 0
                    and now - self._idle_start >= self._idle_timeout_s
                ):
                    log.warning(
                        f"Idle for {(now - self._idle_start) / 60:.0f} min "
                        f"(timeout={self._idle_timeout_s / 60:.0f} min) — exiting"
                    )
                    self._running = False
                    self._auto_finalize = True   # idle-timeout exit → write the CSV

                # Periodic state flush
                if now - self._last_flush >= STATE_FLUSH_S:
                    self.state.flush()
                    self._last_flush = now

            except Exception as exc:
                log.error(f"Monitor loop error: {exc}", exc_info=True)

            # Sleep the remainder of the sample interval
            elapsed = time.time() - loop_start
            sleep_for = max(0.0, SAMPLE_INTERVAL_S - elapsed)
            time.sleep(sleep_for)

        # ── Shutdown ──
        if not self._is_idle and self._step_wall_start is not None:
            processes = get_descendants(self.screen_pid) if self.screen_pid else []
            snap = collect_snapshot(processes)
            self._close_step(time.time(), snap)

        if self._idle_start is not None:
            self._total_idle_s += time.time() - self._idle_start

        self.state.finalize(total_idle_s=self._total_idle_s)
        # When we exited on our own (idle/orphan timeout, e.g. the run's screen was
        # killed) there's no `BenchMark stop` to write the CSV — so do it here.
        if self._auto_finalize:
            try:
                from datetime import datetime
                from benchmark.reporter import generate_csv
                st = self.state.data
                outdir = st.get("output_dir") or os.getcwd()
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                tool = str(st.get("tool_name", "tool")).replace(" ", "_")
                ds = str(st.get("dataset", "dataset")).replace(" ", "_")
                csv_path = os.path.join(outdir, f"benchmark_{tool}_{ds}_{ts}.csv")
                n = generate_csv(st, csv_path)
                log.info(f"Auto-finalized CSV: {csv_path} ({n} rows)")
            except Exception as exc:
                log.error(f"Auto-finalize CSV failed: {exc}")
        self.state.remove_pid()
        log.info("Daemon exited cleanly")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def main():
    parser = argparse.ArgumentParser(description="BenchMark background daemon")
    parser.add_argument("--session-name", required=True)
    parser.add_argument("--session-dir", required=True)
    parser.add_argument("--tool", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--notes", default="")
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=DEFAULT_IDLE_TIMEOUT_M,
        metavar="MINUTES",
        help=f"Exit after this many minutes of continuous inactivity (0=never, default {DEFAULT_IDLE_TIMEOUT_M})",
    )
    parser.add_argument(
        "--orphan-timeout",
        type=float,
        default=DEFAULT_ORPHAN_TIMEOUT_M,
        metavar="MINUTES",
        help=f"Exit after screen session gone for this long (0=never, default {DEFAULT_ORPHAN_TIMEOUT_M})",
    )
    parser.add_argument(
        "--resume-from", default=None,
        help="Prior session dir to carry completed steps over from (non-contiguous run).",
    )
    args = parser.parse_args()

    # Redirect all logging to daemon.log in the session dir
    log_file = os.path.join(args.session_dir, "daemon.log")
    fh = logging.FileHandler(log_file)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logging.getLogger().handlers = [fh]

    state = StateManager(args.session_dir)
    state.init(
        session_name=args.session_name,
        tool_name=args.tool,
        dataset=args.dataset,
        output_dir=args.output_dir,
        notes=args.notes,
        system_info=get_system_info(),
    )
    if args.resume_from:
        try:
            prior = StateManager(args.resume_from)
            prior.load()
            prior_steps = [s for s in prior.data.get("steps", []) if s.get("status") == "done"]
            stamp = ("[RESUMED — NON-CONTIGUOUS run: steps span a gap, the last pre-resume "
                     "step may be incomplete, and conditions may differ across the gap] ")
            state.seed_from_prior(prior_steps, args.resume_from, stamp)
            log.warning(f"RESUMED from {args.resume_from}: carried over {len(prior_steps)} completed step(s).")
        except Exception as exc:
            log.error(f"--resume-from failed ({exc}) — starting fresh.")
    state.write_pid(os.getpid())

    daemon = MonitorDaemon(
        args.session_name,
        state,
        idle_timeout_s=args.idle_timeout * 60,
        orphan_timeout_s=args.orphan_timeout * 60,
    )
    daemon.run()


if __name__ == "__main__":
    main()
