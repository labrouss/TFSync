#!/usr/bin/env python3
"""
tfsync_store.py
================
SQLite-backed storage for TFSync job definitions, run history, and license
usage tracking.

Design notes (see project brief):
- One SQLite file (stdlib `sqlite3`, no new dependency) holds both job
  definitions and run history. This also naturally supports the licensing
  usage-tracking below without a second storage mechanism.
- Free tier quota is a **lifetime** cap (100GB), not monthly-rolling.
- `LicenseManager` and `UsageTracker` are stubs: LicenseManager always
  reports "unlimited/valid" until a real licensing scheme is built.
  UsageTracker is fully functional (it just isn't enforced by anything
  yet) - it accumulates bytes actually copied and can be checked before a
  job starts.

This module has no Windows-only dependencies and can be imported/tested
on any platform. It's meant to be called from the GUI/CLI after a
`robocopy_sync.run_robocopy(...)` call completes, and (later) from the
Task Scheduler integration layer when scheduled jobs run unattended.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

DEFAULT_DB_PATH = Path.home() / "AppData" / "Local" / "TFSync" / "tfsync.db"

FREE_TIER_LIFETIME_BYTES = 100 * (1024 ** 3)  # 100 GB, lifetime cap

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id           TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    source           TEXT NOT NULL,
    dest             TEXT NOT NULL,
    mode             TEXT NOT NULL DEFAULT 'copy',   -- 'copy' or 'mirror'
    schedule_expr    TEXT,                            -- e.g. cron-like or Task Scheduler trigger spec
    threads          INTEGER NOT NULL DEFAULT 16,
    retries          INTEGER NOT NULL DEFAULT 3,
    auto_verify_acl  INTEGER NOT NULL DEFAULT 0,       -- chain an ACL comparison after sync
    enabled          INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    last_run_id      TEXT,
    last_run_status  TEXT                              -- mirrors run_history.description for quick display
);

CREATE TABLE IF NOT EXISTS run_history (
    run_id            TEXT PRIMARY KEY,
    job_id            TEXT,                              -- NULL for ad-hoc/manual runs not tied to a saved job
    source            TEXT NOT NULL,
    dest              TEXT NOT NULL,
    mode              TEXT NOT NULL,                      -- 'copy' or 'mirror'
    dry_run           INTEGER NOT NULL DEFAULT 0,
    start_time        TEXT NOT NULL,
    end_time          TEXT NOT NULL,
    duration_seconds  REAL NOT NULL,
    exit_code         INTEGER NOT NULL,
    description       TEXT NOT NULL,
    is_failure        INTEGER NOT NULL DEFAULT 0,
    dirs_copied       INTEGER,
    dirs_skipped      INTEGER,
    dirs_failed       INTEGER,
    dirs_extras       INTEGER,
    files_copied      INTEGER,
    files_skipped     INTEGER,
    files_failed      INTEGER,
    files_extras      INTEGER,
    bytes_copied      INTEGER,
    throughput_mb_s   REAL,
    throughput_files_s REAL,
    seconds_per_gb    REAL,
    acl_chained       INTEGER NOT NULL DEFAULT 0,
    acl_summary_json  TEXT,                               -- {"missing":n,"extra":n,"owner_diff":n,...} if chained
    raw_summary_json  TEXT NOT NULL,                       -- full parsed robocopy summary, for auditing
    FOREIGN KEY (job_id) REFERENCES jobs (job_id)
);

CREATE INDEX IF NOT EXISTS idx_run_history_job_id ON run_history (job_id);
CREATE INDEX IF NOT EXISTS idx_run_history_start_time ON run_history (start_time);

CREATE TABLE IF NOT EXISTS usage_ledger (
    entry_id     TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    bytes_copied INTEGER NOT NULL,
    recorded_at  TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES run_history (run_id)
);
"""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex


@contextmanager
def _connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    """Creates the database file and tables if they don't already exist."""
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)


# --------------------------------------------------------------------------
# Job definitions
# --------------------------------------------------------------------------

def create_job(
    name: str,
    source: str,
    dest: str,
    mode: str = "copy",
    schedule_expr: Optional[str] = None,
    threads: int = 16,
    retries: int = 3,
    auto_verify_acl: bool = False,
    enabled: bool = True,
    db_path: Path = DEFAULT_DB_PATH,
) -> str:
    """Creates a job definition and returns its job_id."""
    job_id = _new_id()
    now = _utcnow_iso()
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO jobs
               (job_id, name, source, dest, mode, schedule_expr, threads,
                retries, auto_verify_acl, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, name, source, dest, mode, schedule_expr, threads,
             retries, int(auto_verify_acl), int(enabled), now, now),
        )
    return job_id


def update_job(job_id: str, db_path: Path = DEFAULT_DB_PATH, **fields: Any) -> None:
    """Updates arbitrary columns on a job definition (e.g. update_job(id, enabled=False))."""
    if not fields:
        return
    fields["updated_at"] = _utcnow_iso()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with _connect(db_path) as conn:
        conn.execute(f"UPDATE jobs SET {set_clause} WHERE job_id = ?", (*fields.values(), job_id))


def delete_job(job_id: str, db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))


def get_job(job_id: str, db_path: Path = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


def list_jobs(db_path: Path = DEFAULT_DB_PATH, enabled_only: bool = False) -> List[Dict[str, Any]]:
    query = "SELECT * FROM jobs"
    if enabled_only:
        query += " WHERE enabled = 1"
    query += " ORDER BY name"
    with _connect(db_path) as conn:
        return [dict(row) for row in conn.execute(query).fetchall()]


# --------------------------------------------------------------------------
# Run history
# --------------------------------------------------------------------------

def _summary_int(summary: Dict[str, Dict[str, str]], row: str, col: str) -> Optional[int]:
    """Pulls one cell out of robocopy_sync.parse_summary()'s nested dict, as an int."""
    try:
        raw = summary[row][col]
        # robocopy sometimes reports byte counts with a unit suffix (k/m/g); keep it simple
        # and only parse plain integers here - callers can fall back to raw_summary_json.
        return int(raw)
    except (KeyError, ValueError, TypeError):
        return None


def record_run(
    result: Dict[str, Any],
    source: str,
    dest: str,
    mode: str,
    dry_run: bool,
    start_time: datetime,
    end_time: datetime,
    job_id: Optional[str] = None,
    acl_summary: Optional[Dict[str, int]] = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> str:
    """
    Logs one sync run to run_history, given the dict returned by
    robocopy_sync.run_robocopy() plus the metadata that call doesn't itself
    track (job_id, wall-clock start/end, and an optional chained ACL
    comparison summary).

    Also appends to usage_ledger so UsageTracker can compute lifetime usage.
    Returns the new run_id.
    """
    summary = result.get("summary", {})
    duration = max((end_time - start_time).total_seconds(), 0.0)

    dirs_copied = _summary_int(summary, "Dirs", "copied")
    dirs_skipped = _summary_int(summary, "Dirs", "skipped")
    dirs_failed = _summary_int(summary, "Dirs", "failed")
    dirs_extras = _summary_int(summary, "Dirs", "extras")
    files_copied = _summary_int(summary, "Files", "copied")
    files_skipped = _summary_int(summary, "Files", "skipped")
    files_failed = _summary_int(summary, "Files", "failed")
    files_extras = _summary_int(summary, "Files", "extras")
    bytes_copied = _summary_int(summary, "Bytes", "copied")

    throughput_mb_s = None
    throughput_files_s = None
    seconds_per_gb = None
    if duration > 0:
        if bytes_copied:
            throughput_mb_s = (bytes_copied / (1024 ** 2)) / duration
            gb_copied = bytes_copied / (1024 ** 3)
            if gb_copied > 0:
                seconds_per_gb = duration / gb_copied
        if files_copied:
            throughput_files_s = files_copied / duration

    exit_code = int(result.get("exit_code", -1))
    is_fail = bool(result.get("cancelled")) or exit_code >= 8  # matches robocopy_sync.is_failure convention

    run_id = _new_id()
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO run_history
               (run_id, job_id, source, dest, mode, dry_run, start_time, end_time,
                duration_seconds, exit_code, description, is_failure,
                dirs_copied, dirs_skipped, dirs_failed, dirs_extras,
                files_copied, files_skipped, files_failed, files_extras,
                bytes_copied, throughput_mb_s, throughput_files_s, seconds_per_gb,
                acl_chained, acl_summary_json, raw_summary_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, job_id, source, dest, mode, int(dry_run),
                start_time.astimezone(timezone.utc).isoformat(timespec="seconds"),
                end_time.astimezone(timezone.utc).isoformat(timespec="seconds"),
                duration, exit_code, result.get("description", ""), int(is_fail),
                dirs_copied, dirs_skipped, dirs_failed, dirs_extras,
                files_copied, files_skipped, files_failed, files_extras,
                bytes_copied, throughput_mb_s, throughput_files_s, seconds_per_gb,
                int(acl_summary is not None), json.dumps(acl_summary) if acl_summary else None,
                json.dumps(summary),
            ),
        )
        if job_id:
            conn.execute(
                "UPDATE jobs SET last_run_id = ?, last_run_status = ?, updated_at = ? WHERE job_id = ?",
                (run_id, result.get("description", ""), _utcnow_iso(), job_id),
            )
        if bytes_copied and not dry_run:
            conn.execute(
                "INSERT INTO usage_ledger (entry_id, run_id, bytes_copied, recorded_at) VALUES (?, ?, ?, ?)",
                (_new_id(), run_id, bytes_copied, _utcnow_iso()),
            )
    return run_id


def list_run_history(
    job_id: Optional[str] = None,
    limit: int = 100,
    db_path: Path = DEFAULT_DB_PATH,
) -> List[Dict[str, Any]]:
    query = "SELECT * FROM run_history"
    params: List[Any] = []
    if job_id:
        query += " WHERE job_id = ?"
        params.append(job_id)
    query += " ORDER BY start_time DESC LIMIT ?"
    params.append(limit)
    with _connect(db_path) as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


# --------------------------------------------------------------------------
# Licensing scaffolding (stubbed - not enforcing anything yet)
# --------------------------------------------------------------------------

@dataclass
class LicenseStatus:
    tier: str = "free"
    valid: bool = True
    expiry: Optional[str] = None
    detail: str = "Stub: no real licensing scheme implemented yet - always reports unlimited/valid."


class LicenseManager:
    """
    Stub. Always reports the free tier as valid with no expiry. Swap the
    body of `get_status()` for real license-file/server validation later;
    everything downstream (UsageTracker, GUI banners) should only ever
    read `LicenseStatus`, so the rest of the app doesn't need to change
    when real licensing lands.
    """

    def get_status(self) -> LicenseStatus:
        return LicenseStatus()

    def quota_bytes(self) -> Optional[int]:
        """Returns the lifetime byte quota for the current tier, or None for unlimited."""
        status = self.get_status()
        if status.tier == "free":
            return FREE_TIER_LIFETIME_BYTES
        return None  # paid tiers: unlimited (or extend this with real tier limits later)


@dataclass
class UsageCheckResult:
    allowed: bool
    bytes_used_lifetime: int
    quota_bytes: Optional[int]
    message: str = ""


class UsageTracker:
    """
    Accumulates bytes actually copied (from run_history / usage_ledger)
    against the current tier's lifetime quota. Fully functional as a
    read/aggregate layer; nothing calls `check_before_run` yet to actually
    block a job - that enforcement hook is for the scheduler/job-runner to
    wire in later.
    """

    def __init__(self, license_manager: Optional[LicenseManager] = None, db_path: Path = DEFAULT_DB_PATH):
        self.license_manager = license_manager or LicenseManager()
        self.db_path = db_path

    def bytes_used_lifetime(self) -> int:
        with _connect(self.db_path) as conn:
            row = conn.execute("SELECT COALESCE(SUM(bytes_copied), 0) AS total FROM usage_ledger").fetchone()
            return int(row["total"])

    def check_before_run(self, estimated_bytes: int = 0) -> UsageCheckResult:
        """
        Call this before starting a job. Currently informational only
        (LicenseManager always reports unlimited for paid tiers and a
        fixed 100GB lifetime cap for free), but returns enough detail to
        surface a real block or warning once licensing is enforced.
        """
        used = self.bytes_used_lifetime()
        quota = self.license_manager.quota_bytes()
        if quota is None:
            return UsageCheckResult(allowed=True, bytes_used_lifetime=used, quota_bytes=None,
                                     message="Unlimited tier - no quota check applied.")
        projected = used + estimated_bytes
        if projected > quota:
            return UsageCheckResult(
                allowed=False, bytes_used_lifetime=used, quota_bytes=quota,
                message=(f"This run would bring lifetime usage to "
                         f"{projected / (1024**3):.1f}GB, over the free tier's "
                         f"{quota / (1024**3):.0f}GB lifetime cap."),
            )
        return UsageCheckResult(
            allowed=True, bytes_used_lifetime=used, quota_bytes=quota,
            message=f"{used / (1024**3):.1f}GB of {quota / (1024**3):.0f}GB lifetime quota used.",
        )


if __name__ == "__main__":
    # Quick self-test / demo, safe to run on any platform (no Windows APIs touched).
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        demo_db = Path(tmp) / "tfsync_demo.db"
        init_db(demo_db)

        job_id = create_job(
            "Nightly finance share sync", r"\\src\finance", r"\\dst\finance",
            mode="mirror", schedule_expr="daily@02:00", auto_verify_acl=True, db_path=demo_db,
        )
        print("Created job:", job_id)

        fake_result = {
            "exit_code": 1,
            "cancelled": False,
            "description": "OK - files copied, no mismatches or failures.",
            "summary": {
                "Dirs": {"total": "10", "copied": "2", "skipped": "8", "mismatch": "0", "failed": "0", "extras": "0"},
                "Files": {"total": "500", "copied": "120", "skipped": "380", "mismatch": "0", "failed": "0", "extras": "0"},
                "Bytes": {"total": "10737418240", "copied": "2147483648", "skipped": "8589934592", "mismatch": "0", "failed": "0", "extras": "0"},
            },
        }
        start = datetime.now(timezone.utc)
        end = start
        run_id = record_run(
            fake_result, r"\\src\finance", r"\\dst\finance", mode="mirror", dry_run=False,
            start_time=start, end_time=end, job_id=job_id,
            acl_summary={"missing": 0, "extra": 0, "owner_diff": 0, "ace_diff": 0},
            db_path=demo_db,
        )
        print("Recorded run:", run_id)
        print("History:", list_run_history(db_path=demo_db))

        tracker = UsageTracker(db_path=demo_db)
        print("Usage check:", tracker.check_before_run(estimated_bytes=50 * 1024**3))
