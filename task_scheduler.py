#!/usr/bin/env python3
"""
task_scheduler.py
==================
Task Scheduler integration layer for TFSync. Manages job *definitions*
(see tfsync_store.py); this module registers/updates/removes the actual
Windows scheduled tasks that invoke run_scheduled_job.py (or its packaged
.exe) so jobs run unattended, per the project brief's design decision to
use Task Scheduler rather than a custom persistent daemon.

Two parts, deliberately separated:
- `parse_schedule_expr()` is pure string parsing with no OS dependency,
  so schedule expressions can be validated (e.g. in the GUI's job editor)
  on any platform.
- Everything else shells out to schtasks.exe and only works on Windows.

SCHEDULE EXPRESSION SYNTAX (stored in jobs.schedule_expr):
    daily@HH:MM                    e.g. daily@02:00
    weekly:DAY[,DAY...]@HH:MM      e.g. weekly:MON,WED,FRI@03:30
    once@YYYY-MM-DDTHH:MM          e.g. once@2026-08-01T10:00
    hourly
    every:Nh                       e.g. every:6h
    every:Nm                       e.g. every:15m

CURRENT LIMITATION: registered tasks run under the current Windows user's
account using an interactive-only token (schtasks /RU <user> /IT) - no
password is requested or stored. This means a job only fires while that
user is logged on; it will NOT run across a full logoff or if the machine
is rebooted with nobody logged in. Supporting true logged-off/rebooted
execution would mean prompting for and passing a Windows account password
to schtasks (/RU + /RP) - deliberately not implemented here to avoid
handling a plaintext credential; it's a natural follow-up if unattended-
while-logged-off execution becomes a real requirement.
"""

from __future__ import annotations

import csv
import getpass
import io
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

TASK_FOLDER = r"\TFSync"


class ScheduleParseError(ValueError):
    pass


class TaskSchedulerError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Schedule expression parsing (pure logic - no OS dependency)
# --------------------------------------------------------------------------

_DAILY_RE = re.compile(r"^daily@(\d{1,2}):(\d{2})$", re.IGNORECASE)
_WEEKLY_RE = re.compile(r"^weekly:([A-Za-z,]+)@(\d{1,2}):(\d{2})$", re.IGNORECASE)
_ONCE_RE = re.compile(r"^once@(\d{4}-\d{2}-\d{2})[T ](\d{1,2}):(\d{2})$", re.IGNORECASE)
_HOURLY_RE = re.compile(r"^hourly$", re.IGNORECASE)
_EVERY_H_RE = re.compile(r"^every:(\d+)h$", re.IGNORECASE)
_EVERY_M_RE = re.compile(r"^every:(\d+)m$", re.IGNORECASE)

VALID_DAYS = {"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"}

SCHEDULE_HELP = (
    "Supported schedule formats:\n"
    "  daily@HH:MM                  e.g. daily@02:00\n"
    "  weekly:DAY[,DAY...]@HH:MM    e.g. weekly:MON,WED,FRI@03:30\n"
    "  once@YYYY-MM-DDTHH:MM        e.g. once@2026-08-01T10:00\n"
    "  hourly\n"
    "  every:Nh                     e.g. every:6h\n"
    "  every:Nm                     e.g. every:15m"
)


def _validate_time(hh: int, mm: int, expr: str) -> None:
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ScheduleParseError(f"Invalid time in '{expr}': hours must be 00-23, minutes 00-59.")


def decompose_schedule_expr(expr: Optional[str]) -> Dict[str, Any]:
    """
    Parses a schedule_expr into a structured dict rather than raw schtasks
    args, so a GUI can populate a Frequency dropdown + day checkboxes +
    time picker from an existing job without re-implementing the regexes.
    Returns one of:
      {"kind": "daily", "hour": int, "minute": int}
      {"kind": "weekly", "days": [str, ...], "hour": int, "minute": int}
      {"kind": "once", "date": "YYYY-MM-DD", "hour": int, "minute": int}
      {"kind": "hourly"}
      {"kind": "every_hours", "n": int}
      {"kind": "every_minutes", "n": int}
    Raises ScheduleParseError (with the supported syntax) if unrecognized.
    """
    if not expr or not expr.strip():
        raise ScheduleParseError("Schedule expression is empty.\n\n" + SCHEDULE_HELP)
    expr = expr.strip()

    m = _DAILY_RE.match(expr)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        _validate_time(hh, mm, expr)
        return {"kind": "daily", "hour": hh, "minute": mm}

    m = _WEEKLY_RE.match(expr)
    if m:
        days_raw, hh, mm = m.group(1), int(m.group(2)), int(m.group(3))
        days = [d.strip().upper() for d in days_raw.split(",") if d.strip()]
        invalid = [d for d in days if d not in VALID_DAYS]
        if invalid:
            raise ScheduleParseError(
                f"Unknown day(s) {invalid} in '{expr}'. Use MON,TUE,WED,THU,FRI,SAT,SUN.\n\n" + SCHEDULE_HELP
            )
        _validate_time(hh, mm, expr)
        return {"kind": "weekly", "days": days, "hour": hh, "minute": mm}

    m = _ONCE_RE.match(expr)
    if m:
        date_str, hh, mm = m.group(1), int(m.group(2)), int(m.group(3))
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            raise ScheduleParseError(f"Invalid date '{date_str}' in '{expr}'. Use YYYY-MM-DD.\n\n" + SCHEDULE_HELP)
        _validate_time(hh, mm, expr)
        return {"kind": "once", "date": date_str, "hour": hh, "minute": mm}

    if _HOURLY_RE.match(expr):
        return {"kind": "hourly"}

    m = _EVERY_H_RE.match(expr)
    if m:
        n = int(m.group(1))
        if n < 1:
            raise ScheduleParseError("Interval must be at least 1 hour.\n\n" + SCHEDULE_HELP)
        return {"kind": "every_hours", "n": n}

    m = _EVERY_M_RE.match(expr)
    if m:
        n = int(m.group(1))
        if n < 1:
            raise ScheduleParseError("Interval must be at least 1 minute.\n\n" + SCHEDULE_HELP)
        return {"kind": "every_minutes", "n": n}

    raise ScheduleParseError(f"Unrecognized schedule expression: '{expr}'.\n\n" + SCHEDULE_HELP)


def build_schedule_expr(kind: str, **kwargs: Any) -> str:
    """The inverse of decompose_schedule_expr - builds a canonical schedule_expr
    string from structured fields (e.g. from the GUI's Frequency dropdown)."""
    if kind == "daily":
        return f'daily@{kwargs["hour"]:02d}:{kwargs["minute"]:02d}'
    if kind == "weekly":
        days = kwargs.get("days") or []
        if not days:
            raise ScheduleParseError("Select at least one day for a weekly schedule.")
        return f'weekly:{",".join(days)}@{kwargs["hour"]:02d}:{kwargs["minute"]:02d}'
    if kind == "once":
        return f'once@{kwargs["date"]}T{kwargs["hour"]:02d}:{kwargs["minute"]:02d}'
    if kind == "hourly":
        return "hourly"
    if kind == "every_hours":
        return f'every:{kwargs["n"]}h'
    if kind == "every_minutes":
        return f'every:{kwargs["n"]}m'
    raise ValueError(f"Unknown schedule kind: {kind}")


def parse_schedule_expr(expr: Optional[str]) -> List[str]:
    """
    Parses a schedule_expr into the schtasks /Create arguments describing
    the trigger (/SC ... /ST ... etc). Raises ScheduleParseError with a
    human-readable message (including the supported syntax) if the
    expression doesn't match anything recognized. Pure string logic - runs
    on any platform, so the GUI can validate before ever touching Windows
    APIs.
    """
    info = decompose_schedule_expr(expr)
    kind = info["kind"]
    if kind == "daily":
        return ["/SC", "DAILY", "/ST", f'{info["hour"]:02d}:{info["minute"]:02d}']
    if kind == "weekly":
        return ["/SC", "WEEKLY", "/D", ",".join(info["days"]), "/ST", f'{info["hour"]:02d}:{info["minute"]:02d}']
    if kind == "once":
        d = datetime.strptime(info["date"], "%Y-%m-%d")
        return ["/SC", "ONCE", "/SD", d.strftime("%m/%d/%Y"), "/ST", f'{info["hour"]:02d}:{info["minute"]:02d}']
    if kind == "hourly":
        return ["/SC", "HOURLY", "/MO", "1"]
    if kind == "every_hours":
        return ["/SC", "HOURLY", "/MO", str(info["n"])]
    if kind == "every_minutes":
        return ["/SC", "MINUTE", "/MO", str(info["n"])]
    raise ScheduleParseError(f"Unrecognized schedule kind: {kind}")


# --------------------------------------------------------------------------
# schtasks.exe wrapper (Windows only)
# --------------------------------------------------------------------------

def _require_windows() -> None:
    if os.name != "nt":
        raise TaskSchedulerError("Task Scheduler integration requires Windows (schtasks.exe).")


def _task_name(job_id: str) -> str:
    return f"{TASK_FOLDER}\\{job_id}"


def _current_user() -> str:
    domain = os.environ.get("USERDOMAIN", "")
    user = os.environ.get("USERNAME") or getpass.getuser()
    return f"{domain}\\{user}" if domain else user


def _runner_command(job_id: str) -> str:
    """Builds the /TR command line schtasks should execute for this job."""
    if getattr(sys, "frozen", False):
        # Packaged build: run_scheduled_job.exe ships next to this exe.
        exe_dir = Path(sys.executable).resolve().parent
        runner = exe_dir / "run_scheduled_job.exe"
        return f'"{runner}" --job-id {job_id}'
    script = Path(__file__).resolve().parent / "run_scheduled_job.py"
    return f'"{sys.executable}" "{script}" --job-id {job_id}'


def _run_schtasks(args: List[str], allow_not_found: bool = False) -> subprocess.CompletedProcess:
    _require_windows()
    proc = subprocess.run(["schtasks.exe", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        stderr = (proc.stderr or proc.stdout or "").strip()
        if allow_not_found and ("cannot find" in stderr.lower() or "does not exist" in stderr.lower()):
            return proc
        raise TaskSchedulerError(f"schtasks {' '.join(args)} failed:\n{stderr}")
    return proc


def register_task(job: Dict[str, Any]) -> None:
    """
    Creates or updates (idempotent - /F overwrites) the scheduled task for
    a job, per its schedule_expr. Raises ScheduleParseError if the
    expression is invalid, or TaskSchedulerError if schtasks itself fails.
    """
    schedule_args = parse_schedule_expr(job["schedule_expr"])
    args = [
        "/Create", "/F",
        "/TN", _task_name(job["job_id"]),
        "/TR", _runner_command(job["job_id"]),
        *schedule_args,
        "/RU", _current_user(),
        "/IT",
    ]
    _run_schtasks(args)


def delete_task(job_id: str) -> None:
    """Removes the scheduled task for a job. Safe/idempotent if it was never registered."""
    _run_schtasks(["/Delete", "/TN", _task_name(job_id), "/F"], allow_not_found=True)


def query_task(job_id: str) -> Optional[Dict[str, str]]:
    """Returns key/value fields (incl. 'Next Run Time', 'Last Result', 'Scheduled Task State',
    'Status') for a registered task, or None if it isn't registered (or we're not on Windows)."""
    if os.name != "nt":
        return None
    proc = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", _task_name(job_id), "/V", "/FO", "LIST"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    info: Dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            if key:
                info[key] = value
    return info or None


def _list_tfsync_task_job_ids() -> List[str]:
    """Lists job_ids for every task currently registered under the \\TFSync\\ folder."""
    _require_windows()
    proc = subprocess.run(["schtasks.exe", "/Query", "/FO", "CSV"], capture_output=True, text=True)
    if proc.returncode != 0:
        return []
    job_ids = []
    reader = csv.reader(io.StringIO(proc.stdout))
    rows = list(reader)
    if not rows:
        return []
    header = [h.strip('"') for h in rows[0]]
    try:
        name_idx = header.index("TaskName")
    except ValueError:
        return []
    prefix = TASK_FOLDER + "\\"
    for row in rows[1:]:
        if len(row) <= name_idx:
            continue
        task_name = row[name_idx]
        if task_name.startswith(prefix):
            job_ids.append(task_name[len(prefix):])
    return job_ids


def reconcile_all(jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Full reconciliation between the jobs database and what's actually
    registered in Task Scheduler:
      - registers/updates the task for every enabled job with a schedule
      - removes the task for every disabled/unscheduled job
      - removes any orphaned \\TFSync\\* task whose job no longer exists
        in the database at all (e.g. deleted outside the normal flow)
    Returns a summary dict for a confirmation dialog. Windows only.
    """
    _require_windows()
    registered, removed, errors = 0, 0, []

    known_ids = {job["job_id"] for job in jobs}

    for job in jobs:
        should_be_scheduled = bool(job["enabled"]) and bool(job.get("schedule_expr"))
        try:
            if should_be_scheduled:
                register_task(job)
                registered += 1
            else:
                delete_task(job["job_id"])
        except (TaskSchedulerError, ScheduleParseError) as e:
            errors.append(f"{job['name']}: {e}")

    for orphan_job_id in _list_tfsync_task_job_ids():
        if orphan_job_id not in known_ids:
            try:
                delete_task(orphan_job_id)
                removed += 1
            except TaskSchedulerError as e:
                errors.append(f"orphaned task {orphan_job_id}: {e}")

    return {"registered": registered, "removed": removed, "errors": errors}


if __name__ == "__main__":
    # Pure-logic parser self-test - safe on any platform.
    tests = ["daily@02:00", "weekly:MON,WED,FRI@03:30", "once@2026-08-01T10:00", "hourly", "every:6h", "every:15m"]
    for t in tests:
        print(t, "->", parse_schedule_expr(t))
    for bad in ["", "daily@25:00", "weekly:FUNDAY@01:00", "every:0h", "nonsense"]:
        try:
            parse_schedule_expr(bad)
            print(bad, "-> ERROR: should have raised")
        except ScheduleParseError as e:
            print(bad, "-> correctly rejected:", str(e).splitlines()[0])
