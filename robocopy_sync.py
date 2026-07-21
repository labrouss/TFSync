#!/usr/bin/env python3
"""
robocopy_sync.py
=================
Thin wrapper around Windows' built-in robocopy for syncing/copying an
SMB/CIFS share to another while preserving NTFS permissions. Used by both
sync_shares.py (CLI) and tfsync_gui.py (GUI Sync tab).

Two modes:
  - Copy-only (default): robocopy /E - copies new/changed files and folders,
    NEVER deletes anything in the destination.
  - Mirror: robocopy /MIR - makes the destination an exact copy of the
    source, which DELETES files/folders in the destination that don't exist
    in the source. Use --dry-run (robocopy /L) first to preview.

Requires: Windows (robocopy ships with the OS - no extra install needed).
"""

import re
import subprocess
import sys
from typing import Callable, Dict, List, Optional

# Robocopy exit codes are a bitmask; each bit means something different and
# they combine (e.g. 3 = 1 + 2). 16 is a fatal error and overrides everything.
ROBOCOPY_EXIT_MEANINGS = {
    1: "Files were copied successfully",
    2: "Extra files/dirs found in destination that aren't in source",
    4: "Mismatched files or directories were detected",
    8: "Some files or directories could NOT be copied (errors occurred)",
    16: "FATAL ERROR - robocopy copied nothing (check paths/permissions/syntax)",
}


def describe_exit_code(code: int) -> str:
    """Human-readable summary of a robocopy exit code."""
    if code < 0:
        return "Cancelled before completion"
    if code >= 16:
        return ROBOCOPY_EXIT_MEANINGS[16]
    if code == 0:
        return "No files copied - source and destination already match"
    parts = [ROBOCOPY_EXIT_MEANINGS[bit] for bit in (1, 2, 4, 8) if code & bit]
    return "; ".join(parts) if parts else f"Unrecognized exit code {code}"


def is_failure(code: int) -> bool:
    """True if robocopy reported an actual failure (not just informational bits)."""
    return code < 0 or code >= 8


def build_command(
    source: str,
    dest: str,
    mirror: bool = False,
    dry_run: bool = False,
    threads: int = 16,
    retries: int = 3,
    wait_seconds: int = 5,
    preserve_permissions: bool = True,
    log_path: Optional[str] = None,
    excludes: Optional[List[str]] = None,
) -> List[str]:
    cmd = ["robocopy", source, dest]
    cmd.append("/MIR" if mirror else "/E")

    if preserve_permissions:
        # D=Data, A=Attributes, T=Timestamps, S=Security(ACLs), O=Owner
        cmd += ["/COPY:DATSO", "/DCOPY:DAT"]
    else:
        cmd += ["/COPY:DAT", "/DCOPY:DAT"]

    cmd += [f"/R:{retries}", f"/W:{wait_seconds}", f"/MT:{max(1, threads)}"]
    cmd += ["/XJ"]   # don't follow junction points - avoids potential infinite loops
    cmd += ["/NP"]   # no per-file percentage spam - cleaner, line-based output

    if dry_run:
        cmd.append("/L")

    if log_path:
        cmd += [f"/LOG:{log_path}", "/TEE"]

    if excludes:
        cmd += ["/XF", *excludes]

    return cmd


# Matches the start of robocopy's summary rows, e.g. "   Dirs :", "  Files :", "  Bytes :"
_SUMMARY_LABEL_RE = re.compile(r"^\s*(Dirs|Files|Bytes)\s*:\s*(.*)$", re.IGNORECASE)

# Each of the 6 values in a summary row is a number, optionally followed by a
# single-letter unit suffix on the Bytes row (e.g. "1011.52 m" for megabytes).
_SUMMARY_VALUE_RE = re.compile(r"\d[\d.,]*(?:\s[kmgtKMGT])?")


def parse_summary(lines: List[str]) -> Dict[str, Dict[str, str]]:
    """Parse robocopy's Dirs/Files/Bytes summary block into a dict."""
    summary: Dict[str, Dict[str, str]] = {}
    columns = ("total", "copied", "skipped", "mismatch", "failed", "extras")

    for line in lines:
        label_match = _SUMMARY_LABEL_RE.match(line)
        if not label_match:
            continue
        key = label_match.group(1).capitalize()
        values = _SUMMARY_VALUE_RE.findall(label_match.group(2))
        if len(values) != len(columns):
            continue  # unexpected format - skip rather than misattribute columns
        summary[key] = dict(zip(columns, (v.strip() for v in values)))

    return summary


def run_robocopy(
    source: str,
    dest: str,
    mirror: bool = False,
    dry_run: bool = False,
    threads: int = 16,
    retries: int = 3,
    wait_seconds: int = 5,
    preserve_permissions: bool = True,
    log_path: Optional[str] = None,
    excludes: Optional[List[str]] = None,
    line_cb: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> Dict[str, object]:
    """
    Runs robocopy, streaming each output line to line_cb as it happens.
    Returns a dict: exit_code, cancelled, summary (parsed Dirs/Files/Bytes),
    description (human-readable), log_lines (full captured output).
    """
    log = line_cb or (lambda line: None)
    cancelled_check = should_cancel or (lambda: False)

    cmd = build_command(
        source, dest, mirror=mirror, dry_run=dry_run, threads=threads,
        retries=retries, wait_seconds=wait_seconds,
        preserve_permissions=preserve_permissions, log_path=log_path, excludes=excludes,
    )
    log(f"$ {' '.join(cmd)}")

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, universal_newlines=True,
        creationflags=creationflags,
    )

    all_lines: List[str] = []
    cancelled = False
    try:
        for raw_line in process.stdout:
            line = raw_line.rstrip("\n").rstrip("\r")
            if line:
                all_lines.append(line)
                log(line)
            if cancelled_check():
                cancelled = True
                process.terminate()
                break
    finally:
        process.wait()

    exit_code = -1 if cancelled else process.returncode
    summary = parse_summary(all_lines)
    description = "Cancelled by user" if cancelled else describe_exit_code(exit_code)

    return {
        "exit_code": exit_code,
        "cancelled": cancelled,
        "summary": summary,
        "description": description,
        "log_lines": all_lines,
    }
