#!/usr/bin/env python3
"""
TFSync (Total File Sync) — ACL Comparison CLI
================================================

Compares NTFS ACLs (owner, primary group, explicit + inherited ACEs) between
a source and destination SMB share, recursively, for migration/sync validation.

REQUIREMENTS
    - Must run on Windows (uses the Win32 security APIs).
    - pip install pywin32
    - Run as an account with Administrator / Backup Operator rights on both
      source and destination (the script auto-enables SeBackupPrivilege).

USAGE
    python compare_acls.py ^
        --source "\\\\srcserver\\share\\path" ^
        --dest   "\\\\dstserver\\share\\path" ^
        --output report.csv

    Optional:
        --dirs-only     only compare folder-level permissions (skip files)

Also see tfsync_gui.py for a PyQt5 GUI front-end to this same engine.
"""

import argparse
import csv
import os
import sys

import acl_compare_core as core


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare NTFS/ACL permissions between two SMB/CIFS UNC paths."
    )
    parser.add_argument("--source", required=True, help=r"Source UNC path, e.g. \\server1\share\folder")
    parser.add_argument("--dest", required=True, help=r"Destination UNC path, e.g. \\server2\share\folder")
    parser.add_argument("--output", default="acl_comparison_report.csv", help="CSV output path")
    parser.add_argument("--dirs-only", action="store_true",
                         help="Only compare folder-level permissions, skip individual files")
    parser.add_argument("--resolve-host", default=None,
                         help="If SIDs don't resolve locally (e.g. local accounts on a file "
                              "server), retry the lookup against this host, e.g. a server name")
    parser.add_argument("--threads", type=int, default=16,
                         help="Number of parallel threads for reading ACLs (default: 16). "
                              "Higher can speed up scans on large trees; too high may overload "
                              "the file server or your connection.")
    parser.add_argument("--show-matches", action="store_true",
                         help="Also include items with no differences in the report as MATCH rows "
                              "(by default only differences are listed)")
    args = parser.parse_args()

    if not core.PYWIN32_AVAILABLE:
        print("ERROR: This script requires pywin32.\nInstall it with:  pip install pywin32", file=sys.stderr)
        sys.exit(1)

    if os.name != "nt":
        print("ERROR: This script must be run on Windows (requires pywin32 / Win32 security APIs).",
              file=sys.stderr)
        sys.exit(1)

    core.enable_privilege("SeBackupPrivilege")

    if core.paths_are_same(args.source, args.dest):
        print("ERROR: source and destination are the same path. Nothing to compare.", file=sys.stderr)
        sys.exit(1)

    if not os.path.isdir(core.long_path(args.source)):
        print(f"ERROR: source path not accessible: {args.source}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isdir(core.long_path(args.dest)):
        print(f"ERROR: destination path not accessible: {args.dest}", file=sys.stderr)
        sys.exit(1)

    with open(args.output, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(core.ROW_FIELDS)

        def on_row(row: dict) -> None:
            writer.writerow([row[field] for field in core.ROW_FIELDS])

        def on_progress(cur: int, total: int) -> None:
            print(f"  ...compared {cur}/{total} common items")

        counts = core.compare_trees(
            args.source, args.dest,
            dirs_only=args.dirs_only,
            resolve_host=args.resolve_host,
            max_workers=args.threads,
            include_matches=args.show_matches,
            log_cb=print,
            progress_cb=on_progress,
            row_cb=on_row,
        )

    print("\n=== Summary ===")
    print(f"Items only in source (missing in dest): {counts['missing']}")
    print(f"Items only in destination (extra):      {counts['extra']}")
    print(f"Owner mismatches:                        {counts['owner_diff']}")
    print(f"Group mismatches:                        {counts['group_diff']}")
    print(f"ACE differences:                         {counts['ace_diff']}")
    print(f"Items unreadable / errors:               {counts['error']}")
    print(f"Items fully matching:                    {counts['match']}")
    print(f"\nFull report written to: {args.output}")


if __name__ == "__main__":
    main()
