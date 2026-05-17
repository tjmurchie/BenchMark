"""Session state management — reads/writes JSON state files for daemon ↔ CLI communication."""

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


BENCHMARK_DIR = os.path.expanduser("~/.benchmark")
SESSIONS_DIR = os.path.join(BENCHMARK_DIR, "sessions")
ACTIVE_INDEX = os.path.join(BENCHMARK_DIR, "active.json")


def _ensure_dirs():
    os.makedirs(SESSIONS_DIR, exist_ok=True)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _load_json(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_json(path: str, data: dict):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def register_session(session_name: str, session_dir: str):
    _ensure_dirs()
    index = _load_json(ACTIVE_INDEX)
    index[session_name] = session_dir
    _save_json(ACTIVE_INDEX, index)


def unregister_session(session_name: str):
    index = _load_json(ACTIVE_INDEX)
    index.pop(session_name, None)
    _save_json(ACTIVE_INDEX, index)


def get_session_dir(session_name: str) -> Optional[str]:
    index = _load_json(ACTIVE_INDEX)
    path = index.get(session_name)
    if path and os.path.isdir(path):
        return path
    return None


def list_active_sessions() -> Dict[str, str]:
    return _load_json(ACTIVE_INDEX)


class StateManager:
    """Manages the JSON state file for a monitoring session.

    Written by the daemon; read by the CLI for status/stop commands.
    """

    def __init__(self, session_dir: str):
        self.session_dir = session_dir
        self.state_file = os.path.join(session_dir, "state.json")
        self.pid_file = os.path.join(session_dir, "daemon.pid")
        self.mark_file = os.path.join(session_dir, "pending_mark.json")
        self._state: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def init(
        self,
        session_name: str,
        tool_name: str,
        dataset: str,
        output_dir: str,
        notes: str,
        system_info: dict,
    ):
        """Called once when daemon starts up."""
        self._state = {
            "schema_version": 1,
            "session_name": session_name,
            "tool_name": tool_name,
            "dataset": dataset,
            "output_dir": output_dir,
            "notes": notes,
            "system_info": system_info,
            "status": "initializing",
            "screen_pid": None,
            "start_time": _iso(time.time()),
            "end_time": None,
            "steps": [],
            "current_step_num": 0,
            "pending_label": None,
            "total_idle_s": 0.0,
        }
        self.flush()

    def set_running(self, screen_pid: int):
        self._state["status"] = "running"
        self._state["screen_pid"] = screen_pid
        self.flush()

    def set_error(self, message: str):
        self._state["status"] = "error"
        self._state["error_message"] = message
        self.flush()

    def finalize(self, total_idle_s: float = 0.0):
        self._state["status"] = "done"
        self._state["end_time"] = _iso(time.time())
        self._state["total_idle_s"] = total_idle_s
        self.flush()

    # ------------------------------------------------------------------
    # Step management
    # ------------------------------------------------------------------

    def get_next_step_num(self) -> int:
        return self._state["current_step_num"] + 1

    def get_current_step_num(self) -> int:
        return self._state["current_step_num"]

    def start_step(self, step_num: int, timestamp: float):
        label = self.consume_pending_label() or f"step_{step_num:02d}"
        step = {
            "step_num": step_num,
            "step_name": label,
            "start_time": _iso(timestamp),
            "end_time": None,
            "status": "active",
            # Metrics filled in by end_step()
        }
        self._state["steps"].append(step)
        self._state["current_step_num"] = step_num

    def end_step(
        self,
        step_num: int,
        end_time: float,
        wall_time_s: float,
        cpu_user_s: float,
        cpu_system_s: float,
        peak_mem_mb: float,
        avg_mem_mb: float,
        max_threads: int,
        peak_processes: int,
        disk_read_mb: float,
        disk_write_mb: float,
    ):
        for step in self._state["steps"]:
            if step["step_num"] == step_num:
                step.update(
                    end_time=_iso(end_time),
                    status="done",
                    wall_time_s=round(wall_time_s, 3),
                    cpu_user_s=round(cpu_user_s, 3),
                    cpu_system_s=round(cpu_system_s, 3),
                    cpu_total_s=round(cpu_user_s + cpu_system_s, 3),
                    peak_mem_mb=round(peak_mem_mb, 1),
                    avg_mem_mb=round(avg_mem_mb, 1),
                    max_threads=max_threads,
                    peak_processes=peak_processes,
                    disk_read_mb=round(disk_read_mb, 3),
                    disk_write_mb=round(disk_write_mb, 3),
                )
                break
        self.flush()

    def rename_step(self, step_num: int, new_name: str):
        for step in self._state["steps"]:
            if step["step_num"] == step_num:
                step["step_name"] = new_name
                break
        self.flush()

    # ------------------------------------------------------------------
    # Pending label (for BenchMark mark)
    # ------------------------------------------------------------------

    def set_pending_label(self, label: str):
        _save_json(self.mark_file, {"label": label})

    def consume_pending_label(self) -> Optional[str]:
        try:
            data = _load_json(self.mark_file)
            label = data.get("label")
            if label:
                try:
                    os.remove(self.mark_file)
                except FileNotFoundError:
                    pass
                return label
        except Exception:
            pass
        return None

    def check_pending_label(self) -> Optional[str]:
        """Read without consuming (for daemon polling)."""
        try:
            data = _load_json(self.mark_file)
            return data.get("label")
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def flush(self):
        _save_json(self.state_file, self._state)

    def load(self):
        self._state = _load_json(self.state_file)

    @property
    def data(self) -> Dict[str, Any]:
        return self._state

    # ------------------------------------------------------------------
    # PID file
    # ------------------------------------------------------------------

    def write_pid(self, pid: int):
        with open(self.pid_file, "w") as f:
            f.write(str(pid))

    def read_pid(self) -> Optional[int]:
        try:
            with open(self.pid_file) as f:
                return int(f.read().strip())
        except (FileNotFoundError, ValueError):
            return None

    def remove_pid(self):
        try:
            os.remove(self.pid_file)
        except FileNotFoundError:
            pass
