#!/usr/bin/env python3
"""
SMB/CIFS NTFS Permission Comparison Tool (GUI)
================================================

PyQt5 front-end for acl_compare_core.py. Lets you pick a source and
destination UNC path, run the comparison in the background, and browse
the resulting differences in a filterable, sortable table.

REQUIREMENTS
    - Must run on Windows (uses the Win32 security APIs via pywin32).
    - pip install pywin32 PyQt5

USAGE
    python compare_acls_gui.py
"""

import csv
import os
import sys

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSortFilterProxyModel, QDir
from PyQt5.QtGui import QStandardItemModel, QStandardItem, QColor, QBrush, QPalette
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QPushButton, QCheckBox, QLabel, QProgressBar, QPlainTextEdit,
    QTableView, QFileDialog, QMessageBox, QGroupBox, QComboBox, QHeaderView,
    QSplitter, QStyleFactory, QSpinBox, QTabWidget, QRadioButton, QButtonGroup,
)

import acl_compare_core as core
import robocopy_sync

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

        self._build_ui()
        self._apply_theme()

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
        controls.addWidget(self.preview_btn)
        controls.addWidget(self.run_sync_btn)
        controls.addWidget(self.cancel_sync_btn)
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

    # -------------------------------------------------------- Compare tab --
    def _build_compare_tab(self) -> QWidget:
        tab = QWidget()
        root_layout = QVBoxLayout(tab)

        # --- Input group -------------------------------------------------
        input_box = QGroupBox("Comparison Options")
        form = QFormLayout()

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

        controls.addWidget(self.run_btn)
        controls.addWidget(self.cancel_btn)
        controls.addStretch()
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
