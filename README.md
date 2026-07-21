# TFSync (Total File Sync)

A Windows tool for migrating/syncing file shares (via robocopy) and verifying
the result (via NTFS ACL comparison). Compares NTFS ACLs (owner, primary
group, explicit + inherited ACEs) between a source and destination SMB/CIFS
share, recursively, to validate permissions survived a migration or sync
intact. Ships as a CLI and a PyQt5 GUI sharing the same engine, plus a small
standalone helper for decoding raw permission masks.

![Build Executables](https://github.com/OWNER/REPO/actions/workflows/build.yml/badge.svg)

> Replace `OWNER/REPO` above with your actual GitHub path once this is pushed.

## Roadmap

A local job queue and run history (both SQLite-backed) now exist in the GUI
(tabs 3 and 4 below), along with licensing scaffolding (a stubbed
always-unlimited `LicenseManager`/`UsageTracker`, free tier capped at
100GB lifetime). **Not yet built**: the Windows Task Scheduler integration
that would actually register jobs to run unattended - job definitions store
a schedule expression today, but nothing acts on it yet, so jobs only run
when you click "Run Now".

## Job Queue (GUI tab 3)

Define reusable sync jobs (name, source, dest, mode, thread/retry settings,
optional auto-chained ACL verification, a schedule expression field that's
stored but not yet enforced) and run them on demand with **Run Now**. A few
notes on how this behaves today:

- **Max concurrent jobs** (default 2) is an actual cap - "Run Now" is
  blocked once that many jobs are running at once, to avoid an unbounded
  pile of simultaneous robocopy processes.
- **Thread warning threshold** (default 64) is a soft warning, not a cap -
  the "Active jobs" banner turns orange and explains why, but won't stop
  you from proceeding, since the queue length itself is intentionally
  unbounded (per the design brief).
- Jobs with **auto-verify ACL** enabled kick off a background ACL
  comparison after a successful (non-dry-run, non-cancelled) run and
  write its report to `job_reports/`; the summary counts get attached to
  that run's history entry once the comparison finishes.
- **Deleting a job decommissions it**: it unregisters any scheduled task
  (a no-op today - there's nothing to unregister until the Task Scheduler
  integration exists, but the hook is already wired in) and **deletes all
  of that job's run history** from the database. The confirmation dialog
  tells you how many history rows will go with it. Lifetime usage totals
  used for the licensing quota are kept regardless, since those bytes were
  actually transferred whether or not the job/its logs still exist.

## Run History (GUI tab 4)

Every sync run - manual (from tab 1) or from the Job Queue - is logged to
a local SQLite database (`tfsync_store.py`), including start/end time,
duration, mode, dry-run flag, dirs/files copied/skipped/failed/extras,
bytes copied, derived MB/s and seconds-per-GB, exit code + description,
and any chained ACL comparison summary. Filter by job (or "All runs") and
hit Refresh to pull the latest. Failed runs are highlighted in red.

**Retention**: history is capped per job (and separately for manual/ad-hoc
runs) so the database doesn't grow forever. Options, top of the Run
History tab:
- **Keep all runs** - no pruning
- **Keep last run only**
- **Keep last N runs** (default 50) - your own number

The chosen policy is applied automatically after every new run is
recorded, and can also be run immediately with **Apply Retention Now**
(e.g. right after lowering the number, to prune existing history down to
size). Pruning never touches the lifetime usage ledger used for the
licensing quota - that total is intentionally independent of how much
history you choose to keep.

The underlying database lives at
`%LOCALAPPDATA%\TFSync\tfsync.db` (see `tfsync_store.DEFAULT_DB_PATH`).

## Platform support (please read)

Reading an NTFS security descriptor over SMB requires the Win32
`GetFileSecurity` API (via `pywin32`) - there's no cross-platform equivalent.
That means:

| Component | Windows | Linux / macOS |
|---|---|---|
| `compare_acls.py` (CLI) | ✅ Full functionality | ❌ Exits with a clear error |
| `tfsync_gui.py` (GUI) | ✅ Full functionality | ❌ Shows a clear error dialog |
| `decode_mask.py` (mask decoder) | ✅ | ✅ Pure Python, works everywhere |

The GitHub Actions workflow reflects this: it builds full Windows executables
and installer, and only the `decode_mask` helper on Linux/macOS (x86_64 and
arm64) - there's no value in shipping a GUI/CLI binary elsewhere that can
only ever print "this must run on Windows."

## Features

- **Sync source to destination** via a robocopy wrapper, preserving NTFS
  permissions (owner, ACLs, timestamps) - copy-only by default (never
  deletes anything), with an explicit, gated Mirror mode for exact sync
  when you actually want deletions to propagate
- Recursively compares every file and folder common to both trees, and flags
  anything **missing** or **extra** on either side
- Compares **owner**, **primary group**, and every individual **ACE**
  (trustee, allow/deny, access mask, inherited vs explicit)
- Permission masks are decoded into **plain English** automatically
  (e.g. `Full Control except: Delete (0x1E01FF)`), not just raw hex
- **Multi-threaded** ACL reads (default 16 threads, configurable) - each read
  is a network round-trip, so this meaningfully speeds up large scans, the
  same idea as robocopy's `/MT`
- Optional **SID resolution against a specific host**, for local accounts
  that only resolve on the file server itself, not from wherever the tool runs
- Optional **"show matches"** mode to also list items with no differences,
  not just the differences
- Guards against comparing a **path against itself**
- GUI: live filterable/sortable results table, color-coded by difference
  type, dark mode by default (with a light/dark toggle), CSV export as
  results stream in, and auto-fill of the SID-resolution host and report
  filename from the source path

## Requirements

- Windows, for the CLI/GUI (see platform table above)
- Python 3.9+
- `pip install -r requirements.txt` (installs `pywin32` and `PyQt5`)
- Run as Administrator / an account with Backup Operator rights on both
  source and destination, so `SeBackupPrivilege` can take effect and every
  object's security descriptor can actually be read
- [Inno Setup](https://jrsoftware.org/isdl.php) 6, only if you want to build
  the installer locally (already preinstalled on GitHub's Windows runners, so
  not needed just to use CI)

## Repository layout

```
acl_compare_core.py                    Shared comparison engine (used by CLI and GUI)
compare_acls.py                        Command-line interface (compare)
tfsync_gui.py                    PyQt5 graphical interface (sync + compare)
robocopy_sync.py                       Shared robocopy wrapper engine (used by CLI and GUI)
sync_shares.py                         Command-line interface (sync)
decode_mask.py                         Standalone access-mask decoder (cross-platform)
tfsync_store.py                        SQLite job/run-history store + LicenseManager/UsageTracker stubs (cross-platform, no Windows deps)
requirements.txt                       Python dependencies
build.bat                              Local Windows build script (PyInstaller + installer)
installer/compare_acls_installer.iss   Inno Setup script for the Windows installer
.github/workflows/build.yml            CI: builds executables + installer for every push tag
```

## Running from source

CLI:
```
python compare_acls.py --source "\\srcserver\share\path" --dest "\\dstserver\share\path" --output report.csv
```

Useful flags:
| Flag | Purpose |
|---|---|
| `--dirs-only` | Only compare folder-level permissions, skip individual files |
| `--threads N` | Parallel ACL-read threads (default 16) |
| `--resolve-host HOST` | Retry SID lookups against this host if they don't resolve locally |
| `--show-matches` | Also report items with no differences, as `MATCH` rows |

GUI:
```
python tfsync_gui.py
```
All of the above are available as fields/checkboxes in the GUI, plus a
dark/light theme toggle (dark by default), a **Job Queue** tab for saved/
recurring job definitions, and a **Run History** tab (see above).

Decode a raw access mask on its own, no Windows required:
```
python decode_mask.py 0x1E01FF
-> Full Control except: Delete (0x1E01FF)
```

## Syncing shares (robocopy wrapper)

`sync_shares.py` (CLI) and the GUI's "1. Sync (robocopy)" tab wrap Windows'
built-in robocopy to copy a source share to a destination while preserving
NTFS permissions - the natural first step before running a comparison.

**Copy-only (default)** - `robocopy /E`, copies new/changed files and
folders but never deletes anything in the destination:
```
python sync_shares.py --source "\\srcserver\share\path" --dest "\\dstserver\share\path"
```

**Mirror** - `robocopy /MIR`, makes the destination an exact copy of the
source, which **deletes** files/folders in the destination that don't exist
in the source. Always preview first:
```
python sync_shares.py --source ... --dest ... --mirror --dry-run
python sync_shares.py --source ... --dest ... --mirror
```
The CLI requires typing `YES` to confirm a live (non-dry-run) mirror sync
unless `--yes` is passed; the GUI requires checking an explicit "I
understand..." checkbox before the Run Sync button becomes clickable in
Mirror mode, plus a final confirmation dialog.

Other flags: `--threads`, `--retries`, `--wait`, `--no-preserve-permissions`,
`--log` (robocopy log path) - see `python sync_shares.py --help`.

After a sync finishes in the GUI, you'll be asked each time whether to
immediately run the ACL comparison against the same source/destination to
verify the result - sync and compare share the same Source/Destination
fields at the top of the window.

## Building executables locally (Windows)

```
pip install -r requirements.txt
pip install pyinstaller
build.bat
```
Produces `dist\compare_acls.exe`, `dist\tfsync_gui.exe`, and
`dist\decode_mask.exe`. The GUI build defaults to `--console` so startup
errors are visible on first run; switch to `--windowed` in `build.bat` once
you've confirmed it runs cleanly.

If [Inno Setup](https://jrsoftware.org/isdl.php) 6 is installed at its
default path, `build.bat` also builds a proper installer at
`installer\installer_output\TFSync_Setup.exe` - a real Windows
installer with Start Menu shortcuts (including one that opens a command
prompt in the install folder for CLI use), an optional desktop icon, an
optional "add to PATH" checkbox, and a standard uninstaller entry in
Add/Remove Programs. If Inno Setup isn't found, this step is skipped with a
note - the raw `.exe` files still work fine on their own.

## Building via GitHub Actions

Push a tag matching `v*` (e.g. `v1.0.0`), or trigger the workflow manually
from the Actions tab. It builds:
- **Windows**: `compare_acls.exe`, `tfsync_gui.exe`, `decode_mask.exe`,
  and `TFSync_Setup.exe` (the installer, versioned from the tag)
- **Linux (x86_64)**, **macOS (x86_64)**, **macOS (arm64)**: `decode_mask`
  only, for the reasons explained above

If the tag matches `v*`, all of the above are also attached to a GitHub
Release automatically.

## Output format

Both CLI and GUI write a CSV with columns:
```
RelativePath, ItemType, DifferenceType, Detail, SourceValue, DestValue
```

`DifferenceType` is one of:
- `MISSING_IN_DEST` / `EXTRA_IN_DEST` - item exists on only one side
- `OWNER_DIFF` / `GROUP_DIFF` - owner or primary group mismatch
- `ACE_MISSING_IN_DEST` / `ACE_ADDED_IN_DEST` - individual ACE differences
- `READ_ERROR` - security descriptor couldn't be read on one or both sides
  (check `Detail` for the exact Win32 error)
- `MATCH` - no differences found (only present with `--show-matches` /
  "Include matching items")

## Known limitations

- Same-location detection (`paths_are_same`) is a normalized string
  comparison; it won't catch two different paths that happen to resolve to
  the same underlying share (e.g. a mapped drive letter vs. its UNC path, or
  two DFS namespace paths pointing at one target).
- Access-mask decoding covers the standard NTFS file/folder rights; fully
  custom or non-standard bit combinations fall back to a plain list of the
  set bits rather than a named preset.
