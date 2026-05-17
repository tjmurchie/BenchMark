"""Process tree inspection and resource metric collection."""

import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# Process names treated as "idle shell" — no active work
SHELL_NAMES = frozenset({
    "bash", "sh", "zsh", "fish", "dash", "ksh", "tcsh", "csh",
    "rbash", "ash", "mksh", "pdksh",
})

# These appear in screen process trees but aren't "work"
SCREEN_OVERHEAD = frozenset({"screen", "SCREEN"})


@dataclass
class ProcessSnapshot:
    timestamp: float
    cpu_user_s: float = 0.0
    cpu_system_s: float = 0.0
    mem_rss_mb: float = 0.0
    num_threads: int = 0
    num_processes: int = 0
    disk_read_bytes: int = 0
    disk_write_bytes: int = 0
    is_idle: bool = True
    pids: List[int] = field(default_factory=list)


def find_screen_pid(session_name: str) -> Optional[int]:
    """Return the PID of a screen session whose name contains session_name."""
    try:
        result = subprocess.run(
            ["screen", "-ls"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            token = line.strip().split()[0] if line.strip() else ""
            if "." in token:
                pid_s, name = token.split(".", 1)
                try:
                    if session_name.lower() in name.lower():
                        return int(pid_s)
                except ValueError:
                    continue
    except Exception:
        pass
    return None


def list_screen_sessions() -> List[Tuple[int, str]]:
    """Return list of (pid, name) for all active screen sessions."""
    sessions: List[Tuple[int, str]] = []
    try:
        result = subprocess.run(
            ["screen", "-ls"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            token = line.strip().split()[0] if line.strip() else ""
            if "." in token:
                pid_s, name = token.split(".", 1)
                try:
                    sessions.append((int(pid_s), name))
                except ValueError:
                    continue
    except Exception:
        pass
    return sessions


def get_descendants(screen_pid: int):
    """Return the parent process and all descendants as a list of psutil.Process."""
    if not HAS_PSUTIL:
        return []
    try:
        parent = psutil.Process(screen_pid)
        return [parent] + parent.children(recursive=True)
    except psutil.NoSuchProcess:
        return []


def collect_snapshot(processes) -> ProcessSnapshot:
    """Build a ProcessSnapshot from a list of psutil.Process objects."""
    snap = ProcessSnapshot(timestamp=time.time(), num_processes=len(processes))
    if not HAS_PSUTIL or not processes:
        return snap

    non_idle_count = 0
    for p in processes:
        try:
            name = p.name()
            cpu = p.cpu_times()
            snap.cpu_user_s += cpu.user
            snap.cpu_system_s += cpu.system
            snap.mem_rss_mb += p.memory_info().rss / (1024 * 1024)
            snap.num_threads += p.num_threads()
            snap.pids.append(p.pid)
            try:
                io = p.io_counters()
                snap.disk_read_bytes += io.read_bytes
                snap.disk_write_bytes += io.write_bytes
            except (psutil.AccessDenied, AttributeError):
                pass
            if name not in SHELL_NAMES and name not in SCREEN_OVERHEAD:
                non_idle_count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    snap.is_idle = (non_idle_count == 0)
    return snap


def get_cpu_percent_for(processes, prev_times: Dict[int, float], interval: float) -> float:
    """Compute total CPU% for non-shell processes since prev_times was recorded."""
    if interval < 0.05 or not HAS_PSUTIL:
        return 0.0
    total = 0.0
    for p in processes:
        try:
            name = p.name()
            if name in SHELL_NAMES or name in SCREEN_OVERHEAD:
                continue
            cpu = p.cpu_times()
            current = cpu.user + cpu.system
            if p.pid in prev_times:
                total += max(0.0, (current - prev_times[p.pid]) / interval * 100.0)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total


def record_cpu_baselines(processes) -> Dict[int, float]:
    """Return {pid: cumulative_cpu_s} for all non-shell processes."""
    result: Dict[int, float] = {}
    if not HAS_PSUTIL:
        return result
    for p in processes:
        try:
            if p.name() in SHELL_NAMES or p.name() in SCREEN_OVERHEAD:
                continue
            cpu = p.cpu_times()
            result[p.pid] = cpu.user + cpu.system
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return result


def get_system_info() -> dict:
    """Collect static system information for reproducibility."""
    info: dict = {}
    try:
        import platform
        info["hostname"] = platform.node()
        info["os"] = platform.platform()
        info["python_version"] = platform.python_version()
    except Exception:
        pass

    if HAS_PSUTIL:
        try:
            info["cpu_count_logical"] = psutil.cpu_count(logical=True)
            info["cpu_count_physical"] = psutil.cpu_count(logical=False)
            info["total_ram_gb"] = round(psutil.virtual_memory().total / (1024 ** 3), 1)
        except Exception:
            pass

    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    info["cpu_model"] = line.split(":", 1)[1].strip()
                    break
    except Exception:
        pass

    return info
