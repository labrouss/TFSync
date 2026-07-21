#!/usr/bin/env python3
"""
TFSync (Total File Sync) — GUI

PyQt5 front-end combining robocopy-based sync, ACL comparison
(acl_compare_core.py), and a local job queue / run history backed by
tfsync_store.py. Lets you pick a source and destination UNC path, run a
sync and/or comparison in the background, browse results in a
filterable/sortable table, and manage recurring job definitions.

REQUIREMENTS
    - Must run on Windows (uses the Win32 security APIs via pywin32).
    - pip install pywin32 PyQt5

USAGE
    python tfsync_gui.py
"""

import csv
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSortFilterProxyModel, QDir, QTime, QDate, QDateTime
from PyQt5.QtGui import QStandardItemModel, QStandardItem, QColor, QBrush, QPalette
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QPushButton, QCheckBox, QLabel, QProgressBar, QPlainTextEdit,
    QTableView, QFileDialog, QMessageBox, QGroupBox, QComboBox, QHeaderView,
    QSplitter, QStyleFactory, QSpinBox, QTabWidget, QRadioButton, QButtonGroup,
    QDialog, QDialogButtonBox, QAbstractItemView, QTimeEdit, QDateTimeEdit, QStackedWidget, QInputDialog,
)

import acl_compare_core as core
import robocopy_sync
import tfsync_store as store
import task_scheduler

APP_TITLE = "TFSync — Total File Sync"


ACCENT = "#4fa3ff"
ACCENT_DIM = "#3373bd"


def build_dark_palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(30, 30, 30))
    palette.setColor(QPalette.WindowText, QColor(224, 224, 224))
    palette.setColor(QPalette.Base, QColor(37, 37, 38))
    palette.setColor(QPalette.AlternateBase, QColor(43, 43, 45))
    palette.setColor(QPalette.ToolTipBase, QColor(224, 224, 224))
    palette.setColor(QPalette.ToolTipText, QColor(224, 224, 224))
    palette.setColor(QPalette.Text, QColor(224, 224, 224))
    palette.setColor(QPalette.Button, QColor(45, 45, 48))
    palette.setColor(QPalette.ButtonText, QColor(224, 224, 224))
    palette.setColor(QPalette.BrightText, Qt.red)
    palette.setColor(QPalette.Link, QColor(ACCENT))
    palette.setColor(QPalette.Highlight, QColor(ACCENT_DIM))
    palette.setColor(QPalette.HighlightedText, Qt.white)
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(110, 110, 110))
    palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor(110, 110, 110))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(110, 110, 110))
    return palette


# QSS layered on top of the palette for things QPalette can't do:
# rounded corners, hover states, focus rings, custom header/scrollbar styling.
def build_light_palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(245, 245, 245))
    palette.setColor(QPalette.WindowText, QColor(30, 30, 30))
    palette.setColor(QPalette.Base, QColor(255, 255, 255))
    palette.setColor(QPalette.AlternateBase, QColor(247, 247, 247))
    palette.setColor(QPalette.ToolTipBase, QColor(30, 30, 30))
    palette.setColor(QPalette.ToolTipText, QColor(30, 30, 30))
    palette.setColor(QPalette.Text, QColor(30, 30, 30))
    palette.setColor(QPalette.Button, QColor(242, 242, 242))
    palette.setColor(QPalette.ButtonText, QColor(30, 30, 30))
    palette.setColor(QPalette.BrightText, Qt.red)
    palette.setColor(QPalette.Link, QColor(ACCENT_DIM))
    palette.setColor(QPalette.Highlight, QColor(ACCENT_DIM))
    palette.setColor(QPalette.HighlightedText, Qt.white)
    return palette


DARK_QSS = f"""
QWidget {{ font-size: 10pt; }}
QGroupBox {{
    border: 1px solid #3c3c3c; border-radius: 8px;
    margin-top: 14px; padding-top: 12px; font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {ACCENT};
}}
QLineEdit, QSpinBox, QComboBox {{
    background-color: #2d2d30; border: 1px solid #3c3c3c;
    border-radius: 6px; padding: 5px 8px;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QPushButton {{
    background-color: #2d2d30; border: 1px solid #3c3c3c;
    border-radius: 6px; padding: 6px 16px; font-weight: 600;
}}
QPushButton:hover {{ background-color: #383838; border-color: {ACCENT}; }}
QPushButton:pressed {{ background-color: {ACCENT}; color: #1e1e1e; }}
QPushButton:disabled {{ color: #6a6a6a; border-color: #333; }}
QTableView {{
    background-color: #252526; alternate-background-color: #2b2b2d;
    gridline-color: #3c3c3c; border: 1px solid #3c3c3c; border-radius: 6px;
}}
QHeaderView::section {{
    background-color: #2d2d30; color: #e0e0e0; padding: 6px;
    border: none; border-bottom: 2px solid {ACCENT}; font-weight: 600;
}}
QProgressBar {{
    background-color: #2d2d30; border: 1px solid #3c3c3c;
    border-radius: 6px; text-align: center; padding: 1px;
}}
QProgressBar::chunk {{ background-color: {ACCENT}; border-radius: 5px; }}
QPlainTextEdit {{
    background-color: #1a1a1a; border: 1px solid #3c3c3c;
    border-radius: 6px; font-family: Consolas, "Courier New", monospace;
}}
QCheckBox::indicator {{
    width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid #3c3c3c; background: #2d2d30;
}}
QCheckBox::indicator:checked {{ background-color: {ACCENT}; border-color: {ACCENT}; }}
QScrollBar:vertical {{ background: #1e1e1e; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #454545; border-radius: 6px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar:horizontal {{ background: #1e1e1e; height: 12px; }}
QScrollBar::handle:horizontal {{ background: #454545; border-radius: 6px; min-width: 24px; }}
"""

LIGHT_QSS = f"""
QWidget {{ font-size: 10pt; }}
QGroupBox {{
    border: 1px solid #d0d0d0; border-radius: 8px;
    margin-top: 14px; padding-top: 12px; font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {ACCENT_DIM};
}}
QLineEdit, QSpinBox, QComboBox {{
    background-color: #ffffff; border: 1px solid #c8c8c8;
    border-radius: 6px; padding: 5px 8px;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 1px solid {ACCENT_DIM}; }}
QPushButton {{
    background-color: #f2f2f2; border: 1px solid #c8c8c8;
    border-radius: 6px; padding: 6px 16px; font-weight: 600;
}}
QPushButton:hover {{ background-color: #e6e6e6; border-color: {ACCENT_DIM}; }}
QPushButton:pressed {{ background-color: {ACCENT_DIM}; color: white; }}
QPushButton:disabled {{ color: #a0a0a0; }}
QTableView {{
    background-color: #ffffff; alternate-background-color: #f7f7f7;
    gridline-color: #dcdcdc; border: 1px solid #c8c8c8; border-radius: 6px;
}}
QHeaderView::section {{
    background-color: #f2f2f2; color: #202020; padding: 6px;
    border: none; border-bottom: 2px solid {ACCENT_DIM}; font-weight: 600;
}}
QProgressBar {{
    background-color: #f2f2f2; border: 1px solid #c8c8c8;
    border-radius: 6px; text-align: center; padding: 1px;
}}
QProgressBar::chunk {{ background-color: {ACCENT_DIM}; border-radius: 5px; }}
QPlainTextEdit {{
    background-color: #fbfbfb; border: 1px solid #c8c8c8;
    border-radius: 6px; font-family: Consolas, "Courier New", monospace;
}}
QCheckBox::indicator {{
    width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid #c8c8c8; background: #ffffff;
}}
QCheckBox::indicator:checked {{ background-color: {ACCENT_DIM}; border-color: {ACCENT_DIM}; }}
"""

# Row background colors by difference type, for quick visual scanning.
ROW_COLORS = {
    "MISSING_IN_DEST": QColor("#f8d7da"),
    "EXTRA_IN_DEST": QColor("#f8d7da"),
    "OWNER_DIFF": QColor("#fff3cd"),
    "GROUP_DIFF": QColor("#fff3cd"),
    "ACE_MISSING_IN_DEST": QColor("#ffe5b4"),
    "ACE_ADDED_IN_DEST": QColor("#ffe5b4"),
    "READ_ERROR": QColor("#e2e3e5"),
    "MATCH": QColor("#d4edda"),
}

DIFF_TYPES = [
    "All", "MISSING_IN_DEST", "EXTRA_IN_DEST", "OWNER_DIFF", "GROUP_DIFF",
    "ACE_MISSING_IN_DEST", "ACE_ADDED_IN_DEST", "READ_ERROR", "MATCH",
]

JOB_COLUMNS = ["Name", "Source", "Destination", "Mode", "Schedule", "Threads",
               "Auto-Verify ACL", "Enabled", "Run As", "Last Status", "Next Run"]

JOB_EXPORT_FIELDS = ["name", "source", "dest", "mode", "schedule_expr",
                      "threads", "retries", "auto_verify_acl", "enabled", "run_as_user"]

HISTORY_COLUMNS = ["Start Time", "Job", "Source", "Destination", "Mode", "Dry Run",
                    "Duration", "Files Copied", "Bytes Copied", "MB/s", "Exit",
                    "Description", "ACL Chained"]


class ScanWorker(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    row_found = pyqtSignal(dict)
    finished_ok = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, source: str, dest: str, dirs_only: bool, resolve_host: str = None,
                 max_workers: int = 16, include_matches: bool = False):
        super().__init__()
        self.source = source
        self.dest = dest
        self.dirs_only = dirs_only
        self.resolve_host = resolve_host or None
        self.max_workers = max_workers
        self.include_matches = include_matches
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            counts = core.compare_trees(
                self.source, self.dest,
                dirs_only=self.dirs_only,
                resolve_host=self.resolve_host,
                max_workers=self.max_workers,
                include_matches=self.include_matches,
                log_cb=self.log.emit,
                progress_cb=self.progress.emit,
                row_cb=self.row_found.emit,
                should_cancel=lambda: self._cancel,
            )
            self.finished_ok.emit(counts)
        except Exception as e:
            self.failed.emit(str(e))


class SyncWorker(QThread):
    log = pyqtSignal(str)
    finished_ok = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, source: str, dest: str, mirror: bool, dry_run: bool,
                 threads: int, retries: int, wait_seconds: int,
                 preserve_permissions: bool, log_path: str):
        super().__init__()
        self.source = source
        self.dest = dest
        self.mirror = mirror
        self.dry_run = dry_run
        self.threads = threads
        self.retries = retries
        self.wait_seconds = wait_seconds
        self.preserve_permissions = preserve_permissions
        self.log_path = log_path
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            result = robocopy_sync.run_robocopy(
                self.source, self.dest,
                mirror=self.mirror, dry_run=self.dry_run,
                threads=self.threads, retries=self.retries, wait_seconds=self.wait_seconds,
                preserve_permissions=self.preserve_permissions,
                log_path=self.log_path,
                line_cb=self.log.emit,
                should_cancel=lambda: self._cancel,
            )
            self.finished_ok.emit(result)
        except Exception as e:
            self.failed.emit(str(e))


class JobEditorDialog(QDialog):
    """
    Create/edit a job definition. Schedule expression is free-text for now
    ("daily@02:00", "weekly:Sat@03:30", etc.) - the Task Scheduler
    integration layer that actually parses this and registers a real
    scheduled task is a separate piece of work, not yet built. Saving a
    job here only stores the definition; it does not (yet) register
    anything with Windows Task Scheduler.
    """

    def __init__(self, parent=None, job: dict = None, window_title: str = None):
        super().__init__(parent)
        self.job = job
        self._pending_schedule_expr: Optional[str] = None
        self.setWindowTitle(window_title or ("Edit Job" if job else "New Job"))
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit(job["name"] if job else "")
        self.name_edit.setPlaceholderText("e.g. Nightly finance share sync")
        form.addRow("Name:", self.name_edit)

        self.source_edit = QLineEdit(job["source"] if job else "")
        self.source_edit.setPlaceholderText(r"\\srcserver\share\path")
        form.addRow("Source:", self._path_row(self.source_edit))

        self.dest_edit = QLineEdit(job["dest"] if job else "")
        self.dest_edit.setPlaceholderText(r"\\dstserver\share\path")
        form.addRow("Destination:", self._path_row(self.dest_edit))

        mode_row = QHBoxLayout()
        self.mode_copy_radio = QRadioButton("Copy-only")
        self.mode_mirror_radio = QRadioButton("Mirror")
        mode_group = QButtonGroup(self)
        mode_group.addButton(self.mode_copy_radio)
        mode_group.addButton(self.mode_mirror_radio)
        if job and job.get("mode") == "mirror":
            self.mode_mirror_radio.setChecked(True)
        else:
            self.mode_copy_radio.setChecked(True)
        mode_row.addWidget(self.mode_copy_radio)
        mode_row.addWidget(self.mode_mirror_radio)
        form.addRow("Mode:", mode_row)

        schedule_box = QGroupBox("Schedule")
        schedule_layout = QVBoxLayout()

        freq_row = QHBoxLayout()
        freq_row.addWidget(QLabel("Frequency:"))
        self.freq_combo = QComboBox()
        # (label, kind) - kind matches task_scheduler's decompose/build "kind" values,
        # except "none" which just means "no schedule".
        self._freq_kinds = [
            ("No schedule (run manually only)", "none"),
            ("Daily", "daily"),
            ("Weekly", "weekly"),
            ("Hourly", "hourly"),
            ("Every N hours", "every_hours"),
            ("Every N minutes", "every_minutes"),
            ("Once", "once"),
        ]
        for label, _kind in self._freq_kinds:
            self.freq_combo.addItem(label)
        self.freq_combo.currentIndexChanged.connect(self._on_frequency_changed)
        freq_row.addWidget(self.freq_combo, 1)
        schedule_layout.addLayout(freq_row)

        self.freq_stack = QStackedWidget()

        # Index 0: none - nothing to configure.
        self.freq_stack.addWidget(QWidget())

        # Index 1: daily - just a time.
        daily_page = QWidget()
        daily_layout = QHBoxLayout(daily_page)
        daily_layout.setContentsMargins(0, 0, 0, 0)
        daily_layout.addWidget(QLabel("At:"))
        self.daily_time_edit = QTimeEdit(QTime(2, 0))
        self.daily_time_edit.setDisplayFormat("HH:mm")
        daily_layout.addWidget(self.daily_time_edit)
        daily_layout.addStretch()
        self.freq_stack.addWidget(daily_page)

        # Index 2: weekly - day checkboxes + time.
        weekly_page = QWidget()
        weekly_layout = QVBoxLayout(weekly_page)
        weekly_layout.setContentsMargins(0, 0, 0, 0)
        days_row = QHBoxLayout()
        days_row.addWidget(QLabel("On:"))
        self.day_checks = {}
        for day in ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]:
            chk = QCheckBox(day.capitalize())
            self.day_checks[day] = chk
            days_row.addWidget(chk)
        days_row.addStretch()
        weekly_layout.addLayout(days_row)
        weekly_time_row = QHBoxLayout()
        weekly_time_row.addWidget(QLabel("At:"))
        self.weekly_time_edit = QTimeEdit(QTime(3, 0))
        self.weekly_time_edit.setDisplayFormat("HH:mm")
        weekly_time_row.addWidget(self.weekly_time_edit)
        weekly_time_row.addStretch()
        weekly_layout.addLayout(weekly_time_row)
        self.freq_stack.addWidget(weekly_page)

        # Index 3: hourly - nothing to configure.
        self.freq_stack.addWidget(QWidget())

        # Index 4: every N hours.
        every_h_page = QWidget()
        every_h_layout = QHBoxLayout(every_h_page)
        every_h_layout.setContentsMargins(0, 0, 0, 0)
        every_h_layout.addWidget(QLabel("Every"))
        self.every_hours_spin = QSpinBox()
        self.every_hours_spin.setRange(1, 168)
        self.every_hours_spin.setValue(6)
        every_h_layout.addWidget(self.every_hours_spin)
        every_h_layout.addWidget(QLabel("hour(s)"))
        every_h_layout.addStretch()
        self.freq_stack.addWidget(every_h_page)

        # Index 5: every N minutes.
        every_m_page = QWidget()
        every_m_layout = QHBoxLayout(every_m_page)
        every_m_layout.setContentsMargins(0, 0, 0, 0)
        every_m_layout.addWidget(QLabel("Every"))
        self.every_minutes_spin = QSpinBox()
        self.every_minutes_spin.setRange(1, 1440)
        self.every_minutes_spin.setValue(15)
        every_m_layout.addWidget(self.every_minutes_spin)
        every_m_layout.addWidget(QLabel("minute(s)"))
        every_m_layout.addStretch()
        self.freq_stack.addWidget(every_m_page)

        # Index 6: once - date + time.
        once_page = QWidget()
        once_layout = QHBoxLayout(once_page)
        once_layout.setContentsMargins(0, 0, 0, 0)
        once_layout.addWidget(QLabel("At:"))
        self.once_datetime_edit = QDateTimeEdit(QDateTime(QDate.currentDate().addDays(1), QTime(10, 0)))
        self.once_datetime_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.once_datetime_edit.setCalendarPopup(True)
        once_layout.addWidget(self.once_datetime_edit)
        once_layout.addStretch()
        self.freq_stack.addWidget(once_page)

        schedule_layout.addWidget(self.freq_stack)
        schedule_box.setLayout(schedule_layout)
        form.addRow(schedule_box)

        self._load_schedule_into_ui(job["schedule_expr"] if job else None)

        existing_run_as = (job.get("run_as_user") if job else None) or None

        run_as_box = QGroupBox("Run As (Task Scheduler)")
        run_as_layout = QVBoxLayout()
        run_as_mode_row = QHBoxLayout()
        self.runas_interactive_radio = QRadioButton("While I'm logged on (no password needed)")
        self.runas_stored_radio = QRadioButton("Whether logged on or not (needs a Windows password)")
        runas_group = QButtonGroup(self)
        runas_group.addButton(self.runas_interactive_radio)
        runas_group.addButton(self.runas_stored_radio)
        run_as_mode_row.addWidget(self.runas_interactive_radio)
        run_as_mode_row.addWidget(self.runas_stored_radio)
        run_as_layout.addLayout(run_as_mode_row)

        self.runas_stack = QStackedWidget()
        self.runas_stack.addWidget(QWidget())  # index 0: interactive - nothing to configure

        creds_page = QWidget()
        creds_form = QFormLayout(creds_page)
        self.runas_user_edit = QLineEdit(existing_run_as or task_scheduler._current_user())
        self.runas_user_edit.setPlaceholderText(r"DOMAIN\username or username@domain.com")
        self.runas_user_edit.setToolTip(
            "The Windows account this task logs on as - NOT the file server's address.\n"
            r"e.g. HPEHELLAS-DEMO\svc_account or svc_account@hpehellas-demo.com" + "\n\n"
            "It should already have access to the source/destination shares, the same "
            "way your own account does when you run a sync manually."
        )
        creds_form.addRow("Windows account:", self.runas_user_edit)
        account_hint = QLabel(
            "This is the Windows login the task runs as (e.g. a domain account), "
            "not the file server's hostname - it needs its own access to the shares."
        )
        account_hint.setWordWrap(True)
        account_hint.setStyleSheet("color: #999; font-style: italic;")
        creds_form.addRow(account_hint)
        self.runas_password_edit = QLineEdit()
        self.runas_password_edit.setEchoMode(QLineEdit.Password)
        self.runas_password_edit.setPlaceholderText(
            "Re-enter to (re)register - never stored, only sent to Task Scheduler"
            if existing_run_as else "Required to register this schedule"
        )
        creds_form.addRow("Password:", self.runas_password_edit)
        creds_note = QLabel(
            "Not saved anywhere by TFSync - only passed to schtasks.exe when this job is "
            "(re)registered, then discarded. You'll need to re-enter it whenever the "
            "schedule needs to be (re)registered, including via \u201cSync Schedules Now\u201d."
        )
        creds_note.setWordWrap(True)
        creds_note.setStyleSheet("color: #999; font-style: italic;")
        creds_form.addRow(creds_note)
        self.runas_stack.addWidget(creds_page)
        run_as_layout.addWidget(self.runas_stack)
        run_as_box.setLayout(run_as_layout)
        form.addRow(run_as_box)

        self.runas_interactive_radio.toggled.connect(
            lambda checked: self.runas_stack.setCurrentIndex(0) if checked else None
        )
        self.runas_stored_radio.toggled.connect(
            lambda checked: self.runas_stack.setCurrentIndex(1) if checked else None
        )
        if existing_run_as:
            self.runas_stored_radio.setChecked(True)
            self.runas_stack.setCurrentIndex(1)
        else:
            self.runas_interactive_radio.setChecked(True)
            self.runas_stack.setCurrentIndex(0)

        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 128)
        self.threads_spin.setValue(job["threads"] if job else 16)
        form.addRow("Threads (/MT):", self.threads_spin)

        self.retries_spin = QSpinBox()
        self.retries_spin.setRange(0, 100)
        self.retries_spin.setValue(job["retries"] if job else 3)
        form.addRow("Retries:", self.retries_spin)

        self.auto_verify_chk = QCheckBox("Automatically run ACL comparison after each successful run")
        self.auto_verify_chk.setChecked(bool(job["auto_verify_acl"]) if job else False)
        form.addRow("", self.auto_verify_chk)

        self.enabled_chk = QCheckBox("Enabled")
        self.enabled_chk.setChecked(bool(job["enabled"]) if job else True)
        form.addRow("", self.enabled_chk)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _path_row(self, edit: QLineEdit) -> QWidget:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(edit)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(lambda: self._browse_dir(edit))
        row_layout.addWidget(browse_btn)
        return row

    def _browse_dir(self, edit: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select folder", edit.text())
        if path:
            edit.setText(QDir.toNativeSeparators(path))

    def _on_frequency_changed(self, index: int) -> None:
        self.freq_stack.setCurrentIndex(index)

    def _load_schedule_into_ui(self, schedule_expr: Optional[str]) -> None:
        """Populates the Frequency dropdown + relevant page from an existing
        job's schedule_expr (e.g. when opening the edit dialog)."""
        kind = "none"
        info: dict = {}
        if schedule_expr:
            try:
                info = task_scheduler.decompose_schedule_expr(schedule_expr)
                kind = info["kind"]
            except task_scheduler.ScheduleParseError:
                kind = "none"  # unrecognized/legacy expression - fall back to "no schedule" rather than crash

        index = next((i for i, (_label, k) in enumerate(self._freq_kinds) if k == kind), 0)
        self.freq_combo.setCurrentIndex(index)
        self.freq_stack.setCurrentIndex(index)

        if kind == "daily":
            self.daily_time_edit.setTime(QTime(info["hour"], info["minute"]))
        elif kind == "weekly":
            for day, chk in self.day_checks.items():
                chk.setChecked(day in info["days"])
            self.weekly_time_edit.setTime(QTime(info["hour"], info["minute"]))
        elif kind == "every_hours":
            self.every_hours_spin.setValue(info["n"])
        elif kind == "every_minutes":
            self.every_minutes_spin.setValue(info["n"])
        elif kind == "once":
            y, m, d = (int(part) for part in info["date"].split("-"))
            self.once_datetime_edit.setDateTime(QDateTime(QDate(y, m, d), QTime(info["hour"], info["minute"])))

    def _build_schedule_expr(self) -> Optional[str]:
        """Builds the canonical schedule_expr string from the current widget
        state, or None for 'no schedule'. Raises ScheduleParseError if the
        current selection is incomplete (e.g. weekly with no days checked)."""
        kind = self._freq_kinds[self.freq_combo.currentIndex()][1]
        if kind == "none":
            return None
        if kind == "daily":
            t = self.daily_time_edit.time()
            return task_scheduler.build_schedule_expr("daily", hour=t.hour(), minute=t.minute())
        if kind == "weekly":
            days = [day for day, chk in self.day_checks.items() if chk.isChecked()]
            t = self.weekly_time_edit.time()
            return task_scheduler.build_schedule_expr("weekly", days=days, hour=t.hour(), minute=t.minute())
        if kind == "hourly":
            return task_scheduler.build_schedule_expr("hourly")
        if kind == "every_hours":
            return task_scheduler.build_schedule_expr("every_hours", n=self.every_hours_spin.value())
        if kind == "every_minutes":
            return task_scheduler.build_schedule_expr("every_minutes", n=self.every_minutes_spin.value())
        if kind == "once":
            dt = self.once_datetime_edit.dateTime()
            return task_scheduler.build_schedule_expr(
                "once", date=dt.date().toString("yyyy-MM-dd"), hour=dt.time().hour(), minute=dt.time().minute()
            )
        return None

    def _on_accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Job Queue", "Please provide a job name.")
            return
        if not self.source_edit.text().strip() or not self.dest_edit.text().strip():
            QMessageBox.warning(self, "Job Queue", "Please provide both a source and destination path.")
            return
        if self.runas_stored_radio.isChecked() and not self.runas_user_edit.text().strip():
            QMessageBox.warning(self, "Job Queue", "Please provide an account name for \u201cWhether logged on or not\u201d mode.")
            return
        if self.runas_stored_radio.isChecked():
            account = self.runas_user_edit.text().strip()
            server_hosts = {
                core.parse_unc_host(self.source_edit.text()).lstrip("\\").lower(),
                core.parse_unc_host(self.dest_edit.text()).lstrip("\\").lower(),
            }
            server_hosts.discard("")
            if account.lower().lstrip("\\") in server_hosts:
                reply = QMessageBox.warning(
                    self, "Job Queue",
                    f"\u201c{account}\u201d looks like a file server address, not a Windows account.\n\n"
                    f"The account field needs a login identity (e.g. DOMAIN\\username or "
                    f"username@domain.com) that has access to the shares - not the server "
                    f"you're syncing to/from. Task Scheduler will reject this with a "
                    f"\u201cNo mapping between account names and security IDs\u201d error.\n\n"
                    f"Save anyway?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return
        try:
            self._pending_schedule_expr = self._build_schedule_expr()
        except task_scheduler.ScheduleParseError as e:
            QMessageBox.warning(self, "Job Queue", str(e))
            return
        self.accept()

    def get_entered_password(self) -> Optional[str]:
        """Returns the password typed for stored-credential mode, or None
        (interactive mode never needs one, and this is never persisted to
        the job definition itself - callers must use it immediately to
        register the scheduled task, then discard it)."""
        if self.runas_stored_radio.isChecked():
            return self.runas_password_edit.text() or None
        return None

    def values(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "source": self.source_edit.text().strip(),
            "dest": self.dest_edit.text().strip(),
            "mode": "mirror" if self.mode_mirror_radio.isChecked() else "copy",
            "schedule_expr": self._pending_schedule_expr,
            "threads": self.threads_spin.value(),
            "retries": self.retries_spin.value(),
            "auto_verify_acl": self.auto_verify_chk.isChecked(),
            "enabled": self.enabled_chk.isChecked(),
            "run_as_user": self.runas_user_edit.text().strip() if self.runas_stored_radio.isChecked() else None,
        }


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1150, 800)
        self.worker: ScanWorker = None
        self.sync_worker: SyncWorker = None
        self.last_output_path: str = ""
        self.dark_mode = True
        self._light_palette = build_light_palette()
        self._dark_palette = build_dark_palette()
        self._resolve_host_manual = False
        self._output_manual = False
        self._sync_log_manual = False

        store.init_db()
        self._sync_source = ""
        self._sync_dest = ""
        self._sync_mode = "copy"
        self._sync_start_time = None
        self._sync_log_path = None
        self._last_manual_run_id = None
        self._pending_acl_chain_run_id = None
        self._active_job_runs: dict = {}   # job_id -> {"worker": SyncWorker, "start_time": datetime, "run_id": str or None}
        self._job_acl_workers: list = []   # keep references so headless ACL-verify threads aren't garbage collected
        self.max_concurrent_jobs = 2
        self.thread_warning_threshold = 64

        self._build_ui()
        self._apply_theme()
        self.refresh_jobs_table()
        self.refresh_history_table()

    # ---------------------------------------------------------------- UI --
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)

        # --- Top bar: title + theme toggle (persists across tabs) --------
        top_bar = QHBoxLayout()
        title_label = QLabel(APP_TITLE)
        title_label.setStyleSheet("font-size: 13pt; font-weight: 700;")
        self.theme_btn = QPushButton("🌙 Dark Mode")
        self.theme_btn.clicked.connect(self.toggle_theme)
        top_bar.addWidget(title_label)
        top_bar.addStretch()
        top_bar.addWidget(self.theme_btn)
        root_layout.addLayout(top_bar)

        # --- Shared Paths group (used by both Sync and Compare tabs) -----
        paths_box = QGroupBox("Paths")
        paths_form = QFormLayout()

        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText(r"\\srcserver\share\path")
        paths_form.addRow("Source:", self._path_row(self.source_edit))

        self.dest_edit = QLineEdit()
        self.dest_edit.setPlaceholderText(r"\\dstserver\share\path")
        paths_form.addRow("Destination:", self._path_row(self.dest_edit))

        paths_box.setLayout(paths_form)
        root_layout.addWidget(paths_box)

        # --- Tabs ----------------------------------------------------------
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_sync_tab(), "1. Sync (robocopy)")
        self.tabs.addTab(self._build_compare_tab(), "2. Compare ACLs")
        self.tabs.addTab(self._build_queue_tab(), "3. Job Queue")
        self.tabs.addTab(self._build_history_tab(), "4. Run History")
        root_layout.addWidget(self.tabs, 1)

        # Auto-fill resolve-host / output filename / sync log from the
        # source path, but only while the user hasn't customized them.
        self.resolve_host_edit.textEdited.connect(lambda: setattr(self, "_resolve_host_manual", True))
        self.output_edit.textEdited.connect(lambda: setattr(self, "_output_manual", True))
        self.sync_log_edit.textEdited.connect(lambda: setattr(self, "_sync_log_manual", True))
        self.source_edit.textChanged.connect(self._auto_fill_from_source)

    # ----------------------------------------------------------- Sync tab --
    def _build_sync_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        options_box = QGroupBox("Sync Options")
        form = QFormLayout()

        mode_row = QHBoxLayout()
        self.mode_copy_radio = QRadioButton("Copy-only (safe - never deletes anything in destination)")
        self.mode_mirror_radio = QRadioButton("Mirror (exact sync - DELETES extras in destination)")
        self.mode_copy_radio.setChecked(True)
        mode_group = QButtonGroup(tab)
        mode_group.addButton(self.mode_copy_radio)
        mode_group.addButton(self.mode_mirror_radio)
        mode_row.addWidget(self.mode_copy_radio)
        mode_row.addWidget(self.mode_mirror_radio)
        form.addRow("Mode:", mode_row)

        self.mirror_confirm_chk = QCheckBox(
            "I understand Mirror mode will permanently delete files/folders in the "
            "destination that don't exist in the source"
        )
        self.mirror_confirm_chk.setEnabled(False)
        form.addRow("", self.mirror_confirm_chk)
        self.mode_mirror_radio.toggled.connect(self._on_sync_mode_changed)
        self.mirror_confirm_chk.toggled.connect(self._update_sync_run_enabled)

        self.preserve_perms_chk = QCheckBox("Preserve permissions, owner, and timestamps (robocopy /COPY:DATSO)")
        self.preserve_perms_chk.setChecked(True)
        form.addRow("", self.preserve_perms_chk)

        self.sync_threads_spin = QSpinBox()
        self.sync_threads_spin.setRange(1, 128)
        self.sync_threads_spin.setValue(16)
        self.sync_threads_spin.setToolTip("Robocopy /MT thread count")
        form.addRow("Threads:", self.sync_threads_spin)

        retry_row = QHBoxLayout()
        self.sync_retries_spin = QSpinBox()
        self.sync_retries_spin.setRange(0, 100)
        self.sync_retries_spin.setValue(3)
        self.sync_wait_spin = QSpinBox()
        self.sync_wait_spin.setRange(0, 3600)
        self.sync_wait_spin.setValue(5)
        retry_row.addWidget(QLabel("Retries:"))
        retry_row.addWidget(self.sync_retries_spin)
        retry_row.addWidget(QLabel("Wait (sec):"))
        retry_row.addWidget(self.sync_wait_spin)
        retry_row.addStretch()
        form.addRow("On error:", retry_row)

        self.sync_log_edit = QLineEdit(os.path.join(os.getcwd(), "robocopy_sync.log"))
        form.addRow("Log file:", self._path_row(self.sync_log_edit, is_save=True, filter_str="Log files (*.log)"))

        options_box.setLayout(form)
        layout.addWidget(options_box)

        controls = QHBoxLayout()
        self.preview_btn = QPushButton("Preview (Dry Run)")
        self.preview_btn.clicked.connect(lambda: self.start_sync(dry_run=True))
        self.run_sync_btn = QPushButton("Run Sync")
        self.run_sync_btn.clicked.connect(lambda: self.start_sync(dry_run=False))
        self.cancel_sync_btn = QPushButton("Cancel")
        self.cancel_sync_btn.setEnabled(False)
        self.cancel_sync_btn.clicked.connect(self.cancel_sync)
        self.schedule_resync_btn = QPushButton("Schedule a Resync...")
        self.schedule_resync_btn.setToolTip(
            "Create a Job Queue entry with these exact source/dest/mode/threads/retries, "
            "so this sync can repeat on a schedule."
        )
        self.schedule_resync_btn.clicked.connect(self.on_schedule_resync)
        controls.addWidget(self.preview_btn)
        controls.addWidget(self.run_sync_btn)
        controls.addWidget(self.cancel_sync_btn)
        controls.addWidget(self.schedule_resync_btn)
        controls.addStretch()
        layout.addLayout(controls)

        self.sync_progress_bar = QProgressBar()
        self.sync_progress_bar.setFormat("Idle")
        layout.addWidget(self.sync_progress_bar)

        self.sync_summary_label = QLabel("No sync run yet.")
        self.sync_summary_label.setStyleSheet("font-weight: 600; padding: 4px;")
        layout.addWidget(self.sync_summary_label)

        layout.addWidget(QLabel("Log:"))
        self.sync_log_view = QPlainTextEdit()
        self.sync_log_view.setReadOnly(True)
        self.sync_log_view.setMaximumBlockCount(10000)
        layout.addWidget(self.sync_log_view, 1)

        return tab

    def _on_sync_mode_changed(self, mirror_checked: bool) -> None:
        self.mirror_confirm_chk.setEnabled(mirror_checked)
        if not mirror_checked:
            self.mirror_confirm_chk.setChecked(False)
        self._update_sync_run_enabled()

    def _update_sync_run_enabled(self) -> None:
        if self.mode_mirror_radio.isChecked():
            self.run_sync_btn.setEnabled(self.mirror_confirm_chk.isChecked())
        else:
            self.run_sync_btn.setEnabled(True)

    def on_schedule_resync(self) -> None:
        source = self.source_edit.text().strip()
        dest = self.dest_edit.text().strip()
        if not source or not dest:
            QMessageBox.information(self, APP_TITLE, "Enter a source and destination first.")
            return

        folder_name = os.path.basename(source.rstrip("\\/")) or source
        seed = {
            "name": f"Resync: {folder_name}",
            "source": source,
            "dest": dest,
            "mode": "mirror" if self.mode_mirror_radio.isChecked() else "copy",
            "threads": self.sync_threads_spin.value(),
            "retries": self.sync_retries_spin.value(),
            "auto_verify_acl": False,
            "enabled": True,
            "schedule_expr": "daily@02:00",  # sensible starting point - the whole point of this button is to add one
        }
        dialog = JobEditorDialog(self, job=seed, window_title="Schedule a Resync")
        if dialog.exec_() == QDialog.Accepted:
            job_id = store.create_job(**dialog.values())
            self.refresh_jobs_table()
            self._sync_job_schedule(job_id)
            self.tabs.setCurrentIndex(2)  # Job Queue tab

    # -------------------------------------------------------- Compare tab --
    def _build_compare_tab(self) -> QWidget:
        tab = QWidget()
        root_layout = QVBoxLayout(tab)

        # --- Input group -------------------------------------------------
        input_box = QGroupBox("Comparison Options")
        form = QFormLayout()

        job_row = QHBoxLayout()
        self.compare_job_combo = QComboBox()
        self.compare_job_combo.addItem("\u2014 Select a job from the queue \u2014", None)
        self.compare_job_combo.currentIndexChanged.connect(self._on_compare_job_combo_changed)
        job_row.addWidget(self.compare_job_combo, 1)
        form.addRow("Load from Job Queue:", job_row)

        self.output_edit = QLineEdit(os.path.join(os.getcwd(), "acl_comparison_report.csv"))
        output_row = self._path_row(self.output_edit, is_save=True, filter_str="CSV files (*.csv)")
        form.addRow("Output CSV:", output_row)

        self.resolve_host_edit = QLineEdit()
        self.resolve_host_edit.setPlaceholderText(
            "Optional - e.g. a server name, for SIDs that only resolve on that host "
            "(auto-filled from the source server once you enter a source path)"
        )
        form.addRow("Resolve unknown SIDs against:", self.resolve_host_edit)

        self.dirs_only_chk = QCheckBox("Folders only (skip individual files, faster)")
        form.addRow("", self.dirs_only_chk)

        self.show_matches_chk = QCheckBox("Include matching items in report (shown as MATCH rows)")
        form.addRow("", self.show_matches_chk)

        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 128)
        self.threads_spin.setValue(16)
        self.threads_spin.setToolTip(
            "Parallel threads for reading ACLs. Higher speeds up large scans "
            "but can overload the file server or your connection - 16-32 is "
            "usually a good starting point (similar to robocopy's /MT)."
        )
        form.addRow("Threads:", self.threads_spin)

        input_box.setLayout(form)
        root_layout.addWidget(input_box)

        # --- Controls ------------------------------------------------------
        controls = QHBoxLayout()
        self.run_btn = QPushButton("Run Comparison")
        self.run_btn.clicked.connect(self.start_scan)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_scan)
        self.open_report_btn = QPushButton("Open Report Folder")
        self.open_report_btn.setEnabled(False)
        self.open_report_btn.clicked.connect(self.open_report_folder)
        self.view_csv_btn = QPushButton("View CSV")
        self.view_csv_btn.setToolTip("Open the report file itself with your system's default CSV viewer (e.g. Excel)")
        self.view_csv_btn.setEnabled(False)
        self.view_csv_btn.clicked.connect(self.view_report_csv)

        controls.addWidget(self.run_btn)
        controls.addWidget(self.cancel_btn)
        controls.addStretch()
        controls.addWidget(self.view_csv_btn)
        controls.addWidget(self.open_report_btn)
        root_layout.addLayout(controls)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFormat("Idle")
        root_layout.addWidget(self.progress_bar)

        # --- Summary labels --------------------------------------------
        self.summary_label = QLabel("No scan run yet.")
        self.summary_label.setStyleSheet("font-weight: 600; padding: 4px;")
        root_layout.addWidget(self.summary_label)

        # --- Splitter: results table + log ------------------------------
        splitter = QSplitter(Qt.Vertical)

        results_widget = QWidget()
        results_layout = QVBoxLayout(results_widget)
        results_layout.setContentsMargins(0, 0, 0, 0)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Type to filter by path, trustee, detail...")
        self.filter_edit.textChanged.connect(self.apply_filter)
        filter_row.addWidget(self.filter_edit)

        filter_row.addWidget(QLabel("Difference type:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(DIFF_TYPES)
        self.type_combo.currentTextChanged.connect(self.apply_filter)
        filter_row.addWidget(self.type_combo)
        results_layout.addLayout(filter_row)

        self.model = QStandardItemModel(0, len(core.ROW_FIELDS))
        self.model.setHorizontalHeaderLabels(core.ROW_FIELDS)
        self.proxy = QSortFilterProxyModel()
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)  # search across all columns

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableView.NoEditTriggers)
        results_layout.addWidget(self.table)

        splitter.addWidget(results_widget)

        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.addWidget(QLabel("Log:"))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        log_layout.addWidget(self.log_view)
        splitter.addWidget(log_widget)

        splitter.setSizes([500, 150])
        root_layout.addWidget(splitter)

        return tab

    # ------------------------------------------------------------ Queue tab --
    def _build_queue_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        note = QLabel(
            "Job definitions are stored locally (SQLite). Enabled jobs with a Schedule are "
            "registered as real Windows Task Scheduler tasks (under \\TFSync\\) that invoke "
            "run_scheduled_job.exe unattended - you don't need the GUI open for them to run. "
            "Current limitation: registered tasks run under your current Windows account using "
            "an interactive-only token, so they fire while you're logged on, but not across a "
            "full logoff or a reboot with nobody logged in."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #999; font-style: italic;")
        layout.addWidget(note)

        settings_row = QHBoxLayout()
        settings_row.addWidget(QLabel("Max concurrent jobs:"))
        self.max_concurrent_spin = QSpinBox()
        self.max_concurrent_spin.setRange(1, 20)
        self.max_concurrent_spin.setValue(self.max_concurrent_jobs)
        self.max_concurrent_spin.valueChanged.connect(self._on_max_concurrent_changed)
        settings_row.addWidget(self.max_concurrent_spin)
        settings_row.addSpacing(20)
        settings_row.addWidget(QLabel("Thread warning threshold:"))
        self.thread_warning_spin = QSpinBox()
        self.thread_warning_spin.setRange(1, 1000)
        self.thread_warning_spin.setValue(self.thread_warning_threshold)
        self.thread_warning_spin.valueChanged.connect(self._on_thread_warning_changed)
        settings_row.addWidget(self.thread_warning_spin)
        settings_row.addStretch()
        layout.addLayout(settings_row)

        self.active_jobs_label = QLabel()
        self.active_jobs_label.setStyleSheet("font-weight: 600; padding: 4px;")
        layout.addWidget(self.active_jobs_label)
        self._update_active_jobs_label()

        self.jobs_model = QStandardItemModel(0, len(JOB_COLUMNS))
        self.jobs_model.setHorizontalHeaderLabels(JOB_COLUMNS)
        self.jobs_table = QTableView()
        self.jobs_table.setModel(self.jobs_model)
        self.jobs_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.jobs_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.jobs_table.setEditTriggers(QTableView.NoEditTriggers)
        self.jobs_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.jobs_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.jobs_table.horizontalHeader().setSectionResizeMode(9, QHeaderView.Stretch)
        layout.addWidget(self.jobs_table, 1)

        controls = QHBoxLayout()
        new_btn = QPushButton("New Job...")
        new_btn.clicked.connect(self.on_new_job)
        edit_btn = QPushButton("Edit Job...")
        edit_btn.clicked.connect(self.on_edit_job)
        clone_btn = QPushButton("Clone Job...")
        clone_btn.setToolTip("Open a New Job dialog pre-filled with this job's settings")
        clone_btn.clicked.connect(self.on_clone_job)
        delete_btn = QPushButton("Delete Job")
        delete_btn.clicked.connect(self.on_delete_job)
        run_btn = QPushButton("Run Now")
        run_btn.clicked.connect(self.on_run_job_now)
        compare_btn = QPushButton("Compare...")
        compare_btn.setToolTip("Open the Compare ACLs tab with this job's source/dest/threads filled in")
        compare_btn.clicked.connect(self.on_compare_job)
        sync_schedules_btn = QPushButton("Sync Schedules Now")
        sync_schedules_btn.setToolTip(
            "Reconciles Task Scheduler with the jobs database: registers/updates enabled "
            "scheduled jobs, removes tasks for disabled/unscheduled jobs, and cleans up orphans."
        )
        sync_schedules_btn.clicked.connect(self.on_sync_schedules_now)
        export_jobs_btn = QPushButton("Export Jobs...")
        export_jobs_btn.setToolTip("Save all job definitions to a JSON file")
        export_jobs_btn.clicked.connect(self.on_export_jobs)
        import_jobs_btn = QPushButton("Import Jobs...")
        import_jobs_btn.setToolTip("Load job definitions from a JSON file (adds them as new jobs)")
        import_jobs_btn.clicked.connect(self.on_import_jobs)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_jobs_table)
        controls.addWidget(new_btn)
        controls.addWidget(edit_btn)
        controls.addWidget(clone_btn)
        controls.addWidget(delete_btn)
        controls.addWidget(run_btn)
        controls.addWidget(compare_btn)
        controls.addWidget(sync_schedules_btn)
        controls.addWidget(export_jobs_btn)
        controls.addWidget(import_jobs_btn)
        controls.addStretch()
        controls.addWidget(refresh_btn)
        layout.addLayout(controls)

        return tab

    def _on_max_concurrent_changed(self, value: int) -> None:
        self.max_concurrent_jobs = value
        self._update_active_jobs_label()

    def _on_thread_warning_changed(self, value: int) -> None:
        self.thread_warning_threshold = value
        self._update_active_jobs_label()

    def _update_active_jobs_label(self) -> None:
        active_count = len(self._active_job_runs)
        combined_threads = sum(entry["threads"] for entry in self._active_job_runs.values())
        text = (f"Active jobs: {active_count} / {self.max_concurrent_jobs}  |  "
                f"Combined robocopy threads: {combined_threads}")
        if combined_threads > self.thread_warning_threshold:
            text += f"  \u26a0 over warning threshold of {self.thread_warning_threshold} - risk of saturating the link"
            self.active_jobs_label.setStyleSheet(
                "font-weight: 600; padding: 4px; background-color: #7a4a00; color: white; border-radius: 4px;"
            )
        else:
            self.active_jobs_label.setStyleSheet("font-weight: 600; padding: 4px;")
        self.active_jobs_label.setText(text)

    def refresh_jobs_table(self) -> None:
        self.jobs_model.removeRows(0, self.jobs_model.rowCount())
        for job in store.list_jobs():
            next_run = self._next_run_display(job)
            run_as_display = job["run_as_user"] if job.get("run_as_user") else "Interactive (you)"
            row = [
                QStandardItem(job["name"]),
                QStandardItem(job["source"]),
                QStandardItem(job["dest"]),
                QStandardItem(job["mode"]),
                QStandardItem(job["schedule_expr"] or ""),
                QStandardItem(str(job["threads"])),
                QStandardItem("Yes" if job["auto_verify_acl"] else "No"),
                QStandardItem("Yes" if job["enabled"] else "No"),
                QStandardItem(run_as_display),
                QStandardItem(job["last_run_status"] or "Never run"),
                QStandardItem(next_run),
            ]
            row[0].setData(job["job_id"], Qt.UserRole)
            self.jobs_model.appendRow(row)
        self._refresh_compare_job_combo()

    def _next_run_display(self, job: dict) -> str:
        if os.name != "nt":
            return "N/A (Windows only)"
        if not (job["enabled"] and job["schedule_expr"]):
            return "Not scheduled"
        info = task_scheduler.query_task(job["job_id"])
        if not info:
            return "Not registered - use \u201cSync Schedules Now\u201d"
        return info.get("Next Run Time", "Unknown")

    def _refresh_compare_job_combo(self) -> None:
        if not hasattr(self, "compare_job_combo"):
            return
        current_id = self.compare_job_combo.currentData()
        self.compare_job_combo.blockSignals(True)
        self.compare_job_combo.clear()
        self.compare_job_combo.addItem("\u2014 Select a job from the queue \u2014", None)
        for job in store.list_jobs():
            self.compare_job_combo.addItem(job["name"], job["job_id"])
        idx = self.compare_job_combo.findData(current_id)
        self.compare_job_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.compare_job_combo.blockSignals(False)

    def _on_compare_job_combo_changed(self, _index: int) -> None:
        job_id = self.compare_job_combo.currentData()
        if not job_id:
            return
        job = store.get_job(job_id)
        if not job:
            return
        self.source_edit.setText(job["source"])
        self.dest_edit.setText(job["dest"])
        self.threads_spin.setValue(job["threads"])

    def _selected_job_id(self) -> str:
        indexes = self.jobs_table.selectionModel().selectedRows() if self.jobs_table.selectionModel() else []
        if not indexes:
            return None
        return self.jobs_model.item(indexes[0].row(), 0).data(Qt.UserRole)

    def on_new_job(self) -> None:
        dialog = JobEditorDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            values = dialog.values()
            password = dialog.get_entered_password()
            job_id = store.create_job(**values)
            self.refresh_jobs_table()
            self._sync_job_schedule(job_id, password=password)

    def on_edit_job(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            QMessageBox.information(self, APP_TITLE, "Select a job to edit first.")
            return
        job = store.get_job(job_id)
        dialog = JobEditorDialog(self, job=job)
        if dialog.exec_() == QDialog.Accepted:
            password = dialog.get_entered_password()
            store.update_job(job_id, **dialog.values())
            self.refresh_jobs_table()
            self._sync_job_schedule(job_id, password=password)

    def on_clone_job(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            QMessageBox.information(self, APP_TITLE, "Select a job to clone first.")
            return
        source_job = store.get_job(job_id)
        if not source_job:
            return
        clone_seed = dict(source_job)
        clone_seed["name"] = f"{source_job['name']} (Copy)"
        dialog = JobEditorDialog(self, job=clone_seed, window_title="Clone Job")
        if dialog.exec_() == QDialog.Accepted:
            password = dialog.get_entered_password()
            new_job_id = store.create_job(**dialog.values())
            self.refresh_jobs_table()
            self._sync_job_schedule(new_job_id, password=password)

    def _sync_job_schedule(self, job_id: str, password: Optional[str] = None) -> None:
        """
        Registers/updates or removes the job's Windows scheduled task so
        Task Scheduler matches the job definition just saved. A no-op on
        non-Windows (there's nothing to register against). `password` is
        only used immediately for this one registration call (for a
        "whether logged on or not" job) - it is never persisted.
        """
        if os.name != "nt":
            return
        job = store.get_job(job_id)
        if not job:
            return
        try:
            if job["enabled"] and job["schedule_expr"]:
                task_scheduler.register_task(job, password=password)
            else:
                task_scheduler.delete_task(job_id)
        except task_scheduler.MissingCredentialsError as e:
            QMessageBox.warning(
                self, APP_TITLE,
                f"Job saved, but its schedule needs a password to register:\n{e}\n\n"
                f"Edit the job again and enter the password, or use \u201cSync Schedules Now\u201d "
                f"which will prompt for it."
            )
        except (task_scheduler.TaskSchedulerError, task_scheduler.ScheduleParseError) as e:
            QMessageBox.warning(
                self, APP_TITLE,
                f"Job saved, but the scheduled task could not be updated:\n{e}\n\n"
                f"You can retry from \u201cSync Schedules Now\u201d."
            )
        self.refresh_jobs_table()

    def on_delete_job(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            QMessageBox.information(self, APP_TITLE, "Select a job to delete first.")
            return
        if job_id in self._active_job_runs:
            QMessageBox.warning(self, APP_TITLE, "This job is currently running - wait for it to finish before deleting it.")
            return
        job = store.get_job(job_id)
        history_count = store.count_run_history(job_id)
        reply = QMessageBox.question(
            self, APP_TITLE,
            f"Decommission \u201c{job['name']}\u201d?\n\n"
            f"This removes its scheduled task (if any), deletes the job definition, and "
            f"deletes all {history_count} of its run history record(s). This cannot be undone.\n\n"
            f"(Lifetime usage totals for licensing are unaffected either way.)",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            if os.name == "nt":
                try:
                    task_scheduler.delete_task(job_id)
                except task_scheduler.TaskSchedulerError:
                    pass  # best-effort - the job/history deletion below still proceeds
            store.delete_job(job_id)
            self.refresh_jobs_table()
            self.refresh_history_table()

    def on_compare_job(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            QMessageBox.information(self, APP_TITLE, "Select a job first.")
            return
        job = store.get_job(job_id)
        if not job:
            return
        self.source_edit.setText(job["source"])
        self.dest_edit.setText(job["dest"])
        self.threads_spin.setValue(job["threads"])
        self.tabs.setCurrentIndex(1)

    def on_sync_schedules_now(self) -> None:
        if os.name != "nt":
            QMessageBox.information(self, APP_TITLE, "Task Scheduler integration requires Windows.")
            return
        jobs = store.list_jobs()

        passwords = {}
        for user in task_scheduler.get_required_credential_users(jobs):
            pw, ok = QInputDialog.getText(
                self, APP_TITLE,
                f"Enter the Windows password for \u201c{user}\u201d to (re)register its scheduled "
                f"job(s) (runs whether logged on or not).\n\n"
                f"This is not saved anywhere - only passed to Task Scheduler for this operation.\n"
                f"Leave blank/Cancel to skip that account's job(s) for now.",
                QLineEdit.Password,
            )
            if ok and pw:
                passwords[user] = pw

        try:
            summary = task_scheduler.reconcile_all(jobs, passwords=passwords)
        except task_scheduler.TaskSchedulerError as e:
            QMessageBox.critical(self, APP_TITLE, f"Could not sync schedules:\n{e}")
            return
        self.refresh_jobs_table()
        msg = (f"Registered/updated: {summary['registered']}\n"
               f"Removed (disabled/unscheduled/orphaned): {summary['removed']}")
        if summary["errors"]:
            msg += "\n\nErrors:\n" + "\n".join(summary["errors"])
            QMessageBox.warning(self, APP_TITLE, msg)
        else:
            QMessageBox.information(self, APP_TITLE, msg)

    def on_export_jobs(self) -> None:
        jobs = store.list_jobs()
        if not jobs:
            QMessageBox.information(self, APP_TITLE, "No jobs to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Jobs", os.path.join(os.getcwd(), "tfsync_jobs_export.json"), "JSON files (*.json)"
        )
        if not path:
            return
        export_data = {
            "tfsync_export_version": 1,
            "jobs": [{field: job[field] for field in JOB_EXPORT_FIELDS} for job in jobs],
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=2)
        except OSError as e:
            QMessageBox.critical(self, APP_TITLE, f"Could not write file:\n{e}")
            return
        QMessageBox.information(self, APP_TITLE, f"Exported {len(jobs)} job(s) to:\n{path}")

    def _validate_import_job(self, entry: Any) -> dict:
        """Validates and normalizes one imported job entry. Raises ValueError
        (with a short, specific reason) if the entry can't be used."""
        if not isinstance(entry, dict):
            raise ValueError("not a valid job entry")
        name = str(entry.get("name", "")).strip()
        source = str(entry.get("source", "")).strip()
        dest = str(entry.get("dest", "")).strip()
        if not name:
            raise ValueError("missing name")
        if not source or not dest:
            raise ValueError("missing source or destination")
        mode = entry.get("mode", "copy")
        if mode not in ("copy", "mirror"):
            raise ValueError(f"invalid mode '{mode}'")
        schedule_expr = entry.get("schedule_expr") or None
        if schedule_expr:
            try:
                task_scheduler.parse_schedule_expr(schedule_expr)
            except task_scheduler.ScheduleParseError as e:
                raise ValueError(f"invalid schedule '{schedule_expr}': {str(e).splitlines()[0]}")
        try:
            threads = int(entry.get("threads", 16))
            retries = int(entry.get("retries", 3))
        except (TypeError, ValueError):
            raise ValueError("threads/retries must be numbers")
        run_as_user = str(entry.get("run_as_user") or "").strip() or None
        return {
            "name": name, "source": source, "dest": dest, "mode": mode,
            "schedule_expr": schedule_expr, "threads": threads, "retries": retries,
            "auto_verify_acl": bool(entry.get("auto_verify_acl", False)),
            "enabled": bool(entry.get("enabled", True)),
            "run_as_user": run_as_user,
        }

    def on_import_jobs(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import Jobs", os.getcwd(), "JSON files (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            QMessageBox.critical(self, APP_TITLE, f"Could not read file:\n{e}")
            return

        raw_jobs = data.get("jobs") if isinstance(data, dict) else data
        if not isinstance(raw_jobs, list):
            QMessageBox.critical(self, APP_TITLE, "This doesn't look like a TFSync jobs export file.")
            return

        valid, errors = [], []
        for i, entry in enumerate(raw_jobs):
            try:
                valid.append(self._validate_import_job(entry))
            except ValueError as e:
                label = entry.get("name", "?") if isinstance(entry, dict) else "?"
                errors.append(f"Entry {i + 1} (\u201c{label}\u201d): {e}")

        if not valid:
            QMessageBox.warning(self, APP_TITLE, "No valid jobs found in this file.\n\n" + "\n".join(errors))
            return

        needs_password = [job["name"] for job in valid if job.get("run_as_user")]
        msg = f"Import {len(valid)} job(s) as new entries?"
        if errors:
            msg += f"\n\n{len(errors)} entry/entries will be skipped:\n" + "\n".join(errors)
        if needs_password:
            msg += (f"\n\n{len(needs_password)} job(s) are set to run whether logged on or "
                    f"not and will need their password re-entered afterward (Edit Job, or "
                    f"\u201cSync Schedules Now\u201d) - passwords are never included in exports:\n"
                    + "\n".join(needs_password))
        reply = QMessageBox.question(self, APP_TITLE, msg, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        created_ids = [store.create_job(**job) for job in valid]
        self.refresh_jobs_table()
        for job_id in created_ids:
            self._sync_job_schedule(job_id)  # registers Task Scheduler tasks where applicable; no-op off Windows

        QMessageBox.information(self, APP_TITLE, f"Imported {len(created_ids)} job(s).")

    def on_run_job_now(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            QMessageBox.information(self, APP_TITLE, "Select a job to run first.")
            return
        if job_id in self._active_job_runs:
            QMessageBox.information(self, APP_TITLE, "This job is already running.")
            return
        if len(self._active_job_runs) >= self.max_concurrent_jobs:
            QMessageBox.warning(
                self, APP_TITLE,
                f"Max concurrent jobs ({self.max_concurrent_jobs}) reached.\n"
                f"Wait for a running job to finish, or raise the limit above the job table."
            )
            return

        job = store.get_job(job_id)
        if os.name != "nt":
            QMessageBox.critical(self, APP_TITLE, "This tool must be run on Windows (robocopy is a Windows-only tool).")
            return
        if core.paths_are_same(job["source"], job["dest"]):
            QMessageBox.warning(self, APP_TITLE, "Source and destination are the same path for this job.")
            return

        mirror = job["mode"] == "mirror"
        if mirror:
            reply = QMessageBox.warning(
                self, APP_TITLE,
                f"\u201c{job['name']}\u201d is a MIRROR job - it will permanently delete files/folders "
                f"in the destination that don't exist in the source.\n\nRun it now?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        log_dir = os.path.join(os.getcwd(), "job_logs")
        os.makedirs(log_dir, exist_ok=True)
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in job["name"])
        run_id = uuid.uuid4().hex
        log_path = os.path.join(log_dir, f"{safe_name}_{run_id[:8]}.log")

        worker = SyncWorker(
            job["source"], job["dest"], mirror, False,
            job["threads"], job["retries"], 5, True, log_path,
        )
        worker.finished_ok.connect(lambda result, jid=job_id: self._job_finished(jid, result))
        worker.failed.connect(lambda message, jid=job_id: self._job_failed(jid, message))

        self._active_job_runs[job_id] = {
            "worker": worker, "start_time": datetime.now(timezone.utc), "threads": job["threads"],
            "run_id": run_id, "log_path": log_path,
        }
        self._update_active_jobs_label()
        self.refresh_jobs_table()
        worker.start()

    def _job_finished(self, job_id: str, result: dict) -> None:
        entry = self._active_job_runs.pop(job_id, None)
        self._update_active_jobs_label()
        if not entry:
            return
        job = store.get_job(job_id)
        if not job:
            self.refresh_jobs_table()
            return
        end_time = datetime.now(timezone.utc)
        run_id = store.record_run(
            result, job["source"], job["dest"], job["mode"], dry_run=False,
            start_time=entry["start_time"], end_time=end_time, job_id=job_id,
            run_id=entry["run_id"], log_path=entry["log_path"],
        )
        self.refresh_jobs_table()
        self.refresh_history_table()

        if job["auto_verify_acl"] and not result["cancelled"] and not robocopy_sync.is_failure(result["exit_code"]):
            self._start_headless_acl_verify(job, run_id)

    def _job_failed(self, job_id: str, message: str) -> None:
        self._active_job_runs.pop(job_id, None)
        self._update_active_jobs_label()
        self.refresh_jobs_table()
        QMessageBox.critical(self, APP_TITLE, f"Job failed to start:\n{message}")

    def _start_headless_acl_verify(self, job: dict, run_id: str) -> None:
        """Runs an ACL comparison in the background (no table/log wired to the UI) and
        attaches its summary to the just-recorded run once it finishes."""
        report_dir = os.path.join(os.getcwd(), "job_reports")
        os.makedirs(report_dir, exist_ok=True)
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in job["name"])
        report_path = os.path.join(report_dir, f"{safe_name}_{run_id[:8]}_acl_report.csv")

        worker = ScanWorker(job["source"], job["dest"], False, None, job["threads"], False)
        csv_file = open(report_path, "w", newline="", encoding="utf-8-sig")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(core.ROW_FIELDS)

        def on_row(row: dict) -> None:
            csv_writer.writerow([row[field] for field in core.ROW_FIELDS])

        def on_done(counts: dict) -> None:
            csv_file.close()
            acl_summary = {k: v for k, v in counts.items() if k != "cancelled"}
            store.update_run_acl_summary(run_id, acl_summary, report_path=report_path)
            self.refresh_history_table()
            self._job_acl_workers[:] = [w for w in self._job_acl_workers if w is not worker]

        def on_fail(message: str) -> None:
            csv_file.close()
            self._job_acl_workers[:] = [w for w in self._job_acl_workers if w is not worker]

        worker.row_found.connect(on_row)
        worker.finished_ok.connect(on_done)
        worker.failed.connect(on_fail)
        self._job_acl_workers.append(worker)
        worker.start()

    # ---------------------------------------------------------- History tab --
    def _build_history_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        retention_box = QGroupBox("Retention")
        retention_row = QHBoxLayout()
        retention_row.addWidget(QLabel("Keep per job (and per manual runs):"))
        self.retention_combo = QComboBox()
        self.retention_combo.addItems(["Keep all runs", "Keep last run only", "Keep last N runs"])
        self.retention_combo.currentIndexChanged.connect(self._on_retention_combo_changed)
        retention_row.addWidget(self.retention_combo)

        self.retention_count_spin = QSpinBox()
        self.retention_count_spin.setRange(2, 100000)
        self.retention_count_spin.setValue(store.DEFAULT_RETENTION_COUNT)
        self.retention_count_spin.valueChanged.connect(self._on_retention_count_changed)
        retention_row.addWidget(self.retention_count_spin)

        retention_row.addStretch()
        apply_retention_btn = QPushButton("Apply Retention Now")
        apply_retention_btn.clicked.connect(self.on_apply_retention_now)
        retention_row.addWidget(apply_retention_btn)
        retention_box.setLayout(retention_row)
        layout.addWidget(retention_box)

        self._load_retention_policy_into_ui()

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Job:"))
        self.history_job_combo = QComboBox()
        self.history_job_combo.addItem("All runs", None)
        self.history_job_combo.currentIndexChanged.connect(self.refresh_history_table)
        filter_row.addWidget(self.history_job_combo)
        filter_row.addStretch()
        view_log_btn = QPushButton("View Log")
        view_log_btn.setToolTip("Open the selected run's robocopy log file with your system's default viewer")
        view_log_btn.clicked.connect(self.on_view_selected_log)
        filter_row.addWidget(view_log_btn)
        view_acl_btn = QPushButton("View ACL Report")
        view_acl_btn.setToolTip("Open the selected run's chained ACL comparison CSV, if it has one")
        view_acl_btn.clicked.connect(self.on_view_selected_acl_report)
        filter_row.addWidget(view_acl_btn)
        delete_selected_btn = QPushButton("Delete Selected")
        delete_selected_btn.setToolTip("Delete the selected run(s) from history (Ctrl/Shift-click to select multiple)")
        delete_selected_btn.clicked.connect(self.on_delete_selected_history)
        filter_row.addWidget(delete_selected_btn)
        delete_all_btn = QPushButton("Delete All (shown)")
        delete_all_btn.setToolTip("Delete all run history matching the current Job filter above")
        delete_all_btn.clicked.connect(self.on_delete_all_history)
        filter_row.addWidget(delete_all_btn)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_history_table)
        filter_row.addWidget(refresh_btn)
        layout.addLayout(filter_row)

        self.history_model = QStandardItemModel(0, len(HISTORY_COLUMNS))
        self.history_model.setHorizontalHeaderLabels(HISTORY_COLUMNS)
        self.history_table = QTableView()
        self.history_table.setModel(self.history_model)
        self.history_table.setSortingEnabled(True)
        self.history_table.setEditTriggers(QTableView.NoEditTriggers)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.history_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.history_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.history_table.horizontalHeader().setSectionResizeMode(11, QHeaderView.Stretch)
        layout.addWidget(self.history_table, 1)

        self.history_row_count_label = QLabel()
        self.history_row_count_label.setStyleSheet("color: #999; font-style: italic;")
        layout.addWidget(self.history_row_count_label)

        return tab

    def _load_retention_policy_into_ui(self) -> None:
        mode, count = store.get_retention_policy()
        self.retention_combo.blockSignals(True)
        self.retention_count_spin.blockSignals(True)
        if mode == store.RETENTION_MODE_ALL:
            self.retention_combo.setCurrentIndex(0)
        elif count == 1:
            self.retention_combo.setCurrentIndex(1)
        else:
            self.retention_combo.setCurrentIndex(2)
        self.retention_count_spin.setValue(max(count, 2))
        self.retention_count_spin.setEnabled(self.retention_combo.currentIndex() == 2)
        self.retention_combo.blockSignals(False)
        self.retention_count_spin.blockSignals(False)

    def _save_retention_policy_from_ui(self) -> None:
        idx = self.retention_combo.currentIndex()
        if idx == 0:
            store.set_retention_policy(store.RETENTION_MODE_ALL)
        elif idx == 1:
            store.set_retention_policy(store.RETENTION_MODE_COUNT, 1)
        else:
            store.set_retention_policy(store.RETENTION_MODE_COUNT, self.retention_count_spin.value())

    def _on_retention_combo_changed(self, idx: int) -> None:
        self.retention_count_spin.setEnabled(idx == 2)
        self._save_retention_policy_from_ui()

    def _on_retention_count_changed(self, _value: int) -> None:
        if self.retention_combo.currentIndex() == 2:
            self._save_retention_policy_from_ui()

    def on_apply_retention_now(self) -> None:
        deleted = store.apply_retention_policy()
        self.refresh_history_table()
        mode, count = store.get_retention_policy()
        if mode == store.RETENTION_MODE_ALL:
            QMessageBox.information(self, APP_TITLE, "Retention is set to \u201ckeep all runs\u201d - nothing was pruned.")
        else:
            QMessageBox.information(
                self, APP_TITLE,
                f"Applied retention (keep last {count} per job/manual bucket).\n"
                f"Deleted {deleted} old run history row(s). Lifetime usage totals are unaffected."
            )

    def refresh_history_table(self) -> None:
        # Repopulate the job filter combo without losing the current selection.
        current_job_id = self.history_job_combo.currentData() if hasattr(self, "history_job_combo") else None
        jobs_by_id = {job["job_id"]: job for job in store.list_jobs()}
        if hasattr(self, "history_job_combo"):
            self.history_job_combo.blockSignals(True)
            self.history_job_combo.clear()
            self.history_job_combo.addItem("All runs", None)
            for job in jobs_by_id.values():
                self.history_job_combo.addItem(job["name"], job["job_id"])
            idx = self.history_job_combo.findData(current_job_id)
            self.history_job_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.history_job_combo.blockSignals(False)
            selected_job_id = self.history_job_combo.currentData()
        else:
            selected_job_id = None

        self.history_model.removeRows(0, self.history_model.rowCount())
        for run in store.list_run_history(job_id=selected_job_id, limit=200):
            job_name = jobs_by_id.get(run["job_id"], {}).get("name", "Manual run") if run["job_id"] else "Manual run"
            duration = f"{run['duration_seconds']:.1f}s"
            mb_s = f"{run['throughput_mb_s']:.1f}" if run["throughput_mb_s"] else ""
            acl_col = "\u2014"
            if run["acl_chained"]:
                try:
                    summary = json.loads(run["acl_summary_json"]) if run["acl_summary_json"] else {}
                    acl_col = ", ".join(f"{k}:{v}" for k, v in summary.items() if v)
                    acl_col = acl_col or "no differences"
                except (ValueError, TypeError):
                    acl_col = "yes"
            row = [
                QStandardItem(run["start_time"]),
                QStandardItem(job_name),
                QStandardItem(run["source"]),
                QStandardItem(run["dest"]),
                QStandardItem(run["mode"]),
                QStandardItem("Yes" if run["dry_run"] else "No"),
                QStandardItem(duration),
                QStandardItem(str(run["files_copied"]) if run["files_copied"] is not None else ""),
                QStandardItem(str(run["bytes_copied"]) if run["bytes_copied"] is not None else ""),
                QStandardItem(mb_s),
                QStandardItem(str(run["exit_code"])),
                QStandardItem(run["description"]),
                QStandardItem(acl_col),
            ]
            row[0].setData(run["run_id"], Qt.UserRole)
            if run["is_failure"]:
                for item in row:
                    item.setBackground(QBrush(QColor("#f8d7da")))
                    item.setForeground(QBrush(QColor("#1a1a1a")))
            self.history_model.appendRow(row)

        total_rows = store.count_run_history()
        mode, count = store.get_retention_policy()
        policy_text = "keeping all runs" if mode == store.RETENTION_MODE_ALL else f"keeping last {count} per job/manual bucket"
        shown = self.history_model.rowCount()
        self.history_row_count_label.setText(
            f"Showing {shown} run(s){' (limited to 200)' if shown == 200 else ''} - "
            f"{total_rows} total in database - retention policy: {policy_text}."
        )

    def on_delete_selected_history(self) -> None:
        selection_model = self.history_table.selectionModel()
        if not selection_model or not selection_model.selectedRows():
            QMessageBox.information(self, APP_TITLE, "Select one or more rows in the table first "
                                                       "(Ctrl-click or Shift-click to select multiple).")
            return
        run_ids = [self.history_model.item(index.row(), 0).data(Qt.UserRole)
                   for index in selection_model.selectedRows()]
        run_ids = [rid for rid in run_ids if rid]
        reply = QMessageBox.question(
            self, APP_TITLE,
            f"Delete {len(run_ids)} selected run history record(s)? This cannot be undone.\n\n"
            f"(Lifetime usage totals for licensing are unaffected.)",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        deleted = sum(1 for rid in run_ids if store.delete_run(rid))
        self.refresh_history_table()
        QMessageBox.information(self, APP_TITLE, f"Deleted {deleted} run history record(s).")

    def on_delete_all_history(self) -> None:
        job_id = self.history_job_combo.currentData()
        job_name = self.history_job_combo.currentText()
        count = store.count_run_history(job_id)
        if count == 0:
            QMessageBox.information(self, APP_TITLE, "No run history to delete for the current filter.")
            return
        scope_desc = "ALL run history (every job and every manual run)" if not job_id else f"all run history for \u201c{job_name}\u201d"
        reply = QMessageBox.question(
            self, APP_TITLE,
            f"Delete {scope_desc}?\n\nThis deletes {count} record(s) and cannot be undone.\n\n"
            f"(Lifetime usage totals for licensing are unaffected.)",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        deleted = store.delete_all_run_history(job_id)
        self.refresh_history_table()
        QMessageBox.information(self, APP_TITLE, f"Deleted {deleted} run history record(s).")

    def _auto_fill_from_source(self, text: str) -> None:
        host = core.parse_unc_host(text)
        if host and not self._resolve_host_manual:
            self.resolve_host_edit.setText(host)

        last = core.parse_last_component(text)
        if last and not self._output_manual:
            directory = os.path.dirname(self.output_edit.text()) or os.getcwd()
            self.output_edit.setText(os.path.join(directory, f"{last}_acl_comparison_report.csv"))
        if last and not self._sync_log_manual:
            directory = os.path.dirname(self.sync_log_edit.text()) or os.getcwd()
            self.sync_log_edit.setText(os.path.join(directory, f"{last}_robocopy_sync.log"))

    def _path_row(self, edit: QLineEdit, is_save: bool = False, filter_str: str = "CSV files (*.csv)") -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit)
        browse_btn = QPushButton("Browse...")
        if is_save:
            browse_btn.clicked.connect(lambda: self._browse_save(edit, filter_str))
        else:
            browse_btn.clicked.connect(lambda: self._browse_dir(edit))
        layout.addWidget(browse_btn)
        return row

    def _browse_dir(self, edit: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select folder", edit.text())
        if path:
            edit.setText(QDir.toNativeSeparators(path))

    def _browse_save(self, edit: QLineEdit, filter_str: str = "CSV files (*.csv)") -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save as", edit.text(), filter_str)
        if path:
            edit.setText(QDir.toNativeSeparators(path))
            if edit is self.output_edit:
                self._output_manual = True
            elif edit is self.sync_log_edit:
                self._sync_log_manual = True

    # ------------------------------------------------------------- Sync --
    def start_sync(self, dry_run: bool) -> None:
        source = self.source_edit.text().strip()
        dest = self.dest_edit.text().strip()

        if not source or not dest:
            QMessageBox.warning(self, APP_TITLE, "Please provide both a source and destination path.")
            return
        if core.paths_are_same(source, dest):
            QMessageBox.warning(self, APP_TITLE, "Source and destination are the same path.\nRefusing to sync.")
            return
        if os.name != "nt":
            QMessageBox.critical(self, APP_TITLE, "This tool must be run on Windows (robocopy is a Windows-only tool).")
            return

        mirror = self.mode_mirror_radio.isChecked()

        if mirror and not dry_run:
            reply = QMessageBox.warning(
                self, APP_TITLE,
                "You are about to run a MIRROR sync.\n\n"
                "This will PERMANENTLY DELETE files and folders in the destination\n"
                "that do not exist in the source. This cannot be undone.\n\n"
                "Are you sure you want to continue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        log_path = self.sync_log_edit.text().strip() or None

        self._sync_source = source
        self._sync_dest = dest
        self._sync_mode = "mirror" if mirror else "copy"
        self._sync_start_time = datetime.now(timezone.utc)
        self._sync_log_path = log_path

        self.sync_log_view.clear()
        mode_label = "MIRROR" if mirror else "copy-only"
        run_label = "PREVIEW (dry run)" if dry_run else "LIVE RUN"
        self.sync_summary_label.setText(f"Running {mode_label} sync - {run_label}...")
        self.sync_progress_bar.setRange(0, 0)  # indeterminate/busy - robocopy doesn't give an upfront file count
        self.sync_progress_bar.setFormat(f"{run_label} in progress...")

        self.sync_worker = SyncWorker(
            source, dest, mirror, dry_run,
            self.sync_threads_spin.value(), self.sync_retries_spin.value(), self.sync_wait_spin.value(),
            self.preserve_perms_chk.isChecked(), log_path,
        )
        self.sync_worker.log.connect(self.append_sync_log)
        self.sync_worker.finished_ok.connect(lambda result: self.sync_finished(result, dry_run))
        self.sync_worker.failed.connect(self.sync_failed)

        self.preview_btn.setEnabled(False)
        self.run_sync_btn.setEnabled(False)
        self.cancel_sync_btn.setEnabled(True)
        self.sync_worker.start()

    def cancel_sync(self) -> None:
        if self.sync_worker:
            self.sync_worker.cancel()
            self.cancel_sync_btn.setEnabled(False)
            self.append_sync_log("Cancelling... (waiting for robocopy to stop)")

    def append_sync_log(self, msg: str) -> None:
        self.sync_log_view.appendPlainText(msg)

    def sync_finished(self, result: dict, dry_run: bool) -> None:
        self.preview_btn.setEnabled(True)
        self._update_sync_run_enabled()
        self.cancel_sync_btn.setEnabled(False)
        self.sync_progress_bar.setRange(0, 1)
        self.sync_progress_bar.setValue(1)

        status = "CANCELLED" if result["cancelled"] else ("PREVIEW COMPLETE" if dry_run else "COMPLETE")
        self.sync_progress_bar.setFormat(status)

        summary_bits = [f"[{status}] {result['description']}"]
        for section in ("Dirs", "Files", "Bytes"):
            if section in result["summary"]:
                c = result["summary"][section]
                summary_bits.append(
                    f"{section}: copied {c['copied']}, skipped {c['skipped']}, failed {c['failed']}"
                )
        summary_text = "  |  ".join(summary_bits)
        self.sync_summary_label.setText(summary_text)
        self.append_sync_log(f"\n=== Done ===\n{summary_text}\nExit code: {result['exit_code']}")

        self._last_manual_run_id = store.record_run(
            result, self._sync_source, self._sync_dest, self._sync_mode, dry_run=dry_run,
            start_time=self._sync_start_time, end_time=datetime.now(timezone.utc), job_id=None,
            log_path=self._sync_log_path,
        )
        self.refresh_history_table()

        if robocopy_sync.is_failure(result["exit_code"]) and not result["cancelled"]:
            QMessageBox.warning(
                self, APP_TITLE,
                f"Sync finished with errors:\n{result['description']}\n\nCheck the log for details."
            )

        # Only offer to chain into the ACL comparison for a real (non-preview,
        # non-cancelled) run - a dry run made no changes to verify.
        if not dry_run and not result["cancelled"]:
            reply = QMessageBox.question(
                self, APP_TITLE,
                "Sync finished. Run the ACL comparison now to verify permissions?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                self._pending_acl_chain_run_id = self._last_manual_run_id
                self.tabs.setCurrentIndex(1)
                self.start_scan()

    def sync_failed(self, message: str) -> None:
        self.preview_btn.setEnabled(True)
        self._update_sync_run_enabled()
        self.cancel_sync_btn.setEnabled(False)
        self.sync_progress_bar.setRange(0, 1)
        self.sync_progress_bar.setValue(0)
        self.sync_progress_bar.setFormat("Failed")
        self.sync_summary_label.setText("Sync failed - see log.")
        self.append_sync_log(f"ERROR: {message}")
        QMessageBox.critical(self, APP_TITLE, f"Sync failed to start:\n{message}\n\n"
                                               f"(Is robocopy available? It ships with Windows by default.)")

    # ------------------------------------------------------------- Scan --
    def start_scan(self) -> None:
        source = self.source_edit.text().strip()
        dest = self.dest_edit.text().strip()
        output = self.output_edit.text().strip()

        if not source or not dest:
            QMessageBox.warning(self, APP_TITLE, "Please provide both a source and destination path.")
            return
        if core.paths_are_same(source, dest):
            QMessageBox.warning(self, APP_TITLE, "Source and destination are the same path.\nNothing to compare.")
            return
        if not output:
            QMessageBox.warning(self, APP_TITLE, "Please provide an output CSV path.")
            return

        if not core.PYWIN32_AVAILABLE:
            QMessageBox.critical(self, APP_TITLE, "pywin32 is not installed.\nInstall it with:  pip install pywin32")
            return
        if os.name != "nt":
            QMessageBox.critical(self, APP_TITLE, "This tool must be run on Windows.")
            return

        core.enable_privilege("SeBackupPrivilege", log_cb=self.append_log)

        if not os.path.isdir(core.long_path(source)):
            QMessageBox.critical(self, APP_TITLE, f"Source path not accessible:\n{source}")
            return
        if not os.path.isdir(core.long_path(dest)):
            QMessageBox.critical(self, APP_TITLE, f"Destination path not accessible:\n{dest}")
            return

        # Reset UI state
        self.model.removeRows(0, self.model.rowCount())
        self.log_view.clear()
        self.summary_label.setText("Scanning...")
        self.progress_bar.setFormat("Scanning source and destination trees...")
        self.progress_bar.setValue(0)
        self.open_report_btn.setEnabled(False)
        self.view_csv_btn.setEnabled(False)
        self.last_output_path = output

        self._csv_file = open(output, "w", newline="", encoding="utf-8-sig")
        self._csv_writer = csv.writer(self._csv_file)
        self._csv_writer.writerow(core.ROW_FIELDS)

        resolve_host = self.resolve_host_edit.text().strip() or None
        self.worker = ScanWorker(
            source, dest, self.dirs_only_chk.isChecked(), resolve_host,
            self.threads_spin.value(), self.show_matches_chk.isChecked(),
        )
        self.worker.log.connect(self.append_log)
        self.worker.progress.connect(self.update_progress)
        self.worker.row_found.connect(self.add_row)
        self.worker.finished_ok.connect(self.scan_finished)
        self.worker.failed.connect(self.scan_failed)

        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.worker.start()

    def cancel_scan(self) -> None:
        if self.worker:
            self.worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.append_log("Cancelling... (finishing current item)")

    def append_log(self, msg: str) -> None:
        self.log_view.appendPlainText(msg)

    def update_progress(self, cur: int, total: int) -> None:
        if total <= 0:
            return
        self.progress_bar.setFormat(f"Comparing common items: {cur}/{total} (%p%)")
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(cur)

    def add_row(self, row: dict) -> None:
        # Write to CSV as it comes in
        self._csv_writer.writerow([row[field] for field in core.ROW_FIELDS])

        items = [QStandardItem(str(row[field])) for field in core.ROW_FIELDS]
        color = ROW_COLORS.get(row["DifferenceType"])
        if color:
            for item in items:
                item.setBackground(QBrush(color))
                # Force dark text regardless of app theme - these backgrounds
                # are always light pastel colors, so black text stays readable
                # whether the rest of the app is in light or dark mode.
                item.setForeground(QBrush(QColor("#1a1a1a")))
        self.model.appendRow(items)

    def scan_finished(self, counts: dict) -> None:
        self._csv_file.close()
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.open_report_btn.setEnabled(True)
        self.view_csv_btn.setEnabled(True)

        status = "CANCELLED" if counts.get("cancelled") else "COMPLETE"
        self.progress_bar.setFormat(f"{status} - {self.progress_bar.value()} items compared")

        match_label = "Matching" if self.show_matches_chk.isChecked() else "Matching (not shown in table)"
        summary = (
            f"[{status}]  "
            f"Missing in dest: {counts['missing']}  |  "
            f"Extra in dest: {counts['extra']}  |  "
            f"Owner diffs: {counts['owner_diff']}  |  "
            f"Group diffs: {counts['group_diff']}  |  "
            f"ACE diffs: {counts['ace_diff']}  |  "
            f"Errors: {counts['error']}  |  "
            f"{match_label}: {counts['match']}"
        )
        self.summary_label.setText(summary)
        self.append_log(f"\n=== Done ===\n{summary}\nReport saved to: {self.last_output_path}")

        if self._pending_acl_chain_run_id:
            acl_summary = {k: v for k, v in counts.items() if k != "cancelled"}
            store.update_run_acl_summary(self._pending_acl_chain_run_id, acl_summary, report_path=self.last_output_path)
            self._pending_acl_chain_run_id = None
            self.refresh_history_table()

    def scan_failed(self, message: str) -> None:
        try:
            self._csv_file.close()
        except Exception:
            pass
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setFormat("Failed")
        self.summary_label.setText("Scan failed - see log.")
        self.append_log(f"ERROR: {message}")
        QMessageBox.critical(self, APP_TITLE, f"Scan failed:\n{message}")

    def apply_filter(self) -> None:
        text = self.filter_edit.text()
        diff_type = self.type_combo.currentText()

        if diff_type == "All":
            self.proxy.setFilterFixedString(text)
            self.proxy.setFilterKeyColumn(-1)
        else:
            # Filter to rows matching the chosen DifferenceType column exactly,
            # then further narrow by the free-text filter across all columns.
            # QSortFilterProxyModel only supports one regex filter at a time,
            # so we combine both into a single regex against the whole row.
            import re
            pattern = re.escape(diff_type)
            if text:
                pattern = f"(?=.*{re.escape(diff_type)})(?=.*{re.escape(text)})"
            self.proxy.setFilterKeyColumn(-1)
            self.proxy.setFilterRegExp(pattern if text else re.escape(diff_type))

    def _apply_theme(self) -> None:
        app = QApplication.instance()
        app.setStyle(QStyleFactory.create("Fusion"))
        if self.dark_mode:
            app.setPalette(self._dark_palette)
            app.setStyleSheet(DARK_QSS)
            self.theme_btn.setText("☀ Light Mode")
        else:
            app.setPalette(self._light_palette)
            app.setStyleSheet(LIGHT_QSS)
            self.theme_btn.setText("🌙 Dark Mode")

    def toggle_theme(self) -> None:
        self.dark_mode = not self.dark_mode
        self._apply_theme()

    def open_report_folder(self) -> None:
        if not self.last_output_path:
            return
        folder = os.path.dirname(os.path.abspath(self.last_output_path)) or "."
        try:
            os.startfile(folder)
        except Exception as e:
            QMessageBox.warning(self, APP_TITLE, f"Could not open folder:\n{e}")

    def view_report_csv(self) -> None:
        self._open_with_system_default(self.last_output_path, "The report")

    def _open_with_system_default(self, path: Optional[str], what: str = "The file") -> None:
        """Opens a file with the system's default application for its type
        (e.g. Excel for .csv). Shared by the Compare tab's View CSV button
        and the Run History tab's View Log / View ACL Report buttons."""
        if not path:
            QMessageBox.information(self, APP_TITLE, f"{what} has no recorded file path.")
            return
        abspath = os.path.abspath(path)
        if not os.path.isfile(abspath):
            QMessageBox.warning(self, APP_TITLE, f"{what} was not found on disk:\n{abspath}")
            return
        try:
            if os.name == "nt":
                os.startfile(abspath)
            elif sys.platform == "darwin":
                subprocess.run(["open", abspath], check=True)
            else:
                subprocess.run(["xdg-open", abspath], check=True)
        except Exception as e:
            QMessageBox.warning(
                self, APP_TITLE,
                f"Could not open this with your system's default application:\n{e}\n\n"
                f"File location:\n{abspath}"
            )

    def on_view_selected_log(self) -> None:
        run_id = self._selected_history_run_id()
        if not run_id:
            QMessageBox.information(self, APP_TITLE, "Select a run in the table first.")
            return
        run = store.get_run(run_id)
        if not run:
            return
        self._open_with_system_default(run.get("log_path"), "This run's robocopy log")

    def on_view_selected_acl_report(self) -> None:
        run_id = self._selected_history_run_id()
        if not run_id:
            QMessageBox.information(self, APP_TITLE, "Select a run in the table first.")
            return
        run = store.get_run(run_id)
        if not run:
            return
        if not run.get("acl_chained"):
            QMessageBox.information(self, APP_TITLE, "This run didn't have an ACL comparison chained to it.")
            return
        self._open_with_system_default(run.get("acl_report_path"), "This run's ACL comparison report")

    def _selected_history_run_id(self) -> Optional[str]:
        selection_model = self.history_table.selectionModel()
        if not selection_model:
            return None
        rows = selection_model.selectedRows()
        if not rows:
            return None
        return self.history_model.item(rows[0].row(), 0).data(Qt.UserRole)

    def closeEvent(self, event) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(3000)
        if self.sync_worker and self.sync_worker.isRunning():
            self.sync_worker.cancel()
            self.sync_worker.wait(3000)
        event.accept()


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
