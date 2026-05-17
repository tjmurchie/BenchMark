"""Generate per-session CSV reports from state files."""

import csv
import os
from datetime import datetime
from typing import Any, Dict, List, Optional


CSV_COLUMNS = [
    "run_id",
    "session_name",
    "tool_name",
    "dataset",
    "run_date",
    "hostname",
    "cpu_model",
    "cpu_count_logical",
    "cpu_count_physical",
    "total_ram_gb",
    "step_num",
    "step_name",
    "start_time",
    "end_time",
    "wall_time_s",
    "cpu_user_s",
    "cpu_system_s",
    "cpu_total_s",
    "cpu_efficiency",
    "peak_mem_mb",
    "avg_mem_mb",
    "max_threads",
    "peak_processes",
    "disk_read_mb",
    "disk_write_mb",
    "is_summary",
    "notes",
]


def _safe(val, default=""):
    return val if val is not None else default


def _efficiency(cpu_total: float, wall: float) -> str:
    if wall and wall > 0:
        return f"{cpu_total / wall:.3f}"
    return ""


def _build_rows(state: dict) -> List[Dict[str, Any]]:
    sysinfo = state.get("system_info", {})
    run_id = f"{state.get('session_name', 'unknown')}_{state.get('start_time', '')[:10]}"
    base = {
        "run_id": run_id,
        "session_name": _safe(state.get("session_name")),
        "tool_name": _safe(state.get("tool_name")),
        "dataset": _safe(state.get("dataset")),
        "run_date": _safe(state.get("start_time", ""))[:10],
        "hostname": _safe(sysinfo.get("hostname")),
        "cpu_model": _safe(sysinfo.get("cpu_model")),
        "cpu_count_logical": _safe(sysinfo.get("cpu_count_logical")),
        "cpu_count_physical": _safe(sysinfo.get("cpu_count_physical")),
        "total_ram_gb": _safe(sysinfo.get("total_ram_gb")),
        "notes": _safe(state.get("notes")),
    }

    rows = []
    steps = state.get("steps", [])

    for step in steps:
        if step.get("status") != "done":
            continue
        wall = step.get("wall_time_s", 0.0)
        cpu_total = step.get("cpu_total_s", 0.0)
        row = {**base}
        row.update(
            step_num=step.get("step_num"),
            step_name=_safe(step.get("step_name")),
            start_time=_safe(step.get("start_time")),
            end_time=_safe(step.get("end_time")),
            wall_time_s=wall,
            cpu_user_s=step.get("cpu_user_s", 0.0),
            cpu_system_s=step.get("cpu_system_s", 0.0),
            cpu_total_s=cpu_total,
            cpu_efficiency=_efficiency(cpu_total, wall),
            peak_mem_mb=step.get("peak_mem_mb", 0.0),
            avg_mem_mb=step.get("avg_mem_mb", 0.0),
            max_threads=step.get("max_threads", 0),
            peak_processes=step.get("peak_processes", 0),
            disk_read_mb=step.get("disk_read_mb", 0.0),
            disk_write_mb=step.get("disk_write_mb", 0.0),
            is_summary=False,
        )
        rows.append(row)

    # Build TOTAL summary row
    if rows:
        total_wall = sum(r["wall_time_s"] for r in rows)
        total_cpu_user = sum(r["cpu_user_s"] for r in rows)
        total_cpu_sys = sum(r["cpu_system_s"] for r in rows)
        total_cpu = total_cpu_user + total_cpu_sys
        summary = {**base}
        summary.update(
            step_num=0,
            step_name="TOTAL",
            start_time=rows[0]["start_time"],
            end_time=rows[-1]["end_time"],
            wall_time_s=total_wall,
            cpu_user_s=total_cpu_user,
            cpu_system_s=total_cpu_sys,
            cpu_total_s=total_cpu,
            cpu_efficiency=_efficiency(total_cpu, total_wall),
            peak_mem_mb=max(r["peak_mem_mb"] for r in rows),
            avg_mem_mb=sum(r["avg_mem_mb"] for r in rows) / len(rows),
            max_threads=max(r["max_threads"] for r in rows),
            peak_processes=max(r["peak_processes"] for r in rows),
            disk_read_mb=sum(r["disk_read_mb"] for r in rows),
            disk_write_mb=sum(r["disk_write_mb"] for r in rows),
            is_summary=True,
        )
        rows.append(summary)

    return rows


def generate_csv(state: dict, output_path: str) -> int:
    """Write a CSV report for the given state dict. Returns number of data rows."""
    rows = _build_rows(state)
    if not rows:
        return 0
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def print_summary(state: dict):
    """Print a human-readable summary to stdout."""
    steps = [s for s in state.get("steps", []) if s.get("status") == "done"]
    tool = state.get("tool_name", "?")
    dataset = state.get("dataset", "?")
    print(f"\n{'─' * 60}")
    print(f"  BenchMark session: {state.get('session_name', '?')}")
    print(f"  Tool:    {tool}   Dataset: {dataset}")
    print(f"{'─' * 60}")
    print(f"  {'Step':<20} {'Wall':>8} {'CPU':>8} {'PeakMem':>10} {'Threads':>8}")
    print(f"  {'─'*20} {'─'*8} {'─'*8} {'─'*10} {'─'*8}")
    total_wall = 0.0
    total_cpu = 0.0
    for s in steps:
        wall = s.get("wall_time_s", 0.0)
        cpu = s.get("cpu_total_s", 0.0)
        total_wall += wall
        total_cpu += cpu
        print(
            f"  {s.get('step_name', '?'):<20} "
            f"{_fmt_time(wall):>8} "
            f"{_fmt_time(cpu):>8} "
            f"{s.get('peak_mem_mb', 0):.0f} MB".rjust(10) + " "
            f"{s.get('max_threads', 0):>8}"
        )
    if steps:
        print(f"  {'─'*20} {'─'*8} {'─'*8} {'─'*10} {'─'*8}")
        eff = total_cpu / total_wall if total_wall > 0 else 0
        print(
            f"  {'TOTAL':<20} "
            f"{_fmt_time(total_wall):>8} "
            f"{_fmt_time(total_cpu):>8} "
            f"  (eff: {eff:.1f}x)"
        )
    print(f"{'─' * 60}\n")


def _fmt_time(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        m, s = divmod(int(seconds), 60)
        return f"{m}m{s:02d}s"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m"
