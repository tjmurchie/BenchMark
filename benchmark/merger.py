"""Merge multiple per-session CSVs into one combined analysis file."""

import csv
import os
import sys
from typing import Dict, List, Optional


def _load_csv(path: str) -> List[Dict[str, str]]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def merge_csvs(
    input_files: List[str],
    output_path: str,
    extra_meta: Optional[Dict[str, str]] = None,
) -> int:
    """Merge multiple benchmark CSVs into one file.

    Adds a source_file column and any extra metadata columns supplied via extra_meta.
    Returns total number of rows written.
    """
    all_rows: List[Dict[str, str]] = []
    all_cols: List[str] = []

    for path in input_files:
        if not os.path.isfile(path):
            print(f"Warning: {path} not found, skipping", file=sys.stderr)
            continue
        rows = _load_csv(path)
        if not rows:
            continue
        # Track all column names seen (preserve order)
        for col in rows[0].keys():
            if col not in all_cols:
                all_cols.append(col)
        for row in rows:
            row["source_file"] = os.path.basename(path)
            if extra_meta:
                row.update(extra_meta)
        all_rows.extend(rows)

    if not all_rows:
        print("No data to merge.", file=sys.stderr)
        return 0

    # Build final column list
    extra_cols = ["source_file"] + (list(extra_meta.keys()) if extra_meta else [])
    final_cols = all_cols.copy()
    for col in extra_cols:
        if col not in final_cols:
            final_cols.append(col)

    # Add derived comparison columns if not already present
    for derived in ["cpu_efficiency", "mem_per_cpu_mb_per_s", "total_io_mb"]:
        if derived not in final_cols:
            final_cols.append(derived)

    enriched = []
    for row in all_rows:
        # cpu_efficiency already computed by reporter, but recalculate if missing
        if not row.get("cpu_efficiency"):
            try:
                wall = float(row.get("wall_time_s", 0) or 0)
                cpu = float(row.get("cpu_total_s", 0) or 0)
                row["cpu_efficiency"] = f"{cpu / wall:.3f}" if wall > 0 else ""
            except (ValueError, ZeroDivisionError):
                row["cpu_efficiency"] = ""

        try:
            r = float(row.get("disk_read_mb", 0) or 0)
            w = float(row.get("disk_write_mb", 0) or 0)
            row["total_io_mb"] = f"{r + w:.3f}"
        except ValueError:
            row["total_io_mb"] = ""

        try:
            avg_mem = float(row.get("avg_mem_mb", 0) or 0)
            cpu = float(row.get("cpu_total_s", 0) or 0)
            row["mem_per_cpu_mb_per_s"] = f"{avg_mem / cpu:.3f}" if cpu > 0 else ""
        except (ValueError, ZeroDivisionError):
            row["mem_per_cpu_mb_per_s"] = ""

        enriched.append(row)

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=final_cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(enriched)

    return len(enriched)
