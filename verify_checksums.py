"""
verify_checksums.py - Command-line content verification (SHA-256 by default).

Compares every file under SOURCE with its counterpart under DEST by hashing
both, as an extra check on top of robocopy and the ACL comparison.

Examples:
  python verify_checksums.py \\\\nas1\\share \\\\qumulo\\share --report checksums.csv
  python verify_checksums.py D:\\data E:\\data --sample 10 --threads 16
"""

import argparse
import csv
import sys

import checksum_verify as cv


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source")
    ap.add_argument("dest")
    ap.add_argument("--algorithm", choices=cv.ALGORITHMS, default=cv.DEFAULT_ALGORITHM)
    ap.add_argument("--threads", type=int, default=8, help="Parallel file hashers (default 8)")
    ap.add_argument("--sample", type=int, default=100, metavar="PCT",
                    help="Verify only PCT percent of files (deterministic sample, default 100)")
    ap.add_argument("--include-matches", action="store_true", help="Also write identical files to the report")
    ap.add_argument("--report", default="checksum_report.csv", help="CSV report path")
    args = ap.parse_args()

    with open(args.report, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cv.ROW_FIELDS)
        counts = cv.verify_trees(
            args.source, args.dest, algorithm=args.algorithm, max_workers=args.threads,
            sample_pct=args.sample, include_matches=args.include_matches,
            row_cb=lambda r: w.writerow([r[k] for k in cv.ROW_FIELDS]), log_cb=print,
        )
    print("\n" + cv.summarize(counts))
    print(f"Report: {args.report}")
    return 1 if cv.problems(counts) or counts["cancelled"] else 0


if __name__ == "__main__":
    sys.exit(main())
