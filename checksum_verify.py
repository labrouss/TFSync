"""
checksum_verify.py - Post-sync content verification for TFSync.

robocopy decides whether a file needs copying from size + timestamp and
never compares file *content*, so a clean robocopy run does not prove the
bytes at the destination equal the bytes at the source. This module closes
that gap: it hashes every file on both sides (SHA-256 by default) and
reports anything that doesn't match, complementing the ACL comparison
(which verifies permissions, not data).

Cross-platform, Qt-free, stdlib-only. Results use the same row layout as the
ACL comparison report (RelativePath, ItemType, DifferenceType, Detail,
SourceValue, DestValue) so the GUI's sortable/filterable CSV viewer works
on checksum reports unchanged.

Per file, in order:
  1. destination missing                   -> MISSING_IN_DEST
  2. sizes differ                          -> SIZE_MISMATCH   (no hashing needed)
  3. hash source and destination           -> HASH_MISMATCH / MATCH / READ_ERROR
A hash mismatch is hashed a second time before being reported, so a file
that was still being written or was briefly locked isn't flagged falsely.
Files that exist only in the destination are reported as EXTRA_IN_DEST
(skipped when sampling, since a sample can't say what is "extra").
"""

from __future__ import annotations

import hashlib
import os
import time
import zlib
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Callable, Dict, List, Optional, Tuple

import long_path_scan

ALGORITHMS = ("sha256", "sha1", "md5", "sha512")
DEFAULT_ALGORITHM = "sha256"
CHUNK_SIZE = 4 * 1024 * 1024

ROW_FIELDS = ["RelativePath", "ItemType", "DifferenceType", "Detail", "SourceValue", "DestValue"]

DIFF_TYPES = ["HASH_MISMATCH", "SIZE_MISMATCH", "MISSING_IN_DEST", "EXTRA_IN_DEST", "READ_ERROR", "MATCH"]


class Cancelled(Exception):
    pass


def hash_file(path: str, algorithm: str = DEFAULT_ALGORITHM,
              should_cancel: Optional[Callable[[], bool]] = None) -> str:
    """Hex digest of a file's content, read in 4 MB chunks."""
    h = hashlib.new(algorithm)
    with open(long_path_scan._fs_path(path), "rb") as f:
        while True:
            if should_cancel and should_cancel():
                raise Cancelled()
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _sep(root: str) -> str:
    return "\\" if (root.startswith("\\\\") or os.name == "nt") else os.sep


def _join(root: str, rel: str) -> str:
    return root.rstrip("\\/") + _sep(root) + rel


def _key(rel: str) -> str:
    """Comparison key for a relative path (case-insensitive on Windows)."""
    return os.path.normcase(rel.replace("\\", "/"))


def in_sample(rel: str, sample_pct: int) -> bool:
    """Deterministic sample: the same files are chosen on every run."""
    if sample_pct >= 100:
        return True
    return (zlib.crc32(_key(rel).encode("utf-8", "surrogatepass")) % 100) < sample_pct


def enumerate_files(root: str, should_cancel: Optional[Callable[[], bool]] = None,
                    errors: Optional[List[Tuple[str, str]]] = None) -> Dict[str, int]:
    """{relative path: size} for every regular file under root (symlinks skipped)."""
    out: Dict[str, int] = {}
    sep = _sep(root)
    stack = [""]
    while stack:
        if should_cancel and should_cancel():
            raise Cancelled()
        rel_dir = stack.pop()
        abs_dir = root if not rel_dir else root.rstrip("\\/") + sep + rel_dir
        try:
            with os.scandir(long_path_scan._fs_path(abs_dir)) as it:
                entries = list(it)
        except OSError as e:
            if errors is not None:
                errors.append((abs_dir, str(e)))
            continue
        for entry in entries:
            rel = entry.name if not rel_dir else rel_dir + sep + entry.name
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(rel)
                elif entry.is_file(follow_symlinks=False):
                    out[rel] = entry.stat(follow_symlinks=False).st_size
            except OSError as e:
                if errors is not None:
                    errors.append((_join(root, rel), str(e)))
    return out


def verify_trees(
    source_root: str,
    dest_root: str,
    algorithm: str = DEFAULT_ALGORITHM,
    max_workers: int = 8,
    sample_pct: int = 100,
    include_matches: bool = False,
    log_cb: Optional[Callable[[str], None]] = None,
    progress_cb: Optional[Callable[[int, int, int, int], None]] = None,
    row_cb: Optional[Callable[[dict], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> Dict[str, object]:
    """Compare file content between two trees.

    progress_cb(files_done, files_total, bytes_done, bytes_total)
    row_cb(dict with keys ROW_FIELDS) - always called from the calling thread.
    Returns counts: files_checked, matched, hash_mismatch, size_mismatch,
    missing_in_dest, extra_in_dest, read_error, not_sampled, bytes_hashed,
    seconds, algorithm, sample_pct, cancelled.
    """
    if algorithm not in ALGORITHMS:
        raise ValueError(f"unsupported algorithm {algorithm!r}")
    sample_pct = max(1, min(100, int(sample_pct)))
    log = log_cb or (lambda m: None)
    cancel = should_cancel or (lambda: False)
    emit = row_cb or (lambda r: None)
    started = time.monotonic()

    counts: Dict[str, object] = {
        "files_checked": 0, "matched": 0, "hash_mismatch": 0, "size_mismatch": 0,
        "missing_in_dest": 0, "extra_in_dest": 0, "read_error": 0, "not_sampled": 0,
        "bytes_hashed": 0, "seconds": 0.0, "algorithm": algorithm,
        "sample_pct": sample_pct, "cancelled": False,
    }

    def row(rel, diff, detail, sval="", dval=""):
        emit({"RelativePath": rel, "ItemType": "File", "DifferenceType": diff,
              "Detail": detail, "SourceValue": sval, "DestValue": dval})

    try:
        enum_errors: List[Tuple[str, str]] = []
        log(f"Enumerating source: {source_root}")
        src_files = enumerate_files(source_root, cancel, enum_errors)
        log(f"Enumerating destination: {dest_root}")
        dst_files = enumerate_files(dest_root, cancel, enum_errors)
        for path, msg in enum_errors:
            counts["read_error"] += 1
            row(path, "READ_ERROR", f"Could not list folder: {msg}")

        dst_by_key = {_key(r): (r, sz) for r, sz in dst_files.items()}
        to_hash: List[Tuple[str, int]] = []
        for rel, size in src_files.items():
            if not in_sample(rel, sample_pct):
                counts["not_sampled"] += 1
                continue
            counts["files_checked"] += 1
            hit = dst_by_key.get(_key(rel))
            if hit is None:
                counts["missing_in_dest"] += 1
                row(rel, "MISSING_IN_DEST", "File exists in source but not in destination", f"{size} bytes", "")
            elif hit[1] != size:
                counts["size_mismatch"] += 1
                row(rel, "SIZE_MISMATCH", "File sizes differ (content not hashed)", f"{size} bytes", f"{hit[1]} bytes")
            else:
                to_hash.append((rel, size))

        total_files = len(to_hash)
        total_bytes = sum(sz for _, sz in to_hash)
        log(f"Hashing {total_files:,} files ({total_bytes:,} bytes) on both sides with {algorithm.upper()}"
            + (f" - {sample_pct}% sample" if sample_pct < 100 else ""))
        done_files = done_bytes = 0
        if progress_cb:
            progress_cb(0, total_files, 0, total_bytes)

        def work(item: Tuple[str, int]):
            rel, size = item
            sp, dp = _join(source_root, rel), _join(dest_root, dst_by_key[_key(rel)][0])
            try:
                a = hash_file(sp, algorithm, cancel)
                b = hash_file(dp, algorithm, cancel)
                if a != b:   # re-hash once to rule out a file still being written / briefly locked
                    a2 = hash_file(sp, algorithm, cancel)
                    b2 = hash_file(dp, algorithm, cancel)
                    return rel, size, a2, b2, None
                return rel, size, a, b, None
            except Cancelled:
                return rel, size, None, None, "cancelled"
            except OSError as e:
                return rel, size, None, None, str(e)

        def handle(res) -> None:
            nonlocal done_files, done_bytes
            rel, size, a, b, err = res
            done_files += 1
            done_bytes += size
            if err == "cancelled":
                return
            if err:
                counts["read_error"] += 1
                row(rel, "READ_ERROR", f"Could not read file: {err}", f"{size} bytes", "")
            elif a == b:
                counts["matched"] += 1
                counts["bytes_hashed"] += size
                if include_matches:
                    row(rel, "MATCH", f"{algorithm.upper()} identical", a, b)
            else:
                counts["hash_mismatch"] += 1
                counts["bytes_hashed"] += size
                row(rel, "HASH_MISMATCH", f"{algorithm.upper()} differs (same size, confirmed on re-read)", a, b)
            if progress_cb:
                progress_cb(done_files, total_files, done_bytes, total_bytes)

        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
            pending = set()
            it = iter(to_hash)
            limit = max(1, max_workers) * 4
            exhausted = False
            while (pending or not exhausted) and not cancel():
                while not exhausted and len(pending) < limit:
                    try:
                        pending.add(pool.submit(work, next(it)))
                    except StopIteration:
                        exhausted = True
                if not pending:
                    break
                finished, pending = wait(pending, return_when=FIRST_COMPLETED)
                for fut in finished:
                    handle(fut.result())
            if cancel():
                for fut in pending:
                    fut.cancel()
                raise Cancelled()

        if sample_pct >= 100:
            src_keys = {_key(r) for r in src_files}
            for k, (rel, size) in dst_by_key.items():
                if k not in src_keys:
                    counts["extra_in_dest"] += 1
                    row(rel, "EXTRA_IN_DEST", "File exists in destination but not in source", "", f"{size} bytes")
    except Cancelled:
        counts["cancelled"] = True

    counts["seconds"] = round(time.monotonic() - started, 2)
    return counts


def problems(counts: Dict[str, object]) -> int:
    """Number of items needing attention."""
    return sum(int(counts[k]) for k in
              ("hash_mismatch", "size_mismatch", "missing_in_dest", "extra_in_dest", "read_error"))


def history_summary(counts: Dict[str, object]) -> Dict[str, object]:
    """Compact dict stored with a run in the history database."""
    keys = ("algorithm", "sample_pct", "files_checked", "matched", "hash_mismatch", "size_mismatch",
            "missing_in_dest", "extra_in_dest", "read_error", "bytes_hashed", "seconds")
    out = {k: counts[k] for k in keys}
    if counts.get("cancelled"):
        out["cancelled"] = True
    return out


def summarize(counts: Dict[str, object]) -> str:
    n = problems(counts)
    mb = int(counts["bytes_hashed"]) / (1024 * 1024)
    secs = float(counts["seconds"]) or 0.001
    head = (f"{counts['algorithm'].upper()} verification"
            + (f" ({counts['sample_pct']}% sample)" if counts["sample_pct"] < 100 else "")
            + f": {counts['files_checked']:,} files checked, {counts['matched']:,} identical, "
              f"{n:,} problem(s).")
    detail = (f"Hash mismatch {counts['hash_mismatch']:,} | size mismatch {counts['size_mismatch']:,} | "
              f"missing in destination {counts['missing_in_dest']:,} | extra in destination "
              f"{counts['extra_in_dest']:,} | read errors {counts['read_error']:,}")
    tail = f"Hashed {mb:,.0f} MB per side in {secs:,.0f}s."
    if counts.get("cancelled"):
        tail += " Cancelled - results are partial."
    return "\n".join([head, detail, tail])
