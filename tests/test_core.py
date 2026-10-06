"""Unit tests for BenchMark core modules."""

import csv
import json
import os
import sys
import tempfile
import time
import unittest

# Ensure package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark.merger import merge_csvs
from benchmark.reporter import _build_rows, generate_csv
from benchmark.state import StateManager


# ── StateManager tests ──────────────────────────────────────────────────

class TestStateManager(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.sm = StateManager(self.tmpdir)
        self.sm.init(
            session_name="test_session",
            tool_name="TestTool",
            dataset="sim_dataset",
            output_dir=self.tmpdir,
            notes="unit test run",
            system_info={"hostname": "testhost", "cpu_count_logical": 8, "total_ram_gb": 32.0},
        )

    def test_init_creates_state_file(self):
        state_file = os.path.join(self.tmpdir, "state.json")
        self.assertTrue(os.path.exists(state_file))

    def test_init_state_content(self):
        self.sm.load()
        self.assertEqual(self.sm.data["tool_name"], "TestTool")
        self.assertEqual(self.sm.data["dataset"], "sim_dataset")
        self.assertEqual(self.sm.data["status"], "initializing")

    def test_seed_from_prior_resume(self):
        prior = {"step_num": 1, "step_name": "blastn", "status": "done",
                 "wall_time_s": 10.0, "cpu_user_s": 8.0, "cpu_system_s": 1.0,
                 "cpu_total_s": 9.0, "peak_mem_mb": 100.0, "avg_mem_mb": 80.0,
                 "max_threads": 4, "peak_processes": 2,
                 "disk_read_mb": 5.0, "disk_write_mb": 2.0}
        self.sm.seed_from_prior([prior], "/prior/dir", "[RESUMED] ")
        self.sm.load()
        self.assertTrue(self.sm.data["resumed"])
        self.assertEqual(self.sm.data["resumed_from"], "/prior/dir")
        self.assertEqual(self.sm.data["current_step_num"], 1)      # continues numbering
        self.assertEqual(self.sm.data["steps"][0]["step_name"], "blastn")  # carried over
        self.assertTrue(self.sm.data["notes"].startswith("[RESUMED] "))    # CSV stamp

    def test_set_running(self):
        self.sm.set_running(12345)
        self.sm.load()
        self.assertEqual(self.sm.data["status"], "running")
        self.assertEqual(self.sm.data["screen_pid"], 12345)

    def test_step_lifecycle(self):
        now = time.time()
        self.sm.set_running(12345)
        self.sm.start_step(1, now)
        self.sm.end_step(
            step_num=1, end_time=now + 10.0,
            wall_time_s=10.0, cpu_user_s=35.0, cpu_system_s=2.0,
            peak_mem_mb=4096.0, avg_mem_mb=3000.0,
            max_threads=16, peak_processes=4,
            disk_read_mb=500.0, disk_write_mb=200.0,
        )
        self.sm.load()
        steps = self.sm.data["steps"]
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["step_name"], "step_01")
        self.assertAlmostEqual(steps[0]["wall_time_s"], 10.0)
        self.assertAlmostEqual(steps[0]["cpu_total_s"], 37.0)
        self.assertAlmostEqual(steps[0]["peak_mem_mb"], 4096.0)

    def test_step_rename(self):
        self.sm.start_step(1, time.time())
        self.sm.end_step(1, time.time() + 5, 5.0, 10.0, 0.5,
                         2048.0, 1500.0, 8, 2, 100.0, 50.0)
        self.sm.rename_step(1, "database_build")
        self.sm.load()
        self.assertEqual(self.sm.data["steps"][0]["step_name"], "database_build")

    def test_pending_label(self):
        self.sm.set_pending_label("alignment_step")
        label = self.sm.consume_pending_label()
        self.assertEqual(label, "alignment_step")
        # Second consume returns None
        self.assertIsNone(self.sm.consume_pending_label())

    def test_multi_step_sequence(self):
        now = time.time()
        for i in range(1, 4):
            self.sm.start_step(i, now + i * 10)
            self.sm.end_step(i, now + i * 10 + 8, 8.0, 24.0, 1.0,
                             1000.0, 800.0, 8, 2, 50.0, 10.0)
        self.sm.load()
        self.assertEqual(len(self.sm.data["steps"]), 3)
        self.assertEqual(self.sm.data["steps"][2]["step_name"], "step_03")

    def test_finalize(self):
        self.sm.finalize(total_idle_s=45.0)
        self.sm.load()
        self.assertEqual(self.sm.data["status"], "done")
        self.assertAlmostEqual(self.sm.data["total_idle_s"], 45.0)
        self.assertIsNotNone(self.sm.data["end_time"])

    def test_pid_file(self):
        self.sm.write_pid(99999)
        self.assertEqual(self.sm.read_pid(), 99999)
        self.sm.remove_pid()
        self.assertIsNone(self.sm.read_pid())


# ── Reporter tests ────────────────────────────────────────────────────────

class TestReporter(unittest.TestCase):

    def _make_state(self, n_steps=2):
        """Construct a minimal state dict with n_steps completed steps."""
        steps = []
        for i in range(1, n_steps + 1):
            steps.append({
                "step_num": i,
                "step_name": f"step_{i:02d}",
                "start_time": "2026-05-17T10:00:00+00:00",
                "end_time":   "2026-05-17T10:00:30+00:00",
                "status":     "done",
                "wall_time_s":    30.0 * i,
                "cpu_user_s":     100.0 * i,
                "cpu_system_s":   5.0 * i,
                "cpu_total_s":    105.0 * i,
                "peak_mem_mb":    2048.0,
                "avg_mem_mb":     1500.0,
                "max_threads":    16,
                "peak_processes": 4,
                "disk_read_mb":   200.0,
                "disk_write_mb":  50.0,
            })
        return {
            "schema_version": 1,
            "session_name": "test_session",
            "tool_name": "TestTool",
            "dataset": "sim_dataset",
            "start_time": "2026-05-17T10:00:00+00:00",
            "end_time":   "2026-05-17T10:10:00+00:00",
            "notes": "",
            "system_info": {
                "hostname": "testhost",
                "cpu_model": "Intel Xeon",
                "cpu_count_logical": 32,
                "cpu_count_physical": 16,
                "total_ram_gb": 128.0,
            },
            "steps": steps,
        }

    def test_build_rows_count(self):
        state = self._make_state(n_steps=3)
        rows = _build_rows(state)
        # 3 steps + 1 TOTAL
        self.assertEqual(len(rows), 4)

    def test_summary_row_is_last(self):
        rows = _build_rows(self._make_state(2))
        self.assertTrue(rows[-1]["is_summary"])
        self.assertEqual(rows[-1]["step_name"], "TOTAL")

    def test_summary_wall_is_sum(self):
        rows = _build_rows(self._make_state(2))
        step_wall = sum(r["wall_time_s"] for r in rows if not r["is_summary"])
        self.assertAlmostEqual(rows[-1]["wall_time_s"], step_wall)

    def test_summary_peak_mem_is_max(self):
        state = self._make_state(2)
        state["steps"][0]["peak_mem_mb"] = 3000.0
        state["steps"][1]["peak_mem_mb"] = 5000.0
        rows = _build_rows(state)
        self.assertAlmostEqual(rows[-1]["peak_mem_mb"], 5000.0)

    def test_cpu_efficiency_field(self):
        rows = _build_rows(self._make_state(1))
        eff = float(rows[0]["cpu_efficiency"])
        # 105 CPU / 30 wall ≈ 3.5
        self.assertAlmostEqual(eff, 3.5, places=1)

    def test_generate_csv_file(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "test.csv")
            n = generate_csv(self._make_state(2), out)
            self.assertTrue(os.path.exists(out))
            self.assertEqual(n, 3)  # 2 steps + TOTAL
            with open(out) as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[-1]["step_name"], "TOTAL")

    def test_empty_state_produces_no_rows(self):
        state = self._make_state(0)
        rows = _build_rows(state)
        self.assertEqual(len(rows), 0)


# ── Merger tests ──────────────────────────────────────────────────────────

class TestMerger(unittest.TestCase):

    def _write_csv(self, path, rows):
        if not rows:
            return
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    def _sample_rows(self, tool="ToolA", dataset="ds1", n=2):
        rows = []
        for i in range(1, n + 1):
            rows.append({
                "run_id": f"{tool}_run",
                "session_name": f"{tool}_sess",
                "tool_name": tool,
                "dataset": dataset,
                "run_date": "2026-05-17",
                "step_num": i,
                "step_name": f"step_{i:02d}",
                "wall_time_s": 30.0 * i,
                "cpu_total_s": 90.0 * i,
                "cpu_efficiency": "",
                "peak_mem_mb": 2048.0,
                "avg_mem_mb": 1500.0,
                "max_threads": 16,
                "disk_read_mb": 100.0,
                "disk_write_mb": 50.0,
                "is_summary": False,
            })
        # Add TOTAL row
        rows.append({
            **rows[0],
            "step_num": 0, "step_name": "TOTAL",
            "wall_time_s": sum(r["wall_time_s"] for r in rows),
            "is_summary": True,
        })
        return rows

    def test_merge_two_files(self):
        with tempfile.TemporaryDirectory() as d:
            f1 = os.path.join(d, "a.csv")
            f2 = os.path.join(d, "b.csv")
            self._write_csv(f1, self._sample_rows("Kraken2", "ds1", 2))
            self._write_csv(f2, self._sample_rows("Fillet", "ds1", 3))
            out = os.path.join(d, "merged.csv")
            n = merge_csvs([f1, f2], out)
            self.assertTrue(os.path.exists(out))
            with open(out) as f:
                rows = list(csv.DictReader(f))
            tools = {r["tool_name"] for r in rows}
            self.assertIn("Kraken2", tools)
            self.assertIn("Fillet", tools)
            self.assertEqual(n, len(rows))

    def test_merge_adds_source_file(self):
        with tempfile.TemporaryDirectory() as d:
            f1 = os.path.join(d, "run1.csv")
            self._write_csv(f1, self._sample_rows("ToolA", "ds", 1))
            out = os.path.join(d, "merged.csv")
            merge_csvs([f1], out)
            with open(out) as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["source_file"], "run1.csv")

    def test_merge_adds_extra_meta(self):
        with tempfile.TemporaryDirectory() as d:
            f1 = os.path.join(d, "r.csv")
            self._write_csv(f1, self._sample_rows("ToolA", "ds", 1))
            out = os.path.join(d, "merged.csv")
            merge_csvs([f1], out, extra_meta={"pipeline_version": "v2.1"})
            with open(out) as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["pipeline_version"], "v2.1")

    def test_merge_derives_total_io(self):
        with tempfile.TemporaryDirectory() as d:
            rows = self._sample_rows("ToolA", "ds", 1)
            rows[0]["disk_read_mb"] = "100.0"
            rows[0]["disk_write_mb"] = "50.0"
            f1 = os.path.join(d, "r.csv")
            self._write_csv(f1, rows)
            out = os.path.join(d, "merged.csv")
            merge_csvs([f1], out)
            with open(out) as f:
                result = list(csv.DictReader(f))
            self.assertAlmostEqual(float(result[0]["total_io_mb"]), 150.0)

    def test_merge_skips_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            f1 = os.path.join(d, "exists.csv")
            self._write_csv(f1, self._sample_rows("ToolA", "ds", 1))
            out = os.path.join(d, "merged.csv")
            n = merge_csvs([f1, "/nonexistent/file.csv"], out)
            self.assertGreater(n, 0)


# ── Process utils tests (light — no real screen session) ──────────────────

class TestProcessUtils(unittest.TestCase):

    def test_list_screen_sessions_returns_list(self):
        from benchmark.process_utils import list_screen_sessions
        result = list_screen_sessions()
        self.assertIsInstance(result, list)

    def test_find_screen_pid_nonexistent(self):
        from benchmark.process_utils import find_screen_pid
        pid = find_screen_pid("benchmark_nonexistent_zzzz")
        self.assertIsNone(pid)

    def test_get_system_info_returns_dict(self):
        from benchmark.process_utils import get_system_info
        info = get_system_info()
        self.assertIsInstance(info, dict)
        self.assertIn("hostname", info)

    def test_get_descendants_self(self):
        from benchmark.process_utils import get_descendants, HAS_PSUTIL
        if not HAS_PSUTIL:
            self.skipTest("psutil not installed")
        procs = get_descendants(os.getpid())
        pids = [p.pid for p in procs]
        self.assertIn(os.getpid(), pids)

    def test_collect_snapshot_self(self):
        from benchmark.process_utils import collect_snapshot, get_descendants, HAS_PSUTIL
        if not HAS_PSUTIL:
            self.skipTest("psutil not installed")
        procs = get_descendants(os.getpid())
        snap = collect_snapshot(procs)
        self.assertGreater(snap.mem_rss_mb, 0)
        self.assertGreater(snap.num_threads, 0)
        self.assertGreater(snap.cpu_user_s, 0)


# ── CPU-time accumulation regression (daemon step lifecycle) ───────────────
#
# Real-world bug this covers: BenchMark's primary documented usage pattern is
# "BenchMark mark 'step'; run one command to completion; BenchMark mark 'next step'".
# The daemon's old _close_step() computed CPU time as a single before/after snapshot
# diff (collect_snapshot() at step-start vs. collect_snapshot() at step-end) -- but
# collect_snapshot() only sums CPU time across processes that are CURRENTLY ALIVE at
# the instant it's called. By the time a step's closing mark fires, the step's own
# dominant work process has already exited (that's WHY the mark fires -- the shell
# went idle / the next command started), so the end-of-step snapshot never includes
# it, and the recorded cpu_total_s silently read ~0 regardless of how much real CPU
# the process used. Confirmed live on a real multi-day production blastn run (every
# completed step read 0.0-5.1s of CPU time despite using up to 99 threads for days).
# Fixed by accumulating per-poll deltas (get_cpu_time_delta_for) into a running total
# throughout the step instead of diffing two point-in-time snapshots.

class TestDaemonCpuAccumulation(unittest.TestCase):

    def test_step_lifecycle_captures_cpu_after_process_exits(self):
        """The exact bug scenario: burn real CPU in a child process, let it fully exit,
        THEN close the step -- recorded cpu_total_s must still reflect the real CPU used,
        not ~0."""
        from benchmark.process_utils import HAS_PSUTIL
        if not HAS_PSUTIL:
            self.skipTest("psutil not installed")
        import subprocess
        import psutil as _psutil
        from benchmark.daemon import MonitorDaemon
        from benchmark.process_utils import (
            collect_snapshot, get_cpu_time_delta_for, record_cpu_baselines_split,
        )

        tmpdir = tempfile.mkdtemp()
        state = StateManager(tmpdir)
        state.init(
            session_name="test_cpu_accum", tool_name="TestTool", dataset="sim",
            output_dir=tmpdir, notes="", system_info={},
        )
        d = MonitorDaemon(session_name="test_cpu_accum", state=state)

        this_proc = _psutil.Process(os.getpid())
        processes = [this_proc] + this_proc.children(recursive=True)
        snap0 = collect_snapshot(processes)
        now = time.time()
        d._begin_step(now, snap0)

        # Real, measurable CPU burn in a short-lived child -- not mocked.
        burner = subprocess.Popen(
            [sys.executable, "-c",
             "import time\nend = time.time() + 1.2\nx = 0\n"
             "while time.time() < end:\n    x += 1\n"],
        )
        baseline = record_cpu_baselines_split(processes)
        deadline = time.time() + 2.0
        while burner.poll() is None and time.time() < deadline:
            time.sleep(0.2)
            processes = [this_proc] + this_proc.children(recursive=True)
            du, ds = get_cpu_time_delta_for(processes, baseline)
            d._update_step_accumulators(collect_snapshot(processes), du, ds)
            baseline = record_cpu_baselines_split(processes)
        burner.wait(timeout=5)

        # Close the step only AFTER the burner has fully exited -- collect_snapshot() here
        # will NOT see it anymore, exactly reproducing the real driver's mark-after-command
        # -finishes pattern.
        processes = [this_proc] + this_proc.children(recursive=True)
        final_snap = collect_snapshot(processes)
        self.assertNotIn(burner.pid, final_snap.pids, "sanity check: burner should be gone")
        d._close_step(time.time(), final_snap)

        steps = state.data["steps"]
        self.assertEqual(len(steps), 1)
        self.assertGreater(
            steps[0]["cpu_total_s"], 0.3,
            f"cpu_total_s={steps[0]['cpu_total_s']} should reflect the ~1.2s real CPU burn "
            "even though the burner process had already exited before the step was closed "
            "-- this is the exact failure mode of the original before/after-snapshot bug."
        )


# ── Step-fragmentation regression tests ─────────────────────────────────
# Real-world bug: a marked step (e.g. `BenchMark mark "blast"` before a long BLASTn-vs-NT
# job) has a transient CPU dip below IDLE_CPU_THRESH during an I/O-bound phase. The old
# idle/active auto-detection unconditionally closed the step on the dip and opened a new
# one when activity resumed -- but the pending-label file was already consumed when the
# marked step began, so the reopened step fell back to a generic step_NN name. One logical
# step's wall/CPU got silently fragmented across several anonymously-labeled records.
# Confirmed live on a real multi-day production blastn run (one step split into 4 records,
# 3 of them generically named). Fixed by only closing a step on auto-idle-detection when it
# was NOT explicitly marked; a marked step's transient dip now just pauses accumulation and
# the step stays open until a real mark (or shutdown) closes it.

class TestDaemonStepFragmentation(unittest.TestCase):

    def _fresh_daemon(self, session_name):
        from benchmark.daemon import MonitorDaemon
        tmpdir = tempfile.mkdtemp()
        state = StateManager(tmpdir)
        state.init(
            session_name=session_name, tool_name="TestTool", dataset="sim",
            output_dir=tmpdir, notes="", system_info={},
        )
        return MonitorDaemon(session_name=session_name, state=state), state

    @staticmethod
    def _snap(mem=100.0, threads=4, procs=1, disk_r=0, disk_w=0, is_idle=False):
        from benchmark.process_utils import ProcessSnapshot
        return ProcessSnapshot(
            timestamp=time.time(), mem_rss_mb=mem, num_threads=threads,
            num_processes=procs, disk_read_bytes=disk_r, disk_write_bytes=disk_w,
            is_idle=is_idle,
        )

    def _drive_active_transition(self, d, state, start_now):
        """Two calls spaced past ACTIVE_DEBOUNCE_S, currently_idle=False both times."""
        d._update_idle_active_state(start_now, self._snap(is_idle=False), currently_idle=False)
        d._update_idle_active_state(start_now + 0.6, self._snap(is_idle=False), currently_idle=False)

    def _drive_idle_transition(self, d, state, start_now):
        """Two calls spaced past IDLE_DEBOUNCE_S, currently_idle=True both times."""
        d._update_idle_active_state(start_now, self._snap(is_idle=True), currently_idle=True)
        d._update_idle_active_state(start_now + 3.1, self._snap(is_idle=True), currently_idle=True)

    def test_marked_step_survives_transient_idle_dip(self):
        d, state = self._fresh_daemon("test_marked_survives")
        state.set_pending_label("blast_step")

        self._drive_active_transition(d, state, 1000.0)
        steps = state.data["steps"]
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["step_name"], "blast_step")
        self.assertTrue(d._step_is_marked)

        # Transient idle dip -- must NOT close the step.
        self._drive_idle_transition(d, state, 1010.0)
        steps = state.data["steps"]
        self.assertEqual(len(steps), 1, "idle dip must not fragment a marked step")
        self.assertEqual(steps[0]["status"], "active")
        self.assertIsNone(steps[0]["end_time"])

        # Activity resumes -- must NOT open a second step.
        self._drive_active_transition(d, state, 1015.0)
        steps = state.data["steps"]
        self.assertEqual(len(steps), 1, "resuming activity must not open a spurious new step")
        self.assertEqual(steps[0]["step_name"], "blast_step")

        # The step finally ends via a real mark for the next step.
        state.set_pending_label("next_step")
        d._apply_mark(1020.0, self._snap())
        steps = state.data["steps"]
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["status"], "done")
        # Step actually opened at 1000.6 (ACTIVE_DEBOUNCE_S after the 1000.0 burst began),
        # not 1000.0 -- wall time spans from there to the 1020.0 mark, dip included.
        self.assertAlmostEqual(steps[0]["wall_time_s"], 19.4, delta=0.1)

        self._drive_active_transition(d, state, 1021.0)
        steps = state.data["steps"]
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[1]["step_name"], "next_step")

    def test_unmarked_step_still_fragments_on_idle_dip(self):
        """Legacy auto-detect behavior (no BenchMark mark ever used) is unchanged: each
        idle->active transition is still treated as a genuinely new command/step."""
        d, state = self._fresh_daemon("test_unmarked_fragments")

        self._drive_active_transition(d, state, 2000.0)
        steps = state.data["steps"]
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["step_name"], "step_01")
        self.assertFalse(d._step_is_marked)

        self._drive_idle_transition(d, state, 2010.0)
        steps = state.data["steps"]
        self.assertEqual(steps[0]["status"], "done", "unmarked step should still close on idle")

        self._drive_active_transition(d, state, 2015.0)
        steps = state.data["steps"]
        self.assertEqual(len(steps), 2, "unmarked usage should still open a new step")
        self.assertEqual(steps[1]["step_name"], "step_02")

    def test_apply_mark_during_idle_dip_closes_paused_step(self):
        """A new `BenchMark mark` arriving exactly during a marked step's transient pause
        (_is_idle=True but the step is still open) must still close the old step out --
        otherwise the new label is never picked up and the next activity burst silently
        keeps extending the OLD step instead."""
        d, state = self._fresh_daemon("test_mark_during_pause")
        state.set_pending_label("step_a")
        self._drive_active_transition(d, state, 3000.0)
        self._drive_idle_transition(d, state, 3010.0)
        self.assertTrue(d._is_idle)
        self.assertIsNotNone(d._step_wall_start, "step should still be open, just paused")

        state.set_pending_label("step_b")
        d._apply_mark(3010.0, self._snap())

        steps = state.data["steps"]
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["step_name"], "step_a")
        self.assertEqual(steps[0]["status"], "done")
        self.assertIsNone(d._step_wall_start)

        self._drive_active_transition(d, state, 3011.0)
        steps = state.data["steps"]
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[1]["step_name"], "step_b")


if __name__ == "__main__":
    unittest.main(verbosity=2)
