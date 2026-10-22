"""
long_path_scan.py - Long-path (>= 260 character) tooling for TFSync.

Windows' classic MAX_PATH is 260 characters *including* the terminating NUL,
so any path of 260 characters or more fails in software that is not
long-path aware (Explorer, many desktop apps, older SMB clients). Qumulo (and
most NAS platforms) can store much longer paths, so a migration will happily
copy them - the problem only shows up later, when end users try to open them.

This module is deliberately free of Qt and works on any OS (the registry
check degrades gracefully off Windows) so it can be tested anywhere and used
from the GUI, the headless runner, or a CLI.

It provides:
  * to_extended_path()        - \\\\?\\UNC\\server\\share\\... / \\\\?\\C:\\... conversion
  * check_long_paths_enabled()- reads HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem\\LongPathsEnabled
  * scan_long_paths()         - walks a source tree and reports every path whose
                                length (or projected length under the destination
                                root) reaches the threshold
  * SCOPE_ALIGNMENT_STATEMENT - customer-facing responsibility wording
  * write_csv()               - export results
"""

from __future__ import annotations

import csv
import os
from collections import Counter
from typing import Callable, Dict, List, Optional

import acl_compare_core as core

# Win32 MAX_PATH (includes the terminating NUL, so a 260-char path already fails).
MAX_PATH = 260
# Directory names additionally need room for an 8.3 file name; the Win32 limit is 248.
MAX_DIR_PATH = 248

REGISTRY_KEY = r"SYSTEM\CurrentControlSet\Control\FileSystem"
REGISTRY_VALUE = "LongPathsEnabled"
REGISTRY_DISPLAY = r"HKLM\SYSTEM\CurrentControlSet\Control\FileSystem"

ROW_FIELDS = ["RelativePath", "ItemType", "SourceLength", "DestLength", "Exceeds", "FullSourcePath"]

REGISTRY_FIX_POWERSHELL = (
    "Set-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem' "
    "-Name 'LongPathsEnabled' -Value 1 -Type DWord"
)
REGISTRY_FIX_REG = (
    'reg add "HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem" '
    '/v LongPathsEnabled /t REG_DWORD /d 1 /f'
)

SCOPE_ALIGNMENT_STATEMENT = (
    "Long Path Notice (Scope Alignment)\n"
    "\n"
    "Qumulo supports storing file and folder paths longer than 260 characters, "
    "and the migration will preserve them as they exist on the source. However, "
    "many Windows clients and applications (including Windows Explorer and "
    "software that is not long-path aware) cannot open, rename or back up items "
    "whose full path is 260 characters or longer.\n"
    "\n"
    "Any end-user client access issue caused by paths longer than 260 characters "
    "- including the need to enable Windows long-path support on client "
    "machines, update applications, or shorten/flatten the folder structure - "
    "remains the responsibility of the customer's IT desktop management team "
    "and is outside the scope of the migration engagement.\n"
    "\n"
    "A scan of the source shares for paths over 260 characters is performed "
    "before the initial full sync so the customer can decide in advance whether "
    "the folder structure needs to be flattened for end-user compatibility."
)

HOST_GUIDANCE = (
    "Migration host prerequisite: enable long path support on the server that "
    "runs the migration jobs (Robocopy host).\n"
    f"  Key:   {REGISTRY_DISPLAY}\n"
    f"  Value: {REGISTRY_VALUE} = 1 (DWORD)\n"
    "Run from an elevated prompt:\n"
    f"  {REGISTRY_FIX_REG}\n"
    "or in elevated PowerShell:\n"
    f"  {REGISTRY_FIX_POWERSHELL}\n"
    "A reboot (or at least restarting the migration application) is recommended "
    "afterwards."
)


# ---------------------------------------------------------------- syntax ---
def to_extended_path(path: str) -> str:
    """Convert a UNC or drive path to Windows extended-length syntax.

    \\\\server\\share\\dir  ->  \\\\?\\UNC\\server\\share\\dir
    C:\\dir               ->  \\\\?\\C:\\dir
    Already-prefixed paths are returned unchanged (idempotent). Forward slashes
    are converted to backslashes because the \\\\?\\ prefix disables the
    automatic normalisation Windows normally applies. Trailing separators are
    stripped (except on a bare drive root).
    """
    path = path.strip()
    if not path:
        return path
    if path[:4] in ("\\\\?\\", "//?/"):
        return path
    path = path.replace("/", "\\")
    if path.startswith("\\\\"):
        return core.long_path(path.rstrip("\\"))
    stripped = path.rstrip("\\")
    if len(stripped) == 2 and stripped[1] == ":":
        stripped += "\\"
    return core.long_path(stripped)


def from_extended_path(path: str) -> str:
    """Inverse of to_extended_path(): \\\\?\\UNC\\s\\x -> \\\\s\\x, \\\\?\\C:\\x -> C:\\x."""
    if path[:8].upper() == "\\\\?\\UNC\\":
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def is_unc_or_drive_path(path: str) -> bool:
    p = path.strip()
    return p.startswith("\\\\") or (len(p) >= 2 and p[1] == ":")


def _fs_path(path: str) -> str:
    """Path to hand to the OS for reading. On Windows, long paths get the
    extended prefix so os.scandir works past MAX_PATH regardless of the
    LongPathsEnabled setting; elsewhere the path is used as-is."""
    if os.name == "nt" and len(path) >= MAX_DIR_PATH - 20:
        return to_extended_path(path)
    return path


# -------------------------------------------------------------- registry ---
def check_long_paths_enabled() -> Dict[str, object]:
    """Report the state of LongPathsEnabled on THIS machine.

    Returns {applicable, enabled, value, message}. `applicable` is False on
    non-Windows hosts; `enabled` is None when the value cannot be determined
    (key/value missing means the OS default, which is disabled on Windows
    10/Server 2016 and earlier builds, so a missing value reports enabled=False).
    """
    if os.name != "nt":
        return {
            "applicable": False, "enabled": None, "value": None,
            "message": "Not a Windows host - the LongPathsEnabled registry check does not apply.",
        }
    try:
        import winreg  # type: ignore
    except ImportError:  # pragma: no cover - only on exotic builds
        return {"applicable": False, "enabled": None, "value": None,
                "message": "winreg is unavailable; cannot read the registry."}

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REGISTRY_KEY, 0,
                            winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)) as key:
            value, value_type = winreg.QueryValueEx(key, REGISTRY_VALUE)
    except FileNotFoundError:
        return {
            "applicable": True, "enabled": False, "value": None,
            "message": f"{REGISTRY_VALUE} is not set (Windows default = disabled).",
        }
    except OSError as e:
        return {"applicable": True, "enabled": None, "value": None,
                "message": f"Could not read {REGISTRY_DISPLAY}\\{REGISTRY_VALUE}: {e}"}

    enabled = (value_type == winreg.REG_DWORD and int(value) == 1)
    return {
        "applicable": True, "enabled": enabled, "value": value,
        "message": (f"{REGISTRY_VALUE} = {value} - long paths are ENABLED on this host."
                    if enabled else
                    f"{REGISTRY_VALUE} = {value!r} - long paths are NOT enabled on this host."),
    }


# ------------------------------------------------------------------ scan ---
def _join_len(root: str, rel: str) -> int:
    """Length of root + separator + rel (rel may be '' for the root itself)."""
    root = root.rstrip("\\/")
    return len(root) + (1 + len(rel) if rel else 0)


def scan_long_paths(
    source: str,
    dest: Optional[str] = None,
    threshold: int = MAX_PATH,
    include_dirs: bool = True,
    progress_cb: Optional[Callable[[int, int, int], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> Dict[str, object]:
    """Walk `source` and report items whose path reaches `threshold` characters.

    A path is flagged when EITHER its length under the source root OR its
    projected length under the destination root (when `dest` is given) is
    >= threshold. The destination projection matters because a long
    destination prefix can push an otherwise-fine source path over the limit.

    progress_cb(scanned_files, scanned_dirs, flagged) is called periodically.
    Unreadable directories are collected in result["errors"], not raised.

    Returns {
      rows:     [dict per flagged item, keys = ROW_FIELDS, longest first],
      files, dirs, flagged, flagged_files, flagged_dirs,
      max_source_length, max_dest_length, errors:[(path, message)],
      by_top_folder: [(first path component, flagged count), ...] desc,
      cancelled, threshold, source, dest
    }
    """
    cancel = should_cancel or (lambda: False)
    source = source.strip()
    dest = dest.strip() if dest else None
    sep = "\\" if (source.startswith("\\\\") or os.name == "nt") else os.sep

    rows: List[Dict[str, object]] = []
    errors: List[tuple] = []
    files = dirs = 0
    max_src = max_dst = 0
    top_counter: Counter = Counter()
    cancelled = False
    tick = 0

    stack = [""]  # relative directory paths still to scan ('' = root)
    while stack:
        if cancel():
            cancelled = True
            break
        rel_dir = stack.pop()
        abs_dir = source if not rel_dir else source.rstrip("\\/") + sep + rel_dir
        try:
            with os.scandir(_fs_path(abs_dir)) as it:
                entries = list(it)
        except OSError as e:
            errors.append((abs_dir, str(e)))
            continue

        for entry in entries:
            rel = entry.name if not rel_dir else rel_dir + sep + entry.name
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                is_dir = False
            if is_dir:
                dirs += 1
                stack.append(rel)
            else:
                files += 1
            if is_dir and not include_dirs:
                continue

            src_len = _join_len(source, rel)
            dst_len = _join_len(dest, rel) if dest else 0
            max_src = max(max_src, src_len)
            max_dst = max(max_dst, dst_len)

            limit = threshold
            exceeds = []
            if src_len >= limit:
                exceeds.append("Source")
            if dest and dst_len >= limit:
                exceeds.append("Destination")
            if exceeds:
                rows.append({
                    "RelativePath": rel,
                    "ItemType": "Directory" if is_dir else "File",
                    "SourceLength": src_len,
                    "DestLength": dst_len if dest else "",
                    "Exceeds": " + ".join(exceeds),
                    "FullSourcePath": source.rstrip("\\/") + sep + rel,
                })
                top_counter[rel.split(sep, 1)[0]] += 1

        tick += 1
        if progress_cb and (tick % 25 == 0):
            progress_cb(files, dirs, len(rows))

    if progress_cb:
        progress_cb(files, dirs, len(rows))

    rows.sort(key=lambda r: max(int(r["SourceLength"]), int(r["DestLength"] or 0)), reverse=True)
    return {
        "rows": rows,
        "files": files, "dirs": dirs,
        "flagged": len(rows),
        "flagged_files": sum(1 for r in rows if r["ItemType"] == "File"),
        "flagged_dirs": sum(1 for r in rows if r["ItemType"] == "Directory"),
        "max_source_length": max_src, "max_dest_length": max_dst,
        "errors": errors,
        "by_top_folder": top_counter.most_common(),
        "cancelled": cancelled,
        "threshold": threshold, "source": source, "dest": dest,
    }


def summarize(result: Dict[str, object]) -> str:
    """One-paragraph plain-text summary used in the GUI and exports."""
    lines = [
        f"Scanned {result['files']:,} files and {result['dirs']:,} folders under {result['source']}.",
        f"Paths >= {result['threshold']} characters: {result['flagged']:,} "
        f"({result['flagged_files']:,} files, {result['flagged_dirs']:,} folders).",
        f"Longest source path: {result['max_source_length']} characters."
        + (f" Longest projected destination path: {result['max_dest_length']} characters."
           if result.get("dest") else ""),
    ]
    if result["errors"]:
        lines.append(f"{len(result['errors'])} folder(s) could not be read (see errors).")
    if result["cancelled"]:
        lines.append("Scan was cancelled - results are partial.")
    if result["flagged"]:
        lines.append(
            "Recommendation: review these paths with the customer before the initial full "
            "sync to decide whether the folder structure should be flattened."
        )
    else:
        lines.append("No paths reach the limit - no flattening is required for end-user compatibility.")
    return "\n".join(lines)


def write_csv(result: Dict[str, object], csv_path: str) -> None:
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=ROW_FIELDS)
        w.writeheader()
        for row in result["rows"]:
            w.writerow(row)
