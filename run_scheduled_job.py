#!/usr/bin/env python3
"""
run_scheduled_job.py
=====================
Headless entry point invoked by Windows Task Scheduler (via tasks that
task_scheduler.py registers) to actually execute one TFSync job: run the
sync, log it to run history, optionally chain an ACL verification, and
apply retention - the same logic the GUI's "Run Now" performs, minus any
Qt/UI dependency, so it can run unattended with nobody logged into the GUI.

USAGE
    run_scheduled_job.exe --job-id <job_id>
    python run_scheduled_job.py --job-id <job_id>      (running from source)

Exit code is 0 if the run completed without a robocopy-reported failure
(and wasn't cancelled), non-zero otherwise, so Task Scheduler's "Last
Result" column reflects the real outcome.
"""

import argparse
import csv
import os
import sys
import uuid
from datetime import datetime, timezone

import acl_compare_core as core
import robocopy_sync
import tfsync_store as store


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one TFSync scheduled job headlessly.")
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()

    store.init_db()
    job = store.get_job(args.job_id)
    if not job:
        print(f"TFSync: job {args.job_id} no longer exists in the database - nothing to do.", file=sys.stderr)
        return 0
    if not job["enabled"]:
        print(f"TFSync: job '{job['name']}' is disabled - skipping.", file=sys.stderr)
        return 0

    if os.name != "nt":
        print("TFSync: the sync/ACL engines require Windows (robocopy + pywin32).", file=sys.stderr)
        return 1

    tracker = store.UsageTracker()
    check = tracker.check_before_run()
    if not check.allowed:
        print(f"TFSync: job '{job['name']}' blocked by usage quota: {check.message}", file=sys.stderr)
        return 1

    if core.paths_are_same(job["source"], job["dest"]):
        print(f"TFSync: job '{job['name']}' has identical source/dest paths - refusing to run.", file=sys.stderr)
        return 1

    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in job["name"])
    run_id = uuid.uuid4().hex

    log_dir = os.path.join(os.getcwd(), "job_logs")
    os.makedirs(log_dir, exist_ok=True)
    # Named after this specific run, not the job, so every run keeps its own
    # log instead of each new run silently overwriting the previous one
    # (robocopy_sync writes logs with /LOG:, which truncates on each run).
    log_path = os.path.join(log_dir, f"{safe_name}_{run_id[:8]}.log")

    start_time = datetime.now(timezone.utc)
    result = robocopy_sync.run_robocopy(
        job["source"], job["dest"],
        mirror=(job["mode"] == "mirror"), dry_run=False,
        threads=job["threads"], retries=job["retries"], wait_seconds=5,
        preserve_permissions=True, log_path=log_path,
        line_cb=lambda line: print(line, flush=True),
        should_cancel=lambda: False,
    )
    end_time = datetime.now(timezone.utc)

    run_id = store.record_run(
        result, job["source"], job["dest"], job["mode"], dry_run=False,
        start_time=start_time, end_time=end_time, job_id=job["job_id"],
        run_id=run_id, log_path=log_path,
    )
    print(f"TFSync: run {run_id} recorded - {result['description']}")

    is_fail = robocopy_sync.is_failure(result["exit_code"]) and not result["cancelled"]

    if job["auto_verify_acl"] and not result["cancelled"] and not is_fail:
        report_dir = os.path.join(os.getcwd(), "job_reports")
        os.makedirs(report_dir, exist_ok=True)
        report_path = os.path.join(report_dir, f"{safe_name}_{run_id[:8]}_acl_report.csv")

        with open(report_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(core.ROW_FIELDS)

            def row_cb(row: dict) -> None:
                writer.writerow([row[field] for field in core.ROW_FIELDS])

            counts = core.compare_trees(
                job["source"], job["dest"],
                dirs_only=False, resolve_host=None, max_workers=job["threads"],
                include_matches=False,
                log_cb=lambda msg: None, progress_cb=lambda cur, total: None,
                row_cb=row_cb, should_cancel=lambda: False,
            )
        acl_summary = {k: v for k, v in counts.items() if k != "cancelled"}
        store.update_run_acl_summary(run_id, acl_summary, report_path=report_path)
        print(f"TFSync: chained ACL verification recorded - {acl_summary}")

    return 1 if is_fail else 0


if __name__ == "__main__":
    sys.exit(main())
