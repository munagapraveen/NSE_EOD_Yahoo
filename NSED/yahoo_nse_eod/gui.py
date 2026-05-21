"""CustomTkinter GUI for the standalone Yahoo/NSE EOD project."""

from __future__ import annotations

import queue
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import customtkinter as ctk

from config import DB_FILE, FAILED_EOD_FILE

BASE_DIR = Path(__file__).resolve().parent


def _find_python():
    venv_names = [".venv", "venv", "env", ".env"]
    if sys.platform == "win32":
        candidates = ["Scripts/python.exe", "Scripts/python3.exe"]
    else:
        candidates = ["bin/python", "bin/python3"]
    for name in venv_names:
        for suffix in candidates:
            candidate = BASE_DIR / name / suffix
            if candidate.exists():
                return str(candidate)
    return sys.executable


PYTHON = _find_python()


TASKS = [
    ("Sync NSE Symbols", "sync_symbols.py", []),
    ("Bootstrap Yahoo EOD", "download_eod.py", ["--bootstrap"]),
    ("Download Shares", "download_shares.py", []),
    ("Build Adjusted Prices", "adjust_splits.py", []),
    ("Daily Price Refresh", "download_eod.py", []),
    ("Review Corporate Actions", "corporate_actions.py", []),
    ("Detect Symbol Changes", "symbol_change_handler.py", []),
]

TOOLTIPS = {
    "Sync NSE Symbols": "Refresh the active NSE symbol master, ISINs, and Yahoo ticker mappings.",
    "Bootstrap Yahoo EOD": "Download full historical Yahoo EOD price data for the tracked symbol universe.",
    "Download Shares": "Fetch historical shares outstanding from Yahoo for market-cap calculations.",
    "Build Adjusted Prices": "Rebuild split-adjusted prices, market cap, and moving averages from stored raw data.",
    "Daily Price Refresh": "Append the latest available Yahoo EOD rows and update only the new dates.",
    "Review Corporate Actions": "Show stored split/dividend events and optionally rebuild affected symbols.",
    "Detect Symbol Changes": "Detect probable NSE ticker renames using NSE files and ISIN continuity.",
    "Apply Symbol Changes": "Apply detected renames to stored symbol history and adjusted datasets.",
    "Retry Failed EOD Downloads": "Retry only the symbols listed in the latest failed-EOD report file.",
    "Run Screener": "Run the standalone Sharpe screener with the current filters and month windows.",
    "Run Top 100": "Run the Sharpe screener and focus on the top 100 ranked stocks.",
    "Latest Snapshot": "Query the latest adjusted row for many symbols from the standalone database.",
    "Query Symbol": "Inspect one symbol's adjusted close, market cap, and moving averages by date range.",
    "Refresh Stats": "Reload database counts and the latest stored date from the standalone DB.",
    "Clear Log": "Clear the live log panel on the right side of the window.",
}


class Tooltip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        self.widget.bind("<Enter>", self.show)
        self.widget.bind("<Leave>", self.hide)

    def show(self, _event=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self.tip = ctk.CTkToplevel(self.widget)
        self.tip.overrideredirect(True)
        self.tip.geometry(f"+{x}+{y}")
        self.tip.attributes("-topmost", True)
        label = ctk.CTkLabel(
            self.tip,
            text=self.text,
            justify="left",
            corner_radius=10,
            fg_color="#111827",
            text_color="#e5e7eb",
            wraplength=280,
            padx=12,
            pady=8,
            font=ctk.CTkFont(size=12),
        )
        label.pack()

    def hide(self, _event=None):
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


class YahooNSEGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Yahoo NSE EOD Manager")
        self.geometry("1380x860")
        self.minsize(1100, 720)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.running = False
        self.output_queue = queue.Queue()
        self.status_var = ctk.StringVar(value="Idle")
        self.clock_var = ctk.StringVar(value="")
        self.stats_vars = {
            "db_path": ctk.StringVar(value=str(DB_FILE)),
            "symbols": ctk.StringVar(value="-"),
            "rows": ctk.StringVar(value="-"),
            "latest": ctk.StringVar(value="-"),
            "actions": ctk.StringVar(value="-"),
        }

        self.sharpe_mode = ctk.StringVar(value="live")
        self.sharpe_date_var = ctk.StringVar(value=datetime.today().strftime("%d/%m/%Y"))
        self.sharpe_top_var = ctk.StringVar(value="50")
        self.sharpe_mcap_var = ctk.StringVar(value="1000")
        self.sharpe_rf_var = ctk.StringVar(value="6.5")
        self.sharpe_turnover_var = ctk.StringVar(value="1.0")
        self.sharpe_long_var = ctk.StringVar(value="6")
        self.sharpe_short_var = ctk.StringVar(value="3")

        self.query_symbol_var = ctk.StringVar(value="")
        self.query_from_var = ctk.StringVar(value="")
        self.query_to_var = ctk.StringVar(value="")
        self.query_limit_var = ctk.StringVar(value="50")

        self.task_buttons = []
        self._build_ui()
        self._tick_clock()
        self.after(150, self._refresh_stats_async)

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, corner_radius=0, fg_color="#0f172a")
        header.grid(row=0, column=0, columnspan=2, sticky="nsew")
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header,
            text="Yahoo NSE EOD Manager",
            font=ctk.CTkFont(size=26, weight="bold"),
        ).grid(row=0, column=0, padx=20, pady=14, sticky="w")
        ctk.CTkLabel(
            header,
            text=f"Python: {Path(PYTHON).name}",
            font=ctk.CTkFont(size=13),
            text_color="#93c5fd",
        ).grid(row=0, column=1, padx=10, pady=14, sticky="w")
        ctk.CTkLabel(
            header,
            textvariable=self.clock_var,
            font=ctk.CTkFont(size=13),
        ).grid(row=0, column=2, padx=20, pady=14, sticky="e")

        sidebar = ctk.CTkScrollableFrame(self, width=420, corner_radius=0)
        sidebar.grid(row=1, column=0, sticky="nsew")
        sidebar.grid_columnconfigure(0, weight=1)

        self._build_stats_card(sidebar)
        self._build_tasks_card(sidebar)
        self._build_sharpe_card(sidebar)
        self._build_query_card(sidebar)

        right = ctk.CTkFrame(self, corner_radius=0)
        right.grid(row=1, column=1, sticky="nsew", padx=(0, 0), pady=0)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        topbar = ctk.CTkFrame(right, fg_color="transparent")
        topbar.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 10))
        topbar.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            topbar,
            text="Run Log",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            topbar,
            textvariable=self.status_var,
            font=ctk.CTkFont(size=13),
            text_color="#fbbf24",
        ).grid(row=0, column=1, sticky="e", padx=(10, 10))
        stats_btn = ctk.CTkButton(
            topbar,
            text="Refresh Stats",
            width=110,
            command=self._refresh_stats_async,
        )
        stats_btn.grid(row=0, column=2, padx=(0, 10))
        Tooltip(stats_btn, TOOLTIPS["Refresh Stats"])
        clear_btn = ctk.CTkButton(
            topbar,
            text="Clear Log",
            width=90,
            fg_color="#334155",
            hover_color="#475569",
            command=self._clear_log,
        )
        clear_btn.grid(row=0, column=3)
        Tooltip(clear_btn, TOOLTIPS["Clear Log"])

        self.log_box = ctk.CTkTextbox(
            right,
            corner_radius=18,
            font=("Consolas", 13),
            wrap="word",
        )
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 18))
        self.log_box.insert("end", "Ready.\n")
        self.log_box.configure(state="disabled")

    def _section_card(self, parent, title, subtitle=None):
        card = ctk.CTkFrame(parent, corner_radius=18)
        card.pack(fill="x", padx=14, pady=(14, 0))
        ctk.CTkLabel(
            card,
            text=title,
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(14, 2))
        if subtitle:
            ctk.CTkLabel(
                card,
                text=subtitle,
                font=ctk.CTkFont(size=12),
                text_color="#94a3b8",
            ).pack(anchor="w", padx=16, pady=(0, 12))
        return card

    def _build_stats_card(self, parent):
        card = self._section_card(
            parent,
            "Database",
            "Standalone Yahoo/NSE SQLite status",
        )
        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=16, pady=(0, 14))
        grid.grid_columnconfigure(1, weight=1)

        rows = [
            ("DB File", "db_path"),
            ("Tracked Symbols", "symbols"),
            ("Adjusted Rows", "rows"),
            ("Latest Date", "latest"),
            ("Corporate Actions", "actions"),
        ]
        for idx, (label, key) in enumerate(rows):
            ctk.CTkLabel(
                grid,
                text=label,
                font=ctk.CTkFont(size=12),
                text_color="#94a3b8",
            ).grid(row=idx, column=0, sticky="w", pady=3)
            ctk.CTkLabel(
                grid,
                textvariable=self.stats_vars[key],
                font=ctk.CTkFont(size=13, weight="bold"),
            ).grid(row=idx, column=1, sticky="w", padx=(12, 0), pady=3)

    def _build_tasks_card(self, parent):
        card = self._section_card(
            parent,
            "Pipeline Tasks",
            "Run the core backend scripts without leaving the app",
        )
        button_grid = ctk.CTkFrame(card, fg_color="transparent")
        button_grid.pack(fill="x", padx=16, pady=(0, 16))
        button_grid.grid_columnconfigure((0, 1), weight=1)

        for idx, (label, script, args) in enumerate(TASKS):
            btn = ctk.CTkButton(
                button_grid,
                text=label,
                height=38,
                command=lambda s=script, a=args, l=label: self._run_script(s, a, l),
            )
            btn.grid(row=idx // 2, column=idx % 2, sticky="ew", padx=6, pady=6)
            self.task_buttons.append(btn)
            Tooltip(btn, TOOLTIPS.get(label, label))

        apply_btn = ctk.CTkButton(
            card,
            text="Apply Symbol Changes",
            fg_color="#7c3aed",
            hover_color="#6d28d9",
            command=lambda: self._run_script(
                "symbol_change_handler.py", ["--apply"], "Apply Symbol Changes"
            ),
        )
        apply_btn.pack(fill="x", padx=16, pady=(0, 16))
        Tooltip(apply_btn, TOOLTIPS["Apply Symbol Changes"])

        retry_btn = ctk.CTkButton(
            card,
            text="Retry Failed EOD Downloads",
            fg_color="#b45309",
            hover_color="#92400e",
            command=self._retry_failed_eod_downloads,
        )
        retry_btn.pack(fill="x", padx=16, pady=(0, 16))
        self.task_buttons.append(retry_btn)
        Tooltip(retry_btn, TOOLTIPS["Retry Failed EOD Downloads"])

    def _build_sharpe_card(self, parent):
        card = self._section_card(
            parent,
            "Sharpe Screener",
            "Standalone screener powered by adjusted prices in this project",
        )

        mode_row = ctk.CTkFrame(card, fg_color="transparent")
        mode_row.pack(fill="x", padx=16, pady=(0, 10))
        ctk.CTkSegmentedButton(
            mode_row,
            values=["live", "historical"],
            variable=self.sharpe_mode,
        ).pack(fill="x")

        form = ctk.CTkFrame(card, fg_color="transparent")
        form.pack(fill="x", padx=16, pady=(0, 10))
        form.grid_columnconfigure((0, 1), weight=1)

        self._labeled_entry(form, "As-of Date (dd/mm/yyyy)", self.sharpe_date_var, 0, 0)
        self._labeled_entry(form, "Top N", self.sharpe_top_var, 0, 1)
        self._labeled_entry(form, "MCAP Filter (Cr)", self.sharpe_mcap_var, 1, 0)
        self._labeled_entry(form, "ROC Hurdle %", self.sharpe_rf_var, 1, 1)
        self._labeled_entry(form, "Turnover (Cr)", self.sharpe_turnover_var, 2, 0)
        self._labeled_entry(form, "Long / Short Months", None, 2, 1, dual_vars=(self.sharpe_long_var, self.sharpe_short_var))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 16))
        btn_row.grid_columnconfigure((0, 1), weight=1)

        run_btn = ctk.CTkButton(btn_row, text="Run Screener", command=lambda: self._run_sharpe(False))
        run_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.task_buttons.append(run_btn)
        Tooltip(run_btn, TOOLTIPS["Run Screener"])

        top_btn = ctk.CTkButton(
            btn_row,
            text="Run Top 100",
            fg_color="#059669",
            hover_color="#047857",
            command=lambda: self._run_sharpe(True),
        )
        top_btn.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self.task_buttons.append(top_btn)
        Tooltip(top_btn, TOOLTIPS["Run Top 100"])

    def _build_query_card(self, parent):
        card = self._section_card(
            parent,
            "Inspect Data",
            "Query adjusted close, market cap, and moving averages",
        )
        form = ctk.CTkFrame(card, fg_color="transparent")
        form.pack(fill="x", padx=16, pady=(0, 10))
        form.grid_columnconfigure((0, 1), weight=1)

        self._labeled_entry(form, "Symbol", self.query_symbol_var, 0, 0)
        self._labeled_entry(form, "Limit", self.query_limit_var, 0, 1)
        self._labeled_entry(form, "From Date (yyyy-mm-dd)", self.query_from_var, 1, 0)
        self._labeled_entry(form, "To Date (yyyy-mm-dd)", self.query_to_var, 1, 1)

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 16))
        btn_row.grid_columnconfigure((0, 1), weight=1)

        latest_btn = ctk.CTkButton(
            btn_row,
            text="Latest Snapshot",
            fg_color="#0f766e",
            hover_color="#115e59",
            command=self._run_latest_query,
        )
        latest_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.task_buttons.append(latest_btn)
        Tooltip(latest_btn, TOOLTIPS["Latest Snapshot"])

        query_btn = ctk.CTkButton(
            btn_row,
            text="Query Symbol",
            fg_color="#1d4ed8",
            hover_color="#1e40af",
            command=self._run_symbol_query,
        )
        query_btn.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self.task_buttons.append(query_btn)
        Tooltip(query_btn, TOOLTIPS["Query Symbol"])

    def _labeled_entry(self, parent, label, variable, row, column, dual_vars=None):
        wrapper = ctk.CTkFrame(parent, fg_color="transparent")
        wrapper.grid(row=row, column=column, sticky="ew", padx=6, pady=6)
        ctk.CTkLabel(
            wrapper,
            text=label,
            font=ctk.CTkFont(size=12),
            text_color="#94a3b8",
        ).pack(anchor="w", pady=(0, 4))

        if dual_vars is not None:
            row_frame = ctk.CTkFrame(wrapper, fg_color="transparent")
            row_frame.pack(fill="x")
            first, second = dual_vars
            ctk.CTkEntry(row_frame, textvariable=first).pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(row_frame, text="/", width=18).pack(side="left", padx=6)
            ctk.CTkEntry(row_frame, textvariable=second).pack(side="left", fill="x", expand=True)
        else:
            ctk.CTkEntry(wrapper, textvariable=variable).pack(fill="x")

    def _tick_clock(self):
        self.clock_var.set(datetime.now().strftime("%a %d %b %Y  %H:%M:%S"))
        self.after(1000, self._tick_clock)

    def _append_log(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _set_buttons_state(self, disabled):
        state = "disabled" if disabled else "normal"
        for btn in self.task_buttons:
            btn.configure(state=state)

    def _run_script(self, script_name, args, label):
        if self.running:
            self._append_log("Another task is already running.\n")
            return

        self.running = True
        self._set_buttons_state(True)
        self.status_var.set(f"Running: {label}")
        cmd = [PYTHON, str(BASE_DIR / script_name)] + list(args)
        self._append_log(f"\n{'=' * 72}\nStarting: {label}\n{'=' * 72}\n")

        def worker():
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(BASE_DIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                for line in proc.stdout:
                    if line:
                        self.output_queue.put(("line", line))
                proc.wait()
                self.output_queue.put(("done", proc.returncode, label))
            except Exception as exc:
                self.output_queue.put(("error", str(exc), label))

        threading.Thread(target=worker, daemon=True).start()
        self.after(80, self._poll_output)

    def _poll_output(self):
        while True:
            try:
                item = self.output_queue.get_nowait()
            except queue.Empty:
                break

            kind = item[0]
            if kind == "line":
                self._append_log(item[1])
            elif kind == "error":
                _, message, label = item
                self._append_log(f"\nError while running {label}: {message}\n")
                self.status_var.set("Failed")
                self.running = False
                self._set_buttons_state(False)
                self._refresh_stats_async()
            elif kind == "done":
                _, returncode, label = item
                if returncode == 0:
                    self._append_log(f"\nCompleted: {label}\n")
                    self.status_var.set(f"Done: {label}")
                else:
                    self._append_log(f"\nFailed: {label} (exit code {returncode})\n")
                    self.status_var.set(f"Failed: {label}")
                self.running = False
                self._set_buttons_state(False)
                self._refresh_stats_async()

        if self.running:
            self.after(80, self._poll_output)

    def _run_sharpe(self, top_100):
        args = [
            "--top", "100" if top_100 else self.sharpe_top_var.get().strip(),
            "--mcap", self.sharpe_mcap_var.get().strip(),
            "--rf", self.sharpe_rf_var.get().strip(),
            "--turnover", self.sharpe_turnover_var.get().strip(),
            "--long-months", self.sharpe_long_var.get().strip(),
            "--short-months", self.sharpe_short_var.get().strip(),
        ]
        label = "Sharpe Screener"
        if self.sharpe_mode.get() == "historical":
            raw = self.sharpe_date_var.get().strip()
            try:
                date_val = datetime.strptime(raw, "%d/%m/%Y").strftime("%Y-%m-%d")
            except ValueError:
                self._append_log("Invalid Sharpe date. Use dd/mm/yyyy.\n")
                return
            args += ["--date", date_val]
            label = f"Sharpe Screener ({raw})"
        self._run_script("sharpe_screener.py", args, label)

    def _run_symbol_query(self):
        symbol = self.query_symbol_var.get().strip().upper()
        if not symbol:
            self._append_log("Enter a symbol for query.\n")
            return
        args = ["--symbol", symbol, "--limit", self.query_limit_var.get().strip() or "50"]
        if self.query_from_var.get().strip():
            args += ["--from", self.query_from_var.get().strip()]
        if self.query_to_var.get().strip():
            args += ["--to", self.query_to_var.get().strip()]
        self._run_script("query_prices.py", args, f"Query {symbol}")

    def _run_latest_query(self):
        args = ["--latest", "--limit", self.query_limit_var.get().strip() or "50"]
        self._run_script("query_prices.py", args, "Latest Snapshot")

    def _retry_failed_eod_downloads(self):
        if not FAILED_EOD_FILE.exists():
            self._append_log("No failed EOD download file found.\n")
            return
        try:
            import csv

            with FAILED_EOD_FILE.open("r", newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                symbols = []
                for row in reader:
                    symbol = str(row.get("symbol", "")).strip().upper()
                    if symbol and symbol not in symbols:
                        symbols.append(symbol)
        except Exception as exc:
            self._append_log(f"Could not read failed EOD file: {exc}\n")
            return

        if not symbols:
            self._append_log("Failed EOD file is empty.\n")
            return

        args = ["--symbols", ",".join(symbols)]
        self._run_script("download_eod.py", args, "Retry Failed EOD Downloads")

    def _refresh_stats_async(self):
        def worker():
            data = {
                "symbols": "-",
                "rows": "-",
                "latest": "-",
                "actions": "-",
            }
            try:
                if DB_FILE.exists():
                    conn = sqlite3.connect(DB_FILE)
                    try:
                        data["symbols"] = f"{conn.execute('SELECT COUNT(*) FROM symbols WHERE active = 1').fetchone()[0]:,}"
                        data["rows"] = f"{conn.execute('SELECT COUNT(*) FROM adjusted_eod_prices').fetchone()[0]:,}"
                        latest = conn.execute("SELECT MAX(date) FROM adjusted_eod_prices").fetchone()[0]
                        data["latest"] = latest or "-"
                        data["actions"] = f"{conn.execute('SELECT COUNT(*) FROM corporate_actions').fetchone()[0]:,}"
                    finally:
                        conn.close()
            except Exception as exc:
                data["latest"] = f"Error: {exc}"
            self.after(0, lambda: self._apply_stats(data))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_stats(self, data):
        for key, value in data.items():
            self.stats_vars[key].set(value)


def main():
    app = YahooNSEGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
