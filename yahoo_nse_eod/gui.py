"""
PySide6 GUI for the standalone Yahoo/NSE EOD project.
Replaces the old CustomTkinter implementation with a professional Qt-based dashboard.
"""

import sys
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QFrame, QScrollArea, QGroupBox,
    QLineEdit, QDateEdit, QGridLayout, QSpacerItem, QSizePolicy,
    QToolBar, QStatusBar, QCheckBox, QFileDialog
)
from PySide6.QtCore import Qt, QProcess, QTimer, QSize, QDate
from PySide6.QtGui import QFont, QIcon, QColor, QTextCursor
from qt_material import apply_stylesheet

from config import DB_FILE, FAILED_EOD_FILE
from logger import get_logger

log = get_logger(__name__)

BASE_DIR = Path(__file__).parent

TASKS = [
    ("Download Shares OS", "sync_share_counts.py", []),
    ("Build Adjusted Prices", "adjust_splits.py", []),
    ("Review Corporate Actions", "corporate_actions.py", []),
]

TOOLTIPS = {
    "Bootstrap Yahoo EOD": "Download full historical Yahoo EOD price data, share counts, and rebuild all indices.",
    "Download Shares OS": "Download historical shares outstanding from Yahoo Finance.",
    "Build Adjusted Prices": "Rebuild split-adjusted prices, market cap, and moving averages from stored raw data.",
    "Daily Price Refresh": "Append the latest available Yahoo EOD rows and update only the new dates.",
    "Review Corporate Actions": "Show stored split/dividend events and optionally rebuild affected symbols.",
    "Detect Symbol Changes": "Detect probable NSE ticker renames using NSE files and ISIN continuity.",
    "Retry Failed EOD Downloads": "Retry only the symbols listed in the latest failed-EOD report file.",
    "Run Screener": "Run the standalone Sharpe screener with current filters.",
}

class SectionCard(QGroupBox):
    def __init__(self, title, subtitle, parent=None):
        super().__init__(title, parent)
        self.setFont(QFont("Segoe UI", 9, QFont.Bold))
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(10, 2, 10, 5)
        self.layout.setSpacing(4)
        
        if subtitle:
            sub_label = QLabel(subtitle)
            sub_label.setStyleSheet("color: #94a3b8; font-size: 10px; font-weight: normal;")
            self.layout.addWidget(sub_label)

class YahooNSEGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Yahoo NSE EOD Dashboard")
        self.resize(1150, 800) # Slightly smaller default size
        self.process = None
        self.task_queue = []
        self.last_query_args = None

        self._init_ui()

        
        # Stats Timer
        self.stats_timer = QTimer(self)
        self.stats_timer.timeout.connect(self._refresh_stats)
        self.stats_timer.start(5000)
        
        QTimer.singleShot(100, self._refresh_stats)
        
    def _init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # --- SIDEBAR ---
        sidebar_scroll = QScrollArea()
        sidebar_scroll.setFixedWidth(280) # Narrower sidebar
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setStyleSheet("QScrollArea { border: none; background-color: #1e293b; }")
        
        sidebar_content = QWidget()
        sidebar_content.setStyleSheet("background-color: #1e293b;")
        self.sidebar_layout = QVBoxLayout(sidebar_content)
        self.sidebar_layout.setContentsMargins(5, 5, 5, 5)
        self.sidebar_layout.setSpacing(4)

        self._build_tasks_section()
        self._build_sharpe_section()
        self._build_query_section()
        self._build_snapshot_section()
        
        self.sidebar_layout.addStretch()
        sidebar_scroll.setWidget(sidebar_content)
        main_layout.addWidget(sidebar_scroll)

        # --- MAIN AREA ---
        content_layout = QVBoxLayout()
        main_layout.addLayout(content_layout)

        # Header Bar
        header = QFrame()
        header.setFixedHeight(70) # Compact header
        header.setStyleSheet("background-color: #0f172a; border-bottom: 1px solid #1e293b;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(15, 0, 20, 0) # Compact margins

        title_label = QLabel("Yahoo NSE EOD")
        title_label.setStyleSheet("color: #38bdf8; font-size: 20px; font-weight: bold;")
        header_layout.addWidget(title_label)

        header_layout.addSpacing(30)

        # Stats in header
        self.active_sym_label = QLabel("Active Symbols: -")
        self.active_sym_label.setStyleSheet("color: #94a3b8; font-size: 12px;")
        header_layout.addWidget(self.active_sym_label)

        header_layout.addSpacing(15)

        self.updated_upto_label = QLabel("Updated upto: -")
        self.updated_upto_label.setStyleSheet("color: #94a3b8; font-size: 12px;")
        header_layout.addWidget(self.updated_upto_label)

        header_layout.addStretch()

        self.clock_label = QLabel()
        self.clock_label.setStyleSheet("color: #64748b; font-family: Consolas; font-size: 13px;")
        header_layout.addWidget(self.clock_label)
        
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._update_clock)
        self.clock_timer.start(1000)
        self._update_clock()

        content_layout.addWidget(header)

        # Log Area
        log_panel = QWidget()
        log_panel.setStyleSheet("background-color: #0f172a;")
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(15, 10, 15, 15)

        log_header = QHBoxLayout()
        log_title = QLabel("Activity Log")
        log_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #e2e8f0;")
        log_header.addWidget(log_title)
        
        self.status_label = QLabel("Idle")
        self.status_label.setStyleSheet("color: #fbbf24; font-weight: bold; font-size: 12px;")
        log_header.addStretch()
        log_header.addWidget(self.status_label)
        log_header.addSpacing(15)

        self.refresh_stats_btn = QPushButton("Refresh")
        self.refresh_stats_btn.setFixedHeight(28)
        self.refresh_stats_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_stats_btn.clicked.connect(self._refresh_stats)
        log_header.addWidget(self.refresh_stats_btn)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setFixedHeight(28)
        self.stop_btn.setStyleSheet("background-color: #991b1b;")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.clicked.connect(self._stop_task)
        log_header.addWidget(self.stop_btn)

        self.clear_log_btn = QPushButton("Clear")
        self.clear_log_btn.setFixedHeight(28)
        self.clear_log_btn.setCursor(Qt.PointingHandCursor)
        self.clear_log_btn.clicked.connect(lambda: self.log_viewer.clear())
        log_header.addWidget(self.clear_log_btn)

        log_layout.addLayout(log_header)

        self.log_viewer = QTextEdit()
        self.log_viewer.setReadOnly(True)
        self.log_viewer.setFont(QFont("Consolas", 10))
        self.log_viewer.setStyleSheet("""
            QTextEdit {
                background-color: #1e293b;
                color: #e2e8f0;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 8px;
            }
        """)
        log_layout.addWidget(self.log_viewer)

        content_layout.addWidget(log_panel)

    def _build_tasks_section(self):
        card = SectionCard("Pipeline Tasks", "Core data management")
        
        # New automated update button
        auto_btn = QPushButton("Fetch Data (From Yahoo)")
        auto_btn.setFixedHeight(30)
        auto_btn.setFixedWidth(200)
        auto_btn.setCursor(Qt.PointingHandCursor)
        auto_btn.setToolTip("Automatically sync symbols, handle renames, and update prices.")
        auto_btn.clicked.connect(self._run_fetch_data)
        card.layout.addWidget(auto_btn, 0, Qt.AlignCenter)
        
        self.refetch_last_cb = QCheckBox("Refetch Last Updated Date")
        self.refetch_last_cb.setChecked(False)
        self.refetch_last_cb.setCursor(Qt.PointingHandCursor)
        self.refetch_last_cb.setToolTip("If checked, overwrites the data for the last available date in the DB.")
        self.refetch_last_cb.setStyleSheet("""
            QCheckBox {
                color: #e2e8f0;
                font-size: 11px;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                background-color: #334155;
                border: 1px solid #475569;
                border-radius: 3px;
            }
            QCheckBox::indicator:checked {
                background-color: #38bdf8;
                image: url("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMCIgaGVpZ2h0PSIxMCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9IiNmZmYiIHN0cm9rZS13aWR0aD0iNCIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cG9seWxpbmUgcG9pbnRzPSIyMCA2IDkgMTcgNCAxMiIvPjwvc3ZnPg==");
            }
            QCheckBox::indicator:hover {
                border-color: #38bdf8;
            }
        """)
        card.layout.addWidget(self.refetch_last_cb, 0, Qt.AlignCenter)
        
        card.layout.addSpacing(10)
        
        for label, script, args in TASKS:
            btn = QPushButton(label)
            btn.setFixedHeight(30)
            btn.setFixedWidth(200) # Even smaller buttons
            btn.setToolTip(TOOLTIPS.get(label, ""))
            btn.setCursor(Qt.PointingHandCursor)
                
            btn.clicked.connect(lambda checked=False, s=script, a=args, l=label: self._run_script(s, a, l))
            card.layout.addWidget(btn, 0, Qt.AlignCenter)
            
        card.layout.addSpacing(5)
        
        self.apply_changes_cb = QCheckBox("Auto-Apply Detected Changes")
        self.apply_changes_cb.setChecked(True)
        self.apply_changes_cb.setCursor(Qt.PointingHandCursor)
        self.apply_changes_cb.setStyleSheet("""
            QCheckBox {
                color: #e2e8f0;
                font-size: 11px;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                background-color: #334155;
                border: 1px solid #475569;
                border-radius: 4px;
            }
            QCheckBox::indicator:checked {
                background-color: #38bdf8;
                image: url("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9IndoaXRlIiBzdHJva2Utd2lkdGg9IjMiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCI+PHBvbHlsaW5lIHBvaW50cz0iMjAgNiA5IDE3IDQgMTIiLz48L3N2Zz4=");
            }
            QCheckBox::indicator:hover {
                border-color: #38bdf8;
            }
        """)
        card.layout.addWidget(self.apply_changes_cb, 0, Qt.AlignCenter)

        detect_btn = QPushButton("Detect Symbol Changes")
        detect_btn.setFixedHeight(30)
        detect_btn.setFixedWidth(200)
        detect_btn.setToolTip(TOOLTIPS["Detect Symbol Changes"])
        detect_btn.setCursor(Qt.PointingHandCursor)
        detect_btn.clicked.connect(self._run_symbol_detection)
        card.layout.addWidget(detect_btn, 0, Qt.AlignCenter)

        retry_btn = QPushButton("Retry Failures")
        retry_btn.setFixedHeight(30)
        retry_btn.setFixedWidth(200)
        retry_btn.setStyleSheet("background-color: #b45309;")
        retry_btn.setCursor(Qt.PointingHandCursor)

        retry_btn.clicked.connect(self._retry_failed)
        card.layout.addWidget(retry_btn, 0, Qt.AlignCenter)
        
        self.sidebar_layout.addWidget(card)

    def _build_sharpe_section(self):
        card = SectionCard("Sharpe Screener", "Ranking filters")
        
        grid = QGridLayout()
        grid.setSpacing(8)
        grid.setColumnStretch(1, 1)
        
        def add_row(label, widget, row):
            l = QLabel(label)
            l.setStyleSheet("color: #94a3b8; font-size: 11px;")
            grid.addWidget(l, row, 0)
            widget.setFixedWidth(120) # Narrower inputs
            grid.addWidget(widget, row, 1, Qt.AlignRight)

        self.sharpe_date = QDateEdit()
        self.sharpe_date.setDate(QDate.currentDate())
        self.sharpe_date.setCalendarPopup(True)
        self.sharpe_date.setDisplayFormat("dd-MM-yyyy")
        self.sharpe_date.setCursor(Qt.PointingHandCursor)
        add_row("As-of Date:", self.sharpe_date, 0)
        
        self.sharpe_mcap = QLineEdit("1000")
        add_row("MCAP (Cr):", self.sharpe_mcap, 1)
        
        self.sharpe_rf = QLineEdit("6.5")
        add_row("ROC Hurdle %:", self.sharpe_rf, 2)
        
        self.sharpe_turnover = QLineEdit("1.0")
        add_row("Turnover (Cr):", self.sharpe_turnover, 3)
        
        l_ls = QLabel("L/S Months:")
        l_ls.setStyleSheet("color: #94a3b8; font-size: 11px;")
        grid.addWidget(l_ls, 4, 0)
        
        ls_container = QWidget()
        ls_container.setFixedWidth(100)
        ls_layout = QHBoxLayout(ls_container)
        ls_layout.setContentsMargins(0, 0, 0, 0)
        ls_layout.setSpacing(5)
        self.sharpe_long = QLineEdit("6")
        self.sharpe_short = QLineEdit("3")
        ls_layout.addWidget(self.sharpe_long)
        ls_layout.addWidget(QLabel("/"))
        ls_layout.addWidget(self.sharpe_short)
        grid.addWidget(ls_container, 4, 1, Qt.AlignRight)
        
        card.layout.addLayout(grid)
        
        run_btn = QPushButton("Run Sharpe Screener")
        run_btn.setFixedHeight(35)
        run_btn.setFixedWidth(200)
        run_btn.setCursor(Qt.PointingHandCursor)
        run_btn.setStyleSheet("font-weight: bold;")
        run_btn.clicked.connect(self._run_sharpe)
        card.layout.addWidget(run_btn, 0, Qt.AlignCenter)
        
        self.sidebar_layout.addWidget(card)

    def _build_snapshot_section(self):
        card = SectionCard("Latest Snapshot", "Recent entries")
        
        grid = QGridLayout()
        l = QLabel("Symbol Limit:")
        l.setStyleSheet("color: #94a3b8; font-size: 11px;")
        grid.addWidget(l, 0, 0)
        
        self.snapshot_limit = QLineEdit("50")
        self.snapshot_limit.setFixedWidth(100)
        grid.addWidget(self.snapshot_limit, 0, 1, Qt.AlignRight)
        
        card.layout.addLayout(grid)
        
        btn = QPushButton("Fetch Latest Rows")
        btn.setFixedHeight(32)
        btn.setFixedWidth(200)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet("background-color: #0f766e;")
        btn.clicked.connect(self._run_snapshot)
        card.layout.addWidget(btn, 0, Qt.AlignCenter)
        
        self.sidebar_layout.addWidget(card)

    def _build_query_section(self):
        card = SectionCard("Query History", "Symbol performance")
        
        grid = QGridLayout()
        grid.setSpacing(8)
        
        def add_row(label, widget, row):
            l = QLabel(label)
            l.setStyleSheet("color: #94a3b8; font-size: 11px;")
            grid.addWidget(l, row, 0)
            widget.setFixedWidth(100)
            grid.addWidget(widget, row, 1, Qt.AlignRight)

        self.query_sym = QLineEdit()
        self.query_sym.setPlaceholderText("RELIANCE")
        add_row("Symbol:", self.query_sym, 0)
        
        self.query_from = QDateEdit()
        self.query_from.setDate(QDate.currentDate().addDays(-30))
        self.query_from.setCalendarPopup(True)
        self.query_from.setDisplayFormat("dd-MM-yyyy")
        self.query_from.setCursor(Qt.PointingHandCursor)
        add_row("From:", self.query_from, 1)
        
        self.query_to = QDateEdit()
        self.query_to.setDate(QDate.currentDate())
        self.query_to.setCalendarPopup(True)
        self.query_to.setDisplayFormat("dd-MM-yyyy")
        self.query_to.setCursor(Qt.PointingHandCursor)
        add_row("To:", self.query_to, 2)
        
        card.layout.addLayout(grid)
        
        btn = QPushButton("Fetch History Report")
        btn.setFixedHeight(32)
        btn.setFixedWidth(200)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet("background-color: #1d4ed8;")
        btn.clicked.connect(self._run_query)
        card.layout.addWidget(btn, 0, Qt.AlignCenter)

        self.export_query_btn = QPushButton("Export to Excel")
        self.export_query_btn.setFixedHeight(32)
        self.export_query_btn.setFixedWidth(200)
        self.export_query_btn.setCursor(Qt.PointingHandCursor)
        self.export_query_btn.setEnabled(False)
        self.export_query_btn.clicked.connect(self._run_export_query)
        card.layout.addWidget(self.export_query_btn, 0, Qt.AlignCenter)
        
        self.sidebar_layout.addWidget(card)

    def _update_clock(self):
        self.clock_label.setText(datetime.now().strftime("%H:%M:%S  |  %d %b %Y"))

    def _refresh_stats(self):
        if not DB_FILE.exists():
            return
            
        def worker():
            try:
                conn = sqlite3.connect(DB_FILE)
                active_count = conn.execute("SELECT COUNT(*) FROM symbols WHERE active = 1").fetchone()[0]
                latest_date = conn.execute("SELECT MAX(date) FROM adjusted_eod_prices").fetchone()[0]
                conn.close()
                self.active_sym_label.setText(f"Active Symbols: {active_count:,}")
                self.updated_upto_label.setText(f"Updated upto: {latest_date or '-'}")
            except Exception as e:
                log.error(f"Failed to refresh stats: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _run_script(self, script_name, args, label):
        if self.process and self.process.state() != QProcess.NotRunning:
            self._log(f"\n[Warning] Another task is already running: {self.status_label.text()}\n")
            return

        self.log_viewer.append(f"\n{'='*70}\nStarting: {label}\n{'='*70}\n")
        self.status_label.setText(f"Running: {label}")
        self.stop_btn.setEnabled(True)
        
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._handle_output)
        self.process.finished.connect(lambda code, exit_status, l=label: self._handle_finished(code, exit_status, l))
        self.process.start(sys.executable, [str(BASE_DIR / script_name)] + args)

    def _run_symbol_detection(self):
        args = []
        label = "Detect Symbol Changes"
        if self.apply_changes_cb.isChecked():
            args.append("--apply")
            label = "Detect & Apply Symbol Changes"
        self._run_script("symbol_change_handler.py", args, label)

    def _handle_output(self):
        data = self.process.readAllStandardOutput().data().decode()
        self.log_viewer.insertPlainText(data)
        self.log_viewer.moveCursor(QTextCursor.End)

    def _handle_finished(self, exit_code, exit_status, label):
        self.stop_btn.setEnabled(False)
        if exit_code == 0:
            self.status_label.setText(f"Done: {label}")
            self._log(f"\n[Success] {label} completed successfully.\n")
            
            if label.startswith("Query ") or label == "Latest Snapshot":
                self.export_query_btn.setEnabled(True)
                
            # If there are more tasks in the queue, process them
            if self.task_queue:
                self._process_next_task()
        else:
            self.status_label.setText(f"Failed: {label}")
            self._log(f"\n[Error] {label} failed with exit code {exit_code}.\n")
            # Clear queue on failure to prevent cascading
            if self.task_queue:
                self._log("[System] Clearing task queue due to failure.\n")
                self.task_queue = []
        self._refresh_stats()

    def _process_next_task(self):
        if not self.task_queue:
            return
        script, args, label = self.task_queue.pop(0)
        self._run_script(script, args, label)

    def _run_fetch_data(self):
        if self.process and self.process.state() != QProcess.NotRunning:
            self._log("\n[Warning] Cannot start Fetch Data: Another task is already running.\n")
            return

        # Check if DB is fresh or update
        is_fresh = True
        if DB_FILE.exists():
            try:
                import sqlite3
                with sqlite3.connect(DB_FILE) as conn:
                    # Check if we have any adjusted prices stored
                    count = conn.execute("SELECT COUNT(*) FROM adjusted_eod_prices").fetchone()[0]
                    if count > 0:
                        is_fresh = False
            except Exception:
                pass

        if is_fresh:
            self._log("\n[System] Fresh database detected. Starting Bootstrap (from 2020)...\n")
            self.task_queue = [
                ("download_eod.py", ["--bootstrap"], "Bootstrap Yahoo EOD")
            ]
        else:
            self._log("\n[System] Existing data detected. Starting Incremental Update...\n")
            eod_args = []
            if self.refetch_last_cb.isChecked():
                eod_args.append("--refetch-last")
                self._log("[System] Option enabled: Refetching last available date.\n")
            
            self.task_queue = [
                ("sync_symbols.py", [], "Sync Symbols"),
                ("symbol_change_handler.py", ["--apply"], "Apply Symbol Changes"),
                ("download_eod.py", eod_args, "Download EOD Updates"),
                ("sync_share_counts.py", ["--only-missing"], "Download Missing Shares")
            ]
        
        self._process_next_task()

    def _stop_task(self):
        if self.process:
            self._log("\n[System] Sending termination signal to task...\n")
            if self.task_queue:
                self._log("[System] Clearing task queue.\n")
                self.task_queue = []
            self.process.terminate()
            if not self.process.waitForFinished(3000):
                self.process.kill()

    def _log(self, text):
        self.log_viewer.append(text)
        self.log_viewer.moveCursor(QTextCursor.End)

    def _run_sharpe(self):
        date_str = self.sharpe_date.date().toString("yyyy-MM-dd")
        args = [
            "--mcap", self.sharpe_mcap.text(),
            "--rf", self.sharpe_rf.text(),
            "--turnover", self.sharpe_turnover.text(),
            "--long-months", self.sharpe_long.text(),
            "--short-months", self.sharpe_short.text(),
            "--date", date_str,
        ]
        self._run_script("sharpe_screener.py", args, "Sharpe Screener")

    def _run_snapshot(self):
        args = ["--latest", "--limit", self.snapshot_limit.text()]
        self.last_query_args = args
        self.export_query_btn.setEnabled(False)
        self._run_script("query_prices.py", args, "Latest Snapshot")

    def _run_query(self):
        symbol = self.query_sym.text().strip().upper()
        if not symbol:
            self._log("\n[Error] Please enter a symbol ticker.\n")
            return
        from_date = self.query_from.date().toString("yyyy-MM-dd")
        to_date = self.query_to.date().toString("yyyy-MM-dd")
        args = ["--symbol", symbol, "--limit", "5000", "--from", from_date, "--to", to_date]
        self.last_query_args = args
        self.export_query_btn.setEnabled(False)
        self._run_script("query_prices.py", args, f"Query {symbol}")

    def _run_export_query(self):
        if not self.last_query_args:
            return
            
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Query Results", "query_results.xlsx", "Excel Files (*.xlsx)"
        )
        if not file_path:
            return
            
        args = self.last_query_args + ["--excel", file_path]
        self._run_script("query_prices.py", args, "Exporting to Excel")

    def _retry_failed(self):
        if not FAILED_EOD_FILE.exists():
            self._log("\n[Info] No failed EOD download file found.\n")
            return
        import csv
        symbols = []
        try:
            with open(FAILED_EOD_FILE, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    s = row.get("symbol", "").strip().upper()
                    if s and s not in symbols:
                        symbols.append(s)
        except Exception as e:
            self._log(f"\n[Error] Failed to read failures file: {e}\n")
            return
        if not symbols:
            self._log("\n[Info] Failures file is empty.\n")
            return
        self._run_script("download_eod.py", ["--symbols", ",".join(symbols)], "Retry Failed EOD")

def main():
    app = QApplication(sys.argv)
    apply_stylesheet(app, theme='dark_blue.xml')
    app.setStyleSheet(app.styleSheet() + """
        QMainWindow { background-color: #0f172a; }
        QPushButton { border-radius: 4px; padding: 4px; font-size: 11px; }
        QLineEdit, QDateEdit { 
            background-color: #334155; 
            border: 1px solid #475569; 
            border-radius: 4px; 
            padding: 2px 4px; 
            color: white;
            font-size: 11px;
            height: 26px;
        }
        QDateEdit {
            padding-right: 25px;
        }
        QDateEdit::drop-down {
            subcontrol-origin: border;
            subcontrol-position: top right;
            width: 25px;
            border-left-width: 1px;
            border-left-color: #475569;
            border-left-style: solid;
            background-color: #1e293b;
            border-top-right-radius: 4px;
            border-bottom-right-radius: 4px;
        }
        QDateEdit::down-arrow {
            image: url("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMCIgaGVpZ2h0PSIxMCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9IiMzOGJkZjgiIHN0cm9rZS13aWR0aD0iMyIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNNiA5bDYgNiA2LTYiLz48L3N2Zz4=");
            width: 12px;
            height: 12px;
        }
        QCalendarWidget QWidget {
            alternate-background-color: #1e293b;
        }
        QCalendarWidget QAbstractItemView:enabled {
            color: #f1f5f9;
            background-color: #0f172a;
            selection-background-color: #38bdf8;
            selection-color: #0f172a;
        }
        QCalendarWidget QWidget#qt_calendar_navigationbar {
            background-color: #1e293b;
            min-height: 35px;
        }
        QCalendarWidget QToolButton {
            color: #f1f5f9;
            background-color: transparent;
            icon-size: 20px;
            font-weight: bold;
            border-radius: 4px;
        }
        QCalendarWidget QToolButton:hover {
            background-color: #334155;
        }
        QCalendarWidget QToolButton#qt_calendar_prevmonth {
            qproperty-icon: url("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9IiMzOGJkZjgiIHN0cm9rZS13aWR0aD0iMyIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNMTUgMThsLTYtNiA2LTYiLz48L3N2Zz4=");
        }
        QCalendarWidget QToolButton#qt_calendar_nextmonth {
            qproperty-icon: url("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9IiMzOGJkZjgiIHN0cm9rZS13aWR0aD0iMyIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNOSAxOGw2LTYtNi02Ii8+PC9zdmc+");
        }
        QCalendarWidget QSpinBox {
            color: #f1f5f9;
            background-color: #334155;
            selection-background-color: #38bdf8;
            selection-color: #0f172a;
            border-radius: 3px;
        }
        /* Style for the day names header */
        QCalendarWidget QWidget#qt_calendar_calendarview {
            background-color: #0f172a;
        }
        QCalendarWidget QMenu {
            background-color: #1e293b;
            color: #f1f5f9;
            border: 1px solid #334155;
        }
        QCalendarWidget QTableView {
            alternate-background-color: #1e293b;
        }
        QGroupBox { 
            border: 1px solid #334155; 
            border-radius: 6px; 
            margin-top: 2px;
            background-color: #1e293b;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 8px;
            padding: 0 3px;
            color: #38bdf8;
        }
    """)
    window = YahooNSEGUI()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
