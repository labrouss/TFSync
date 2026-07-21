#!/usr/bin/env python3
"""
acl_compare_core.py
====================
Shared engine for comparing NTFS ACLs between two SMB/CIFS UNC paths.
Used by both compare_acls.py (CLI) and compare_acls_gui.py (PyQt5 GUI).

Requires: Windows + pywin32 (pip install pywin32)
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set, Tuple

try:
    import win32security
    import win32process
    import ntsecuritycon as con
    PYWIN32_AVAILABLE = True
except ImportError:
    PYWIN32_AVAILABLE = False

if PYWIN32_AVAILABLE:
    SECURITY_INFO = (
        win32security.OWNER_SECURITY_INFORMATION
        | win32security.GROUP_SECURITY_INFORMATION
        | win32security.DACL_SECURITY_INFORMATION
    )
    ACE_TYPE_NAMES = {
        con.ACCESS_ALLOWED_ACE_TYPE: "ALLOW",
        con.ACCESS_DENIED_ACE_TYPE: "DENY",
    }
    # Fixed Win32 ACE-flag value (not always exposed as ntsecuritycon.INHERITED_ACE
    # depending on the pywin32 build), documented as a stable constant by Microsoft.
    INHERITED_ACE_FLAG = getattr(con, "INHERITED_ACE", 0x10)

# Common Windows folder/file access-mask -> friendly name.
# Anything not matching exactly gets decomposed bit-by-bit (see decode_mask_detailed).
ACCESS_MASK_NAMES = [
    (0x1F01FF, "Full Control"),
    (0x1301BF, "Modify"),
    (0x1200A9, "Read & Execute"),
    (0x120089, "Read"),
    (0x100116, "Write"),
]

# Individual NTFS file/folder access rights (bit -> human name).
# The sum of every bit here equals the "Full Control" mask (0x1F01FF).
RIGHT_BITS = [
    (0x000001, "List Folder / Read Data"),
    (0x000002, "Create Files / Write Data"),
    (0x000004, "Create Folders / Append Data"),
    (0x000008, "Read Extended Attributes"),
    (0x000010, "Write Extended Attributes"),
    (0x000020, "Traverse Folder / Execute File"),
    (0x000040, "Delete Subfolders and Files"),
    (0x000080, "Read Attributes"),
    (0x000100, "Write Attributes"),
    (0x010000, "Delete"),
    (0x020000, "Read Permissions"),
    (0x040000, "Change Permissions"),
    (0x080000, "Take Ownership"),
    (0x100000, "Synchronize"),
]
FULL_CONTROL_MASK = 0x1F01FF

# Columns used everywhere a row is reported (CSV header / table header / dict keys)
ROW_FIELDS = ["RelativePath", "ItemType", "DifferenceType", "Detail", "SourceValue", "DestValue"]


class CancelledError(Exception):
    """Raised internally when a scan is cancelled via should_cancel()."""


def enable_privilege(name: str, log_cb: Optional[Callable[[str], None]] = None) -> None:
    """Best-effort enable of a privilege (e.g. SeBackupPrivilege) on the
    current process token so security descriptors can be read even where
    the running account isn't explicitly granted READ_CONTROL."""
    log = log_cb or (lambda msg: print(msg, file=sys.stderr))
    try:
        htoken = win32security.OpenProcessToken(
            win32process.GetCurrentProcess(),
            win32security.TOKEN_ADJUST_PRIVILEGES | win32security.TOKEN_QUERY,
        )
        priv_id = win32security.LookupPrivilegeValue(None, name)
        win32security.AdjustTokenPrivileges(
            htoken, False, [(priv_id, win32security.SE_PRIVILEGE_ENABLED)]
        )
    except Exception as e:
        log(f"Warning: could not enable {name}: {e}")


def long_path(path: str) -> str:
    """Prefix a UNC path for long-path (>260 char) support on Windows."""
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def normalize_path(path: str) -> str:
    """Normalize a UNC/Windows path for equality comparisons: case-insensitive,
    trailing-slash-insensitive, and collapses redundant separators."""
    stripped = path.strip().rstrip("\\/")
    return os.path.normcase(os.path.normpath(stripped))


def paths_are_same(path_a: str, path_b: str) -> bool:
    """True if two paths point at the same location (same UNC path, ignoring
    case and trailing slashes). Does not resolve DFS targets, mapped drive
    letters that alias a UNC path, or symlinks - it's a straightforward
    string-level equality check intended to catch copy/paste mistakes."""
    return normalize_path(path_a) == normalize_path(path_b)


def parse_unc_host(path: str) -> str:
    """Extract the server portion of a UNC path, including the leading
    double backslash, e.g.:
        \\\\fs.example.com\\share\\folder -> \\\\fs.example.com
    Returns "" if the path doesn't look like a UNC path."""
    p = path.strip()
    if not p.startswith("\\\\"):
        return ""
    server = p[2:].split("\\", 1)[0].split("/", 1)[0]
    return f"\\\\{server}" if server else ""


def parse_last_component(path: str) -> str:
    """Extract the final path segment (e.g. the leaf folder name), useful
    for naming a report after the source folder, e.g.:
        \\\\fs.example.com\\share\\AAA -> AAA
    Returns "" if the path has no segments."""
    p = path.strip().rstrip("\\/")
    segments = [seg for seg in p.replace("/", "\\").split("\\") if seg]
    return segments[-1] if segments else ""


def decode_mask(mask: int) -> str:
    """Compact form: preset name if it matches one exactly, else raw hex.
    Kept for places that want a short single-token label."""
    for val, name in ACCESS_MASK_NAMES:
        if mask == val:
            return name
    return f"0x{mask:06X}"


def decode_mask_detailed(mask: int) -> str:
    """Full human-readable breakdown of an access mask. Always includes the
    raw hex value alongside the plain-English description for traceability.

    Examples:
        0x1F01FF -> "Full Control (0x1F01FF)"
        0x1E01FF -> "Full Control except: Delete (0x1E01FF)"
        0x120089 -> "Read (0x120089)"
        0x000003 -> "List Folder / Read Data, Create Files / Write Data (0x000003)"
    """
    hexval = f"0x{mask:06X}"

    for val, name in ACCESS_MASK_NAMES:
        if mask == val:
            return f"{name} ({hexval})"

    non_standard_bits = mask & ~FULL_CONTROL_MASK
    if non_standard_bits == 0:
        missing = [name for bit, name in RIGHT_BITS if not (mask & bit)]
        if not missing:
            return f"Full Control ({hexval})"
        if len(missing) <= 4:
            return f"Full Control except: {', '.join(missing)} ({hexval})"

    present = [name for bit, name in RIGHT_BITS if mask & bit]
    if present:
        return f"{', '.join(present)} ({hexval})"

    # No recognizable standard bits at all (rare - e.g. a purely generic mask)
    return hexval


def sid_to_name(sid, resolve_host: Optional[str] = None) -> str:
    """Resolve a SID to DOMAIN\\name. Tries the local/default lookup first;
    if that fails and resolve_host is given (e.g. the file server itself),
    retries against that host's SAM/AD context - useful for local accounts
    on the server that the machine running this script can't otherwise see.
    Falls back to the raw string SID if nothing resolves."""
    hosts_to_try = [None]
    if resolve_host:
        hosts_to_try.append(resolve_host)

    for host in hosts_to_try:
        try:
            name, domain, _ = win32security.LookupAccountSid(host, sid)
            return f"{domain}\\{name}" if domain else name
        except Exception:
            continue

    try:
        return win32security.ConvertSidToStringSid(sid)
    except Exception:
        return "UNKNOWN_SID"


@dataclass
class AceInfo:
    trustee: str
    ace_type: str
    mask: int
    inherited: bool

    def key(self) -> Tuple[str, str, int, bool]:
        return (self.trustee, self.ace_type, self.mask, self.inherited)

    def __str__(self) -> str:
        inh = "Inherited" if self.inherited else "Explicit"
        return f"{self.trustee}: {self.ace_type} {decode_mask_detailed(self.mask)} ({inh})"


@dataclass
class SecurityInfo:
    owner: str
    group: str
    aces: List[AceInfo]
    error: Optional[str] = None


def _read_security_descriptor(path: str):
    """Try GetFileSecurity with the plain path first (GetFileSecurity is a
    legacy API that doesn't always accept the \\\\?\\ extended-length prefix),
    then fall back to the long-path form for paths that genuinely need it."""
    last_err = None
    for candidate in (path, long_path(path)):
        try:
            return win32security.GetFileSecurity(candidate, SECURITY_INFO)
        except Exception as e:
            last_err = e
            continue
    raise last_err


def get_security(path: str, resolve_host: Optional[str] = None) -> SecurityInfo:
    try:
        sd = _read_security_descriptor(path)
        owner_sid = sd.GetSecurityDescriptorOwner()
        group_sid = sd.GetSecurityDescriptorGroup()
        dacl = sd.GetSecurityDescriptorDacl()

        aces: List[AceInfo] = []
        if dacl is not None:
            for i in range(dacl.GetAceCount()):
                (ace_type, ace_flags), mask, sid = dacl.GetAce(i)
                type_name = ACE_TYPE_NAMES.get(ace_type, f"TYPE_{ace_type}")
                inherited = bool(ace_flags & INHERITED_ACE_FLAG)
                aces.append(
                    AceInfo(
                        trustee=sid_to_name(sid, resolve_host=resolve_host),
                        ace_type=type_name,
                        mask=mask,
                        inherited=inherited,
                    )
                )

        return SecurityInfo(
            owner=sid_to_name(owner_sid, resolve_host=resolve_host),
            group=sid_to_name(group_sid, resolve_host=resolve_host) if group_sid else "",
            aces=aces,
        )
    except Exception as e:
        return SecurityInfo(owner="", group="", aces=[], error=str(e))


def walk_tree(
    root: str,
    log_cb: Optional[Callable[[str], None]] = None,
) -> Dict[str, str]:
    """Return {relative_path: 'file'|'dir'} for the whole tree. Root itself
    is represented by the empty-string key."""
    log = log_cb or (lambda msg: print(msg, file=sys.stderr))
    items: Dict[str, str] = {"": "dir"}
    root = root.rstrip("\\")

    def _onerror(err: OSError) -> None:
        log(f"Warning: cannot access {err.filename}: {err}")

    for dirpath, dirnames, filenames in os.walk(long_path(root), onerror=_onerror):
        norm_dirpath = dirpath.replace("\\\\?\\UNC\\", "\\\\").replace("\\\\?\\", "")
        rel_dir = os.path.relpath(norm_dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir

        for d in dirnames:
            rel = os.path.join(rel_dir, d) if rel_dir else d
            items[rel] = "dir"
        for f in filenames:
            rel = os.path.join(rel_dir, f) if rel_dir else f
            items[rel] = "file"

    return items


def compare_trees(
    source_root: str,
    dest_root: str,
    dirs_only: bool = False,
    resolve_host: Optional[str] = None,
    max_workers: int = 16,
    include_matches: bool = False,
    log_cb: Optional[Callable[[str], None]] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    row_cb: Optional[Callable[[dict], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> Dict[str, object]:
    """
    Walk both trees and compare ACLs on every item common to both.
    Every difference found is reported via row_cb(dict) with keys ROW_FIELDS.
    If include_matches is True, items with no differences are also reported,
    as a "MATCH" row, instead of only being counted.
    Returns a counts dict, with an extra "cancelled" bool key.

    ACL reads for each item are I/O-bound (a GetFileSecurity round-trip over
    SMB), so they're dispatched across max_workers threads for a large
    speedup on high-latency or high-file-count shares - the same idea as
    robocopy's /MT. Only the ACL reads themselves run in worker threads;
    row_cb/log_cb/progress_cb are always invoked serially from whichever
    thread called compare_trees(), so no locking is needed in the callbacks
    themselves (e.g. Qt signal emission from a background QThread works
    exactly as it did before this change).
    """
    log = log_cb or (lambda msg: print(msg))
    emit_row = row_cb or (lambda row: None)
    report_progress = progress_cb or (lambda cur, tot: None)
    cancelled_check = should_cancel or (lambda: False)

    counts = {
        "missing": 0, "extra": 0, "owner_diff": 0, "group_diff": 0,
        "ace_diff": 0, "error": 0, "match": 0, "cancelled": False,
    }

    log(f"Scanning source tree: {source_root}")
    src_items = walk_tree(source_root, log_cb=log_cb)
    log(f"  found {len(src_items)} items")

    log(f"Scanning destination tree: {dest_root}")
    dst_items = walk_tree(dest_root, log_cb=log_cb)
    log(f"  found {len(dst_items)} items")

    src_keys: Set[str] = set(src_items.keys())
    dst_keys: Set[str] = set(dst_items.keys())

    missing_in_dst = sorted(src_keys - dst_keys)
    extra_in_dst = sorted(dst_keys - src_keys)
    common = sorted(src_keys & dst_keys)

    for rel in missing_in_dst:
        counts["missing"] += 1
        emit_row({
            "RelativePath": rel or "(root)", "ItemType": src_items[rel],
            "DifferenceType": "MISSING_IN_DEST",
            "Detail": "Present in source, missing in destination",
            "SourceValue": "", "DestValue": "",
        })

    for rel in extra_in_dst:
        counts["extra"] += 1
        emit_row({
            "RelativePath": rel or "(root)", "ItemType": dst_items[rel],
            "DifferenceType": "EXTRA_IN_DEST",
            "Detail": "Present in destination, not in source",
            "SourceValue": "", "DestValue": "",
        })

    total = len(common)

    def compare_one(rel: str) -> Optional[Tuple[List[dict], Dict[str, int]]]:
        """Runs in a worker thread. Returns (rows, count_deltas) or None if skipped."""
        item_type = src_items[rel]
        if dirs_only and item_type == "file":
            return None

        src_path = os.path.join(source_root, rel) if rel else source_root
        dst_path = os.path.join(dest_root, rel) if rel else dest_root

        src_sec = get_security(src_path, resolve_host=resolve_host)
        dst_sec = get_security(dst_path, resolve_host=resolve_host)

        rows: List[dict] = []
        deltas: Dict[str, int] = {}

        def bump(key: str) -> None:
            deltas[key] = deltas.get(key, 0) + 1

        if src_sec.error or dst_sec.error:
            bump("error")
            rows.append({
                "RelativePath": rel or "(root)", "ItemType": item_type,
                "DifferenceType": "READ_ERROR",
                "Detail": f"src_error={src_sec.error} dst_error={dst_sec.error}",
                "SourceValue": "", "DestValue": "",
            })
            return rows, deltas

        item_has_diff = False

        if src_sec.owner != dst_sec.owner:
            bump("owner_diff")
            item_has_diff = True
            rows.append({
                "RelativePath": rel or "(root)", "ItemType": item_type,
                "DifferenceType": "OWNER_DIFF", "Detail": "Owner mismatch",
                "SourceValue": src_sec.owner, "DestValue": dst_sec.owner,
            })

        if src_sec.group != dst_sec.group:
            bump("group_diff")
            item_has_diff = True
            rows.append({
                "RelativePath": rel or "(root)", "ItemType": item_type,
                "DifferenceType": "GROUP_DIFF", "Detail": "Primary group mismatch",
                "SourceValue": src_sec.group, "DestValue": dst_sec.group,
            })

        src_ace_keys = {a.key(): a for a in src_sec.aces}
        dst_ace_keys = {a.key(): a for a in dst_sec.aces}

        removed = set(src_ace_keys) - set(dst_ace_keys)
        added = set(dst_ace_keys) - set(src_ace_keys)

        for key in sorted(removed, key=str):
            bump("ace_diff")
            item_has_diff = True
            rows.append({
                "RelativePath": rel or "(root)", "ItemType": item_type,
                "DifferenceType": "ACE_MISSING_IN_DEST", "Detail": str(src_ace_keys[key]),
                "SourceValue": str(src_ace_keys[key]), "DestValue": "",
            })

        for key in sorted(added, key=str):
            bump("ace_diff")
            item_has_diff = True
            rows.append({
                "RelativePath": rel or "(root)", "ItemType": item_type,
                "DifferenceType": "ACE_ADDED_IN_DEST", "Detail": str(dst_ace_keys[key]),
                "SourceValue": "", "DestValue": str(dst_ace_keys[key]),
            })

        if not item_has_diff:
            bump("match")
            if include_matches:
                rows.append({
                    "RelativePath": rel or "(root)", "ItemType": item_type,
                    "DifferenceType": "MATCH",
                    "Detail": "Owner, group, and all ACEs match",
                    "SourceValue": src_sec.owner, "DestValue": dst_sec.owner,
                })

        return rows, deltas

    max_workers = max(1, int(max_workers))
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_rel = {executor.submit(compare_one, rel): rel for rel in common}

        for future in as_completed(future_to_rel):
            if cancelled_check():
                counts["cancelled"] = True
                log("Scan cancelled by user - waiting for in-flight lookups to finish...")
                for f in future_to_rel:
                    f.cancel()
                break

            result = future.result()
            done += 1

            if result is not None:
                rows, deltas = result
                for k, v in deltas.items():
                    counts[k] += v
                for row in rows:
                    emit_row(row)

            if done % 200 == 0 or done == total:
                report_progress(done, total)

    return counts
