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

A local job queue and run history (both SQLite-backed) exist in the GUI
(tabs 3 and 4 below), along with licensing scaffolding (a stubbed
always-unlimited `LicenseManager`/`UsageTracker`, free tier capped at
100GB lifetime), **and** Windows Task Scheduler integration - enabled jobs
with a schedule are registered as real scheduled tasks and run unattended
via `run_scheduled_job.exe` (see the Job Queue section below for the
current logged-on-only limitation). Nothing major left from the original
design brief; remaining ideas are refinements (e.g. a credential-prompt
flow to support running fully logged-off/rebooted, not just logged-on).

## Job Queue (GUI tab 3)

Define reusable sync jobs (name, source, dest, mode, thread/retry settings,
optional auto-chained ACL verification, and an optional **Schedule**) and
run them on demand with **Run Now** - or let them run unattended via
Windows Task Scheduler.

**Schedule** (set via a Frequency dropdown in the job editor, not raw text):
Daily and Weekly show a time picker (Weekly also shows day checkboxes);
Hourly, Every N hours/minutes, and Once are also available for less
common cases. Internally this still maps to a compact `schedule_expr`
string (see `task_scheduler.py`) - e.g. `daily@02:00` or
`weekly:MON,WED,FRI@03:30` - so existing jobs created before this UI
existed still load correctly (the dropdown/checkboxes/time picker just
populate themselves from whatever's stored). Pick "No schedule" for a job
you only ever run manually.

**How the Task Scheduler integration works** (`task_scheduler.py` +
`run_scheduled_job.py`):
- Saving an enabled job with a schedule registers (or updates) a real
  Windows scheduled task under `\TFSync\<job_id>`, whose action is
  `run_scheduled_job.exe --job-id <job_id>`. That runner reads the job
  fresh from the database, runs the sync, logs it to run history, chains
  the ACL comparison if configured, and applies retention - the same
  logic "Run Now" uses, just without the GUI.
- Disabling a job, clearing its schedule, or deleting it removes the
  corresponding scheduled task.
- **Sync Schedules Now** reconciles the database against what's actually
  registered: it (re)creates tasks for every enabled+scheduled job,
  removes tasks for disabled/unscheduled jobs, and cleans up any orphaned
  `\TFSync\*` task whose job no longer exists - useful after manual
  changes in Task Scheduler itself, or after moving the install.
- The **Next Run** column reflects Task Scheduler's own "Next Run Time"
  for that task (via `schtasks /Query`), or explains why there isn't one
  (not scheduled, not yet registered, or "N/A" off Windows).
- **Run As** (in the job editor, under the Schedule section) controls
  which Windows identity the task runs under - see below.

**Run As: interactive-only vs. whether logged on or not.** Each job picks one:
- **While I'm logged on** (default, no password needed) - registers with
  `schtasks /RU <you> /IT`. The job only fires while you're logged on
  interactively; it will **not** run across a full logoff or a reboot with
  nobody logged in. This is the same behavior as before and is the right
  choice if TFSync (or at least your desktop session) is always running
  when the schedule fires.
- **Whether logged on or not** - registers with `schtasks /RU <account>
  /RP <password>`, which lets Windows run the task even fully logged off.
  You provide an account name (defaults to your current one, but can be a
  service account) and its password right in the job editor. **The
  password is never stored anywhere by TFSync** - it's passed straight to
  `schtasks.exe` for that one registration call and then discarded, so
  you'll need to re-enter it any time the task needs to be (re)registered:
  after editing the job, after "Sync Schedules Now" (which prompts for it,
  once per distinct account, only for jobs that need it), or after
  importing a job that had this mode set (exports include the account name
  but never a password, for the same reason). If registration ever fails
  (e.g. a bad account name), the error dialog shows the `schtasks` command
  that was attempted for troubleshooting - with the password always masked
  as `********`, never shown in clear text.
- This also happens to be the standard fix for `robocopy` failing under
  Task Scheduler with `ERROR 1326 - The user name or password is
  incorrect` when accessing a share that relies on cached network
  credentials (e.g. Credential Manager) rather than domain SSO: an
  interactive-only task doesn't inherit those, since it isn't really your
  logged-in session. Switching that job to "whether logged on or not"
  gives it a real logon with its own credentials instead. (The other
  standard fix, without changing anything in TFSync, is
  `cmdkey /add:<server> /user:<account> /pass:<password>` under the same
  Windows account the task runs as - either works.)

A few other notes on how the queue behaves:

- **Max concurrent jobs** (default 2) is an actual cap - "Run Now" is
  blocked once that many jobs are running at once, to avoid an unbounded
  pile of simultaneous robocopy processes. (This caps concurrent manual/
  Run Now executions in the GUI; scheduled runs via Task Scheduler run in
  their own separate process outside this cap today.)
- **Thread warning threshold** (default 64) is a soft warning, not a cap -
  the "Active jobs" banner turns orange and explains why, but won't stop
  you from proceeding, since the queue length itself is intentionally
  unbounded (per the design brief).
- Jobs with **auto-verify ACL** enabled kick off an ACL comparison after a
  successful (non-dry-run, non-cancelled) run and write its report to
  `job_reports/`; the summary counts get attached to that run's history
  entry once the comparison finishes.
- **Deleting a job decommissions it**: it removes its scheduled task (if
  any), deletes the job definition, and **deletes all of that job's run
  history** from the database. The confirmation dialog tells you how many
  history rows will go with it. Lifetime usage totals used for the
  licensing quota are kept regardless, since those bytes were actually
  transferred whether or not the job/its logs still exist.

**Export Jobs / Import Jobs**: back up your job definitions to a JSON
file, move them to another machine, or bulk-create several similar jobs by
hand-editing the exported file before importing it back.
- **Export Jobs...** writes every job's portable settings (name, source,
  dest, mode, schedule, threads, retries, auto-verify ACL, enabled) to a
  JSON file you choose. Run history, job IDs, and Task Scheduler
  registration state aren't part of the export - only the definitions.
- **Import Jobs...** reads a JSON file and adds each entry as a **new**
  job (existing jobs are never overwritten, so importing the same file
  twice creates duplicates - delete the old ones first if that's not what
  you want). Each entry is validated before anything is created: missing
  name/source/dest, an invalid mode, or an unparseable schedule expression
  gets that one entry skipped, listed by name in the confirmation dialog,
  without blocking the rest of the file from importing. Imported jobs that
  are enabled and scheduled get their Task Scheduler task registered
  automatically, the same as creating them by hand.

## Run History (GUI tab 4)

Every sync run - manual (from tab 1) or from the Job Queue - is logged to
a local SQLite database (`tfsync_store.py`), including start/end time,
duration, mode, dry-run flag, dirs/files copied/skipped/failed/extras,
bytes copied, derived MB/s and seconds-per-GB, exit code + description,
and any chained ACL comparison summary. Filter by job (or "All runs") and
hit Refresh to pull the latest. Failed runs are highlighted in red.

**Viewing a run's log or ACL report**: every run (manual, "Run Now", or
scheduled) writes its own robocopy log file - each run gets a unique log
named after its own run ID (under `job_logs/` for Job Queue runs, or
wherever you point "Log file" on the Sync tab for manual ones), so one
run's log never overwrites another's. Select a row and use:
- **View Log** - opens that run's robocopy log with your system's default
  text viewer.
- **View ACL Report** - opens that run's chained ACL comparison CSV, if
  one was run (job auto-verify, or accepting the "run comparison now?"
  prompt after a manual sync).

Both tell you plainly if there's nothing to open (no log path recorded,
the file's gone, or no ACL comparison was chained to that particular run).
Runs recorded by an older build of TFSync, before this tracking existed,
won't have a log/report path stored - there's nothing to recover for
those, but every run going forward does.

**Manual deletion**: alongside automatic retention (below), you can also
remove history by hand:
- **Delete Selected** - deletes just the row(s) you've selected in the
  table (Ctrl-click or Shift-click to select more than one).
- **Delete All (shown)** - deletes everything matching the current Job
  filter above it: pick a specific job to wipe just that job's history,
  or "All runs" to clear the entire run history table.

Both ask for confirmation first and, like retention pruning, never touch
the lifetime usage ledger used for the licensing quota.

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
| `run_scheduled_job.py` (Task Scheduler runner) | ✅ Full functionality | ❌ Exits with a clear error |
| `task_scheduler.py` (scheduling integration) | ✅ Registers/queries real tasks | ⚠️ Schedule *parsing/validation* works everywhere; actually registering a task requires `schtasks.exe` (Windows only) |
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
tfsync_gui.py                          PyQt5 graphical interface (sync + compare + job queue + history)
robocopy_sync.py                       Shared robocopy wrapper engine (used by CLI and GUI)
sync_shares.py                         Command-line interface (sync)
decode_mask.py                         Standalone access-mask decoder (cross-platform)
tfsync_store.py                        SQLite job/run-history store + LicenseManager/UsageTracker stubs (cross-platform, no Windows deps)
task_scheduler.py                      Schedule expression parsing (cross-platform) + schtasks.exe wrapper (Windows only)
run_scheduled_job.py                   Headless entry point Task Scheduler invokes to actually run one job
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

**Schedule a Resync**: the Sync tab has a "Schedule a Resync..." button
that opens the Job Queue's job editor pre-filled with the exact
source/dest/mode/threads/retries currently set on the Sync tab (with a
sensible Daily @ 02:00 starting schedule you're free to change) - a
one-click way to turn a manual sync you just ran (or are about to run)
into a recurring scheduled job, without re-typing anything into the Job
Queue tab. See "Job Queue" above for what happens once it's saved.

## Building executables locally (Windows)

```
pip install -r requirements.txt
pip install pyinstaller
build.bat
```
Produces `dist\compare_acls.exe`, `dist\tfsync_gui.exe`, `dist\sync_shares.exe`,
`dist\run_scheduled_job.exe`, and `dist\decode_mask.exe`. The GUI build
defaults to `--console` so startup errors are visible on first run; switch
to `--windowed` in `build.bat` once you've confirmed it runs cleanly.
`run_scheduled_job.exe` is invoked by Task Scheduler, not meant to be run
by hand - see the Job Queue section above.

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
- **Windows**: `compare_acls.exe`, `tfsync_gui.exe`, `sync_shares.exe`,
  `run_scheduled_job.exe`, `decode_mask.exe`, and `TFSync_Setup.exe` (the
  installer, versioned from the tag)
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

In the GUI, once a comparison finishes, **View CSV** opens the report file
directly in your system's default CSV application (e.g. Excel), and
**Open Report Folder** opens the containing folder instead - both use the
report path currently shown in "Output CSV".

## Known limitations

- Scheduled tasks default to running under your current Windows account
  with an interactive-only token (`/RU <you> /IT`, no password stored) -
  they fire while you're logged on, but **not** across a full logoff or a
  reboot with nobody logged in. Per-job, you can switch to "whether logged
  on or not" mode instead (`/RU <account> /RP <password>`), which does run
  logged-off/rebooted - see "Run As" in the Job Queue section above. That
  mode's password is entered per-job and never stored, so it needs
  re-entering whenever the task is (re)registered.
- Same-location detection (`paths_are_same`) is a normalized string
  comparison; it won't catch two different paths that happen to resolve to
  the same underlying share (e.g. a mapped drive letter vs. its UNC path, or
  two DFS namespace paths pointing at one target).
- Access-mask decoding covers the standard NTFS file/folder rights; fully
  custom or non-standard bit combinations fall back to a plain list of the
  set bits rather than a named preset.
