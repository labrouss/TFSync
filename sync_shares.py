#!/usr/bin/env python3
r"""
sync_shares.py
==============
Sync/copy a source SMB/CIFS share to a destination using robocopy, preserving
NTFS permissions (owner, ACLs, timestamps). Designed to pair with
compare_acls.py for a sync-then-verify migration workflow.

DEFAULT MODE is copy-only (robocopy /E): copies new/changed files and
folders, but NEVER deletes anything in the destination.

--mirror switches to an exact mirror (robocopy /MIR), which DELETES files
and folders in the destination that don't exist in the source. Always
sanity-check with --dry-run first when using --mirror.

REQUIREMENTS
    - Windows (robocopy ships with the OS - nothing extra to install)
    - Run as Administrator / an account with rights to read source ACLs and
      write destination ACLs, same as compare_acls.py

USAGE
    python sync_shares.py --source "\\srcserver\share\path" --dest "\\dstserver\share\path"
    python sync_shares.py --source ... --dest ... --mirror --dry-run
"""

import argparse
import os
import sys

import acl_compare_core as core
import robocopy_sync


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync/copy an SMB share to another using robocopy, preserving NTFS ACLs."
    )
    parser.add_argument("--source", required=True, help=r"Source UNC path, e.g. \\server1\share\folder")
    parser.add_argument("--dest", required=True, help=r"Destination UNC path, e.g. \\server2\share\folder")
    parser.add_argument("--mirror", action="store_true",
                         help="Exact mirror (robocopy /MIR) - DELETES files in dest not present in "
                              "source. Default is copy-only, which never deletes anything.")
    parser.add_argument("--dry-run", action="store_true",
                         help="Preview only (robocopy /L) - makes no changes on disk")
    parser.add_argument("--threads", type=int, default=16, help="Robocopy /MT thread count (default 16)")
    parser.add_argument("--retries", type=int, default=3, help="Robocopy /R retry count (default 3)")
    parser.add_argument("--wait", type=int, default=5, help="Robocopy /W retry wait in seconds (default 5)")
    parser.add_argument("--no-preserve-permissions", action="store_true",
                         help="Skip copying security/ownership info (uses /COPY:DAT instead of /COPY:DATSO)")
    parser.add_argument("--log", default="robocopy_sync.log", help="Robocopy log file path")
    parser.add_argument("--yes", action="store_true",
                         help="Skip the confirmation prompt for --mirror (e.g. for unattended/scripted runs)")
    args = parser.parse_args()

    if os.name != "nt":
        print("ERROR: robocopy is a Windows-only tool.", file=sys.stderr)
        sys.exit(1)

    if core.paths_are_same(args.source, args.dest):
        print("ERROR: source and destination are the same path. Refusing to run.", file=sys.stderr)
        sys.exit(1)

    if args.mirror and not args.dry_run and not args.yes:
        print(
            "\nWARNING: --mirror will DELETE files and folders in the destination\n"
            "that do not exist in the source. This cannot be undone.\n"
        )
        confirm = input('Type "YES" (all caps) to continue, anything else to abort: ')
        if confirm.strip() != "YES":
            print("Aborted - no changes made.")
            sys.exit(1)

    mode_label = "MIRROR (deletes extras)" if args.mirror else "copy-only (safe)"
    dry_label = " [DRY RUN - no changes will be made]" if args.dry_run else ""
    print(f"Starting {mode_label} sync{dry_label}")
    print(f"  Source: {args.source}")
    print(f"  Dest:   {args.dest}\n")

    result = robocopy_sync.run_robocopy(
        args.source, args.dest,
        mirror=args.mirror, dry_run=args.dry_run,
        threads=args.threads, retries=args.retries, wait_seconds=args.wait,
        preserve_permissions=not args.no_preserve_permissions,
        log_path=args.log,
        line_cb=print,
    )

    print("\n=== Summary ===")
    print(result["description"])
    for section, counts in result["summary"].items():
        print(f"  {section:6s}: {counts}")
    print(f"\nExit code: {result['exit_code']}  (full log: {args.log})")

    if robocopy_sync.is_failure(result["exit_code"]):
        sys.exit(1)


if __name__ == "__main__":
    main()
