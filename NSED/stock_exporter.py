# -*- coding: utf-8 -*-
"""
stock_exporter.py -- Stock Data Exporter
=========================================
Standalone GUI to export EOD data for a specific stock
from the local SQLite database to a CSV file.

Features:
  - Enter stock ticker (e.g. RELIANCE, INFY, TCS)
  - Optional From and To date filters
  - Leave dates blank to export all available data
  - Saves as <SYMBOL>.csv in the same folder
  - Shows preview of first few rows before saving

Usage:
    python stock_exporter.py

Requirements:
    tkinter (built into Python)
    pandas, sqlite3 (built into Python)
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import calendar
import threading
from datetime import datetime
from pathlib import Path

# pandas, db, logger loaded lazily so GUI appears instantly

# ===========================================================================
# COLOURS & FONTS
# ===========================================================================

BG        = "#f5f5f0"
BG2       = "#ffffff"
BG3       = "#eeede8"
ACCENT    = "#2563eb"
ACCENT2   = "#1d4ed8"
SUCCESS   = "#16a34a"
DANGER    = "#dc2626"
WARNING   = "#d97706"
FG        = "#1a1a2e"
FG2       = "#4a4a6a"
FG3       = "#9999aa"
BORDER    = "#d1d5db"

FONT_TITLE = ("Segoe UI", 13, "bold")
FONT_LABEL = ("Segoe UI", 10)
FONT_BOLD  = ("Segoe UI", 10, "bold")
FONT_SMALL = ("Segoe UI", 9)
FONT_MONO  = ("Consolas", 9)
FONT_INPUT = ("Segoe UI", 11)

DATE_FORMAT_DISPLAY = "%d/%m/%Y"
DATE_FORMAT_DB = "%Y-%m-%d"
SYMBOL_CACHE = None


# ===========================================================================
# DATA FETCH
# ===========================================================================

def fetch_stock_data(symbol, from_date=None, to_date=None):
    """
    Fetches EOD data for a symbol from the local DB.
    Optionally filters by date range.
    Returns (DataFrame, error_message).
    """
    import pandas as pd
    from db import get_connection
    symbol = symbol.strip().upper()

    if not symbol:
        return None, "Please enter a stock symbol."

    where_clauses = ["symbol = ?"]
    params        = [symbol]

    if from_date:
        where_clauses.append("date >= ?")
        params.append(from_date)

    if to_date:
        where_clauses.append("date <= ?")
        params.append(to_date)

    where_sql = " AND ".join(where_clauses)

    try:
        with get_connection() as conn:
            df = pd.read_sql(
                f"""
                SELECT
                    symbol, company_name, isin, segment,
                    instrument_type, date,
                    open, high, low, close, volume
                FROM eod_data
                WHERE {where_sql}
                ORDER BY date ASC
                """,
                conn,
                params=params,
            )
    except Exception as exc:
        return None, f"Database error: {exc}"

    if df.empty:
        msg = f"No data found for '{symbol}'"
        if from_date or to_date:
            msg += f" between {from_date or 'start'} and {to_date or 'today'}"
        msg += ".\nCheck the symbol name or date range."
        return None, msg

    return df, None


def get_available_symbols(search=""):
    """Returns list of symbols matching search prefix."""
    try:
        from db import get_connection
        with get_connection() as conn:
            if search:
                cur = conn.execute(
                    "SELECT DISTINCT symbol FROM eod_data "
                    "WHERE symbol LIKE ? ORDER BY symbol LIMIT 20",
                    (f"{search.upper()}%",),
                )
            else:
                cur = conn.execute(
                    "SELECT DISTINCT symbol FROM eod_data ORDER BY symbol"
                )
            return [row[0] for row in cur.fetchall()]
    except Exception:
        return []


def load_all_symbols():
    """Load all symbols once for in-memory autocomplete filtering."""
    global SYMBOL_CACHE
    SYMBOL_CACHE = get_available_symbols("")
    return SYMBOL_CACHE


def get_date_range_for_symbol(symbol):
    """Returns (min_date, max_date) for a symbol in the DB."""
    try:
        from db import get_connection
        with get_connection() as conn:
            cur = conn.execute(
                "SELECT MIN(date), MAX(date) FROM eod_data WHERE symbol = ?",
                (symbol.upper(),),
            )
            row = cur.fetchone()
            return row[0], row[1]
    except Exception:
        return None, None


# ===========================================================================
# MAIN GUI
# ===========================================================================

class StockExporterGUI:

    def __init__(self, root):
        self.root = root
        self.root.title("Stock Data Exporter")
        self.root.configure(bg=BG)
        self.root.geometry("780x680")
        self.root.resizable(True, True)

        self.df_result  = None   # last fetched DataFrame
        self.save_dir   = Path(__file__).parent
        self.date_picker = None
        self.date_picker_target = None
        self.date_picker_month = None
        self.date_picker_year = None
        self.date_picker_day_var = None
        self.date_picker_month_var = None
        self.date_picker_year_var = None
        self.symbols_loaded = False
        self.symbol_load_in_progress = False

        self._build_ui()
        self.root.after(200, self._preload_symbols_async)

    # ------------------------------------------------------------------
    # UI BUILD
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Header
        header = tk.Frame(self.root, bg=ACCENT, height=58)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(
            header,
            text="  Stock Data Exporter",
            bg=ACCENT, fg="white",
            font=("Segoe UI", 14, "bold"),
        ).pack(side="left", padx=20, pady=14)

        tk.Label(
            header,
            text="Export EOD data from local database to CSV",
            bg=ACCENT, fg="#bfdbfe",
            font=FONT_SMALL,
        ).pack(side="left", pady=14)

        # Main content
        main = tk.Frame(self.root, bg=BG, padx=24, pady=20)
        main.pack(fill="both", expand=True)

        self._build_inputs(main)
        self._build_action_bar(main)
        self._build_status_bar(main)
        self._build_preview(main)

    def _label(self, parent, text):
        tk.Label(
            parent, text=text,
            bg=BG, fg=FG2, font=FONT_LABEL,
            anchor="w",
        ).pack(fill="x", pady=(0, 3))

    def _format_display_date(self, db_date):
        """Convert YYYY-MM-DD to dd/mm/yyyy for display."""
        if not db_date:
            return ""
        return datetime.strptime(db_date, DATE_FORMAT_DB).strftime(
            DATE_FORMAT_DISPLAY
        )

    def _parse_display_date(self, value):
        """Convert dd/mm/yyyy to YYYY-MM-DD for database filtering."""
        if not value:
            return None
        return datetime.strptime(value, DATE_FORMAT_DISPLAY).strftime(
            DATE_FORMAT_DB
        )

    def _preload_symbols_async(self):
        """Load symbol list in background so first search stays responsive."""
        if self.symbols_loaded or self.symbol_load_in_progress:
            return

        self.symbol_load_in_progress = True

        def worker():
            try:
                symbols = load_all_symbols()
                error = None
            except Exception as exc:
                symbols = []
                error = str(exc)

            def finish():
                self.symbol_load_in_progress = False
                if error:
                    self._set_status(f"Could not load symbol list: {error}", WARNING)
                    return
                self.symbols_loaded = True
                if not self.status_var.get() or "load symbol list" in self.status_var.get().lower():
                    self._set_status("Enter a stock symbol and click Search Data", FG2)

            self.root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _build_inputs(self, parent):
        card = tk.Frame(parent, bg=BG2, relief="flat", bd=0,
                        highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="x", pady=(0, 16))

        inner = tk.Frame(card, bg=BG2, padx=20, pady=20)
        inner.inner_pad = True
        inner.pack(fill="x")

        tk.Label(
            inner, text="Search & Filter",
            bg=BG2, fg=FG, font=FONT_TITLE,
        ).pack(anchor="w", pady=(0, 16))

        # Symbol row
        sym_row = tk.Frame(inner, bg=BG2)
        sym_row.pack(fill="x", pady=(0, 12))

        sym_left = tk.Frame(sym_row, bg=BG2)
        sym_left.pack(side="left", fill="x", expand=True)

        tk.Label(sym_left, text="Stock Symbol *",
                 bg=BG2, fg=FG2, font=FONT_LABEL).pack(anchor="w", pady=(0, 3))

        self.symbol_var = tk.StringVar()
        self.symbol_var.trace("w", self._on_symbol_change)

        sym_entry_frame = tk.Frame(
            sym_left, bg=BG2,
            highlightthickness=1, highlightbackground=BORDER,
        )
        sym_entry_frame.pack(fill="x")

        self.symbol_entry = tk.Entry(
            sym_entry_frame,
            textvariable=self.symbol_var,
            bg=BG2, fg=FG,
            font=FONT_INPUT,
            relief="flat",
            insertbackground=FG,
        )
        self.symbol_entry.pack(fill="x", ipady=8, padx=10)
        self.symbol_entry.bind("<Return>", lambda e: self._fetch())
        self.symbol_entry.bind(
            "<FocusIn>",
            lambda e: (self._highlight(sym_entry_frame), self._preload_symbols_async()),
        )
        self.symbol_entry.bind("<FocusOut>", lambda e: self._unhighlight(sym_entry_frame))

        # Autocomplete listbox
        self.suggest_frame = tk.Frame(sym_left, bg=BG2, bd=0)
        self.suggest_lb = tk.Listbox(
            self.suggest_frame,
            bg=BG2, fg=FG, font=FONT_MONO,
            relief="flat",
            selectbackground=ACCENT,
            selectforeground="white",
            height=5,
            highlightthickness=1,
            highlightbackground=BORDER,
        )
        self.suggest_lb.pack(fill="x")
        self.suggest_lb.bind("<<ListboxSelect>>", self._on_suggest_select)
        self.suggest_lb.bind("<Return>", self._on_suggest_select)

        # DB info label
        self.symbol_info = tk.Label(
            sym_left, text="", bg=BG2, fg=FG3, font=FONT_SMALL
        )
        self.symbol_info.pack(anchor="w", pady=(4, 0))

        # Date range row
        date_row = tk.Frame(inner, bg=BG2)
        date_row.pack(fill="x", pady=(0, 4))

        # From date
        from_col = tk.Frame(date_row, bg=BG2)
        from_col.pack(side="left", fill="x", expand=True, padx=(0, 12))

        tk.Label(from_col, text="From Date  (dd/mm/yyyy)",
                 bg=BG2, fg=FG2, font=FONT_LABEL).pack(anchor="w", pady=(0, 3))

        self.from_var = tk.StringVar()
        self.from_frame = tk.Frame(
            from_col, bg=BG2,
            highlightthickness=1, highlightbackground=BORDER,
        )
        self.from_frame.pack(fill="x")
        self.from_entry = tk.Entry(
            self.from_frame,
            textvariable=self.from_var,
            bg=BG2, fg=FG, font=FONT_INPUT,
            relief="flat", insertbackground=FG, state="readonly",
            readonlybackground=BG2,
        )
        self.from_entry.pack(side="left", fill="x", expand=True, ipady=8, padx=(10, 6))
        self.from_entry.bind(
            "<Button-1>",
            lambda e: self._open_date_picker(self.from_var, self.from_frame, self.from_entry),
        )
        tk.Button(
            self.from_frame,
            text="Pick",
            command=lambda: self._open_date_picker(
                self.from_var, self.from_frame, self.from_entry
            ),
            bg=BG3, fg=FG2, relief="flat",
            font=FONT_SMALL, padx=8, pady=6,
            activebackground=BORDER,
        ).pack(side="left", padx=(0, 4), pady=4)
        tk.Button(
            self.from_frame,
            text="Clear",
            command=lambda: self._clear_date(self.from_var, self.from_frame),
            bg=BG3, fg=FG2, relief="flat",
            font=FONT_SMALL, padx=8, pady=6,
            activebackground=BORDER,
        ).pack(side="left", padx=(0, 6), pady=4)

        tk.Label(from_col, text="Leave blank for earliest available",
                 bg=BG2, fg=FG3, font=FONT_SMALL).pack(anchor="w", pady=(3, 0))

        # To date
        to_col = tk.Frame(date_row, bg=BG2)
        to_col.pack(side="left", fill="x", expand=True)

        tk.Label(to_col, text="To Date  (dd/mm/yyyy)",
                 bg=BG2, fg=FG2, font=FONT_LABEL).pack(anchor="w", pady=(0, 3))

        self.to_var = tk.StringVar()
        self.to_frame = tk.Frame(
            to_col, bg=BG2,
            highlightthickness=1, highlightbackground=BORDER,
        )
        self.to_frame.pack(fill="x")
        self.to_entry = tk.Entry(
            self.to_frame,
            textvariable=self.to_var,
            bg=BG2, fg=FG, font=FONT_INPUT,
            relief="flat", insertbackground=FG, state="readonly",
            readonlybackground=BG2,
        )
        self.to_entry.pack(side="left", fill="x", expand=True, ipady=8, padx=(10, 6))
        self.to_entry.bind(
            "<Button-1>",
            lambda e: self._open_date_picker(self.to_var, self.to_frame, self.to_entry),
        )
        tk.Button(
            self.to_frame,
            text="Pick",
            command=lambda: self._open_date_picker(
                self.to_var, self.to_frame, self.to_entry
            ),
            bg=BG3, fg=FG2, relief="flat",
            font=FONT_SMALL, padx=8, pady=6,
            activebackground=BORDER,
        ).pack(side="left", padx=(0, 4), pady=4)
        tk.Button(
            self.to_frame,
            text="Clear",
            command=lambda: self._clear_date(self.to_var, self.to_frame),
            bg=BG3, fg=FG2, relief="flat",
            font=FONT_SMALL, padx=8, pady=6,
            activebackground=BORDER,
        ).pack(side="left", padx=(0, 6), pady=4)

        tk.Label(to_col, text="Leave blank for latest available",
                 bg=BG2, fg=FG3, font=FONT_SMALL).pack(anchor="w", pady=(3, 0))

    def _build_action_bar(self, parent):
        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x", pady=(0, 12))

        # Fetch button
        self.fetch_btn = tk.Button(
            bar,
            text="Search Data",
            command=self._fetch,
            bg=ACCENT, fg="white",
            font=FONT_BOLD,
            relief="flat", cursor="hand2",
            padx=24, pady=9,
            activebackground=ACCENT2, activeforeground="white",
        )
        self.fetch_btn.pack(side="left")

        # Export button (disabled until data fetched)
        self.export_btn = tk.Button(
            bar,
            text="Export to CSV",
            command=self._export,
            bg=SUCCESS, fg="white",
            font=FONT_BOLD,
            relief="flat", cursor="hand2",
            padx=24, pady=9,
            state="disabled",
            activebackground="#15803d", activeforeground="white",
        )
        self.export_btn.pack(side="left", padx=(10, 0))

        # Clear button
        tk.Button(
            bar,
            text="Clear",
            command=self._clear,
            bg=BG3, fg=FG2,
            font=FONT_LABEL,
            relief="flat", cursor="hand2",
            padx=16, pady=9,
            activebackground=BORDER,
        ).pack(side="left", padx=(10, 0))

        # Save location label
        self.save_label = tk.Label(
            bar,
            text=f"Save to: {self.save_dir}",
            bg=BG, fg=FG3, font=FONT_SMALL,
            anchor="e",
        )
        self.save_label.pack(side="right")

        tk.Button(
            bar,
            text="Change folder",
            command=self._change_save_dir,
            bg=BG3, fg=FG2,
            font=FONT_SMALL,
            relief="flat", cursor="hand2",
            padx=8, pady=4,
        ).pack(side="right", padx=(0, 8))

    def _build_status_bar(self, parent):
        self.status_var = tk.StringVar(value="Enter a stock symbol and click Search Data")
        self.status_lbl = tk.Label(
            parent,
            textvariable=self.status_var,
            bg=BG, fg=FG2,
            font=FONT_SMALL,
            anchor="w",
        )
        self.status_lbl.pack(fill="x", pady=(0, 8))

    def _build_preview(self, parent):
        tk.Label(
            parent, text="Data Preview",
            bg=BG, fg=FG, font=FONT_BOLD,
        ).pack(anchor="w", pady=(0, 6))

        preview_frame = tk.Frame(
            parent, bg=BG2,
            highlightthickness=1, highlightbackground=BORDER,
        )
        preview_frame.pack(fill="both", expand=True)

        # Treeview with scrollbars
        vsb = ttk.Scrollbar(preview_frame, orient="vertical")
        hsb = ttk.Scrollbar(preview_frame, orient="horizontal")

        self.tree = ttk.Treeview(
            preview_frame,
            yscrollcommand=vsb.set,
            xscrollcommand=hsb.set,
            show="headings",
            height=12,
        )

        vsb.config(command=self.tree.yview)
        hsb.config(command=self.tree.xview)

        vsb.pack(side="right",  fill="y")
        hsb.pack(side="bottom", fill="x")
        self.tree.pack(fill="both", expand=True)

        # Style the treeview
        style = ttk.Style()
        style.configure(
            "Treeview",
            background=BG2, foreground=FG,
            fieldbackground=BG2, rowheight=26,
            font=FONT_MONO,
        )
        style.configure(
            "Treeview.Heading",
            background=BG3, foreground=FG,
            font=FONT_BOLD, relief="flat",
        )
        style.map("Treeview", background=[("selected", ACCENT)])

        # Footer stats
        self.stats_var = tk.StringVar(value="")
        tk.Label(
            parent,
            textvariable=self.stats_var,
            bg=BG, fg=FG2, font=FONT_SMALL,
            anchor="w",
        ).pack(fill="x", pady=(6, 0))

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------

    def _highlight(self, frame):
        frame.config(highlightbackground=ACCENT, highlightthickness=2)

    def _unhighlight(self, frame):
        frame.config(highlightbackground=BORDER, highlightthickness=1)

    def _set_status(self, msg, color=FG2):
        self.status_var.set(msg)
        self.status_lbl.config(fg=color)

    def _clear_date(self, var, frame):
        var.set("")
        self._unhighlight(frame)

    def _open_date_picker(self, var, frame, entry_widget):
        """Open a popup picker and write dd/mm/yyyy to the target var."""
        self.date_picker_target = (var, frame)

        if var.get().strip():
            try:
                selected = datetime.strptime(var.get().strip(), DATE_FORMAT_DISPLAY)
            except ValueError:
                selected = datetime.today()
        else:
            selected = datetime.today()

        self.date_picker_month = selected.month
        self.date_picker_year = selected.year
        self.date_picker_day_var = tk.IntVar(value=selected.day)
        self.date_picker_month_var = tk.IntVar(value=selected.month)
        self.date_picker_year_var = tk.IntVar(value=selected.year)

        if self.date_picker and self.date_picker.winfo_exists():
            self.date_picker.destroy()

        popup = tk.Toplevel(self.root)
        popup.title("Select Date")
        popup.configure(bg=BG2)
        popup.resizable(False, False)
        popup.transient(self.root)
        popup.grab_set()
        self.date_picker = popup

        try:
            x = entry_widget.winfo_rootx()
            y = entry_widget.winfo_rooty() + entry_widget.winfo_height() + 6
            popup.geometry(f"+{x}+{y}")
        except Exception:
            pass

        self._render_date_picker()

    def _render_date_picker(self):
        if not self.date_picker or not self.date_picker.winfo_exists():
            return

        for widget in self.date_picker.winfo_children():
            widget.destroy()

        wrapper = tk.Frame(self.date_picker, bg=BG2, padx=12, pady=12)
        wrapper.pack(fill="both", expand=True)

        tk.Label(
            wrapper,
            text="Select Date",
            bg=BG2,
            fg=FG,
            font=FONT_BOLD,
        ).pack(anchor="w", pady=(0, 10))

        row = tk.Frame(wrapper, bg=BG2)
        row.pack(fill="x")

        day_box = ttk.Combobox(
            row,
            width=5,
            state="readonly",
            textvariable=self.date_picker_day_var,
            values=[f"{day:02d}" for day in range(1, 32)],
        )
        day_box.pack(side="left", padx=(0, 8))

        month_box = ttk.Combobox(
            row,
            width=10,
            state="readonly",
            textvariable=self.date_picker_month_var,
            values=[f"{month:02d}" for month in range(1, 13)],
        )
        month_box.pack(side="left", padx=(0, 8))
        month_box.bind("<<ComboboxSelected>>", lambda e: self._sync_picker_day_values())

        current_year = datetime.today().year
        year_box = ttk.Combobox(
            row,
            width=7,
            state="readonly",
            textvariable=self.date_picker_year_var,
            values=[str(year) for year in range(current_year - 15, current_year + 16)],
        )
        year_box.pack(side="left")
        year_box.bind("<<ComboboxSelected>>", lambda e: self._sync_picker_day_values())

        hint = tk.Label(
            wrapper,
            text="Format: dd/mm/yyyy",
            bg=BG2,
            fg=FG3,
            font=FONT_SMALL,
        )
        hint.pack(anchor="w", pady=(8, 10))

        footer = tk.Frame(wrapper, bg=BG2)
        footer.pack(fill="x")
        tk.Button(
            footer,
            text="Today",
            command=self._select_today_from_picker,
            bg=ACCENT,
            fg="white",
            relief="flat",
            font=FONT_SMALL,
            padx=8,
            pady=5,
        ).pack(side="left")
        tk.Button(
            footer,
            text="Apply",
            command=self._apply_date_picker,
            bg=SUCCESS,
            fg="white",
            relief="flat",
            font=FONT_SMALL,
            padx=8,
            pady=5,
        ).pack(side="right", padx=(8, 0))
        tk.Button(
            footer,
            text="Cancel",
            command=self._close_date_picker,
            bg=BG3,
            fg=FG2,
            relief="flat",
            font=FONT_SMALL,
            padx=8,
            pady=5,
        ).pack(side="right")

        self._sync_picker_day_values()

    def _sync_picker_day_values(self):
        month = int(self.date_picker_month_var.get())
        year = int(self.date_picker_year_var.get())
        max_day = calendar.monthrange(year, month)[1]
        valid_days = [f"{day:02d}" for day in range(1, max_day + 1)]

        current_day = int(self.date_picker_day_var.get())
        if current_day > max_day:
            self.date_picker_day_var.set(max_day)

        for child in self.date_picker.winfo_children():
            for widget in child.winfo_children():
                if isinstance(widget, ttk.Combobox) and str(widget.cget("width")) == "5":
                    widget.configure(values=valid_days)
                    break

    def _select_today_from_picker(self):
        today = datetime.today()
        self.date_picker_day_var.set(today.day)
        self.date_picker_month_var.set(today.month)
        self.date_picker_year_var.set(today.year)
        self._sync_picker_day_values()

    def _apply_date_picker(self):
        selected = datetime(
            int(self.date_picker_year_var.get()),
            int(self.date_picker_month_var.get()),
            int(self.date_picker_day_var.get()),
        )
        if self.date_picker_target:
            var, frame = self.date_picker_target
            var.set(selected.strftime(DATE_FORMAT_DISPLAY))
            frame.config(highlightbackground=SUCCESS, highlightthickness=2)
        self._close_date_picker()

    def _close_date_picker(self):
        if self.date_picker and self.date_picker.winfo_exists():
            self.date_picker.destroy()
        self.date_picker = None
        self.date_picker_target = None

    def _validate_date_field(self, var, frame):
        """Validates dd/mm/yyyy format. Highlights red if invalid."""
        val = var.get().strip()
        self._unhighlight(frame)
        if not val:
            return True
        try:
            datetime.strptime(val, DATE_FORMAT_DISPLAY)
            frame.config(highlightbackground=SUCCESS)
            return True
        except ValueError:
            frame.config(highlightbackground=DANGER, highlightthickness=2)
            self._set_status(
                f"Invalid date '{val}' - use dd/mm/yyyy format", DANGER
            )
            return False

    def _parse_date(self, var, label):
        """Returns YYYY-MM-DD if valid, None if blank, raises on bad format."""
        val = var.get().strip()
        if not val:
            return None
        try:
            return self._parse_display_date(val)
        except ValueError:
            raise ValueError(f"{label} must be in dd/mm/yyyy format.")

    # ------------------------------------------------------------------
    # AUTOCOMPLETE
    # ------------------------------------------------------------------

    def _on_symbol_change(self, *args):
        global SYMBOL_CACHE

        search = self.symbol_var.get().strip()
        self.symbol_info.config(text="")

        if len(search) < 1:
            self.suggest_frame.pack_forget()
            return

        if not self.symbols_loaded:
            self._preload_symbols_async()
            self.suggest_frame.pack_forget()
            self._set_status("Loading symbol list...", FG2)
            return

        symbols = SYMBOL_CACHE or []
        prefix = search.upper()
        matches = [sym for sym in symbols if sym.startswith(prefix)][:20]

        if not matches:
            self.suggest_frame.pack_forget()
            return

        self.suggest_lb.delete(0, "end")
        for sym in matches:
            self.suggest_lb.insert("end", sym)

        self.suggest_frame.pack(fill="x", pady=(2, 0))

    def _on_suggest_select(self, event=None):
        sel = self.suggest_lb.curselection()
        if not sel:
            return
        symbol = self.suggest_lb.get(sel[0])
        self.symbol_var.set(symbol)
        self.suggest_frame.pack_forget()

        # Show available date range
        min_d, max_d = get_date_range_for_symbol(symbol)
        if min_d and max_d:
            self.symbol_info.config(
                text=(
                    f"Available: {self._format_display_date(min_d)}  to  "
                    f"{self._format_display_date(max_d)}"
                ),
                fg=FG3,
            )

    # ------------------------------------------------------------------
    # FETCH
    # ------------------------------------------------------------------

    def _fetch(self):
        symbol = self.symbol_var.get().strip().upper()
        self.suggest_frame.pack_forget()

        if not symbol:
            self._set_status("Please enter a stock symbol.", DANGER)
            self.symbol_entry.focus()
            return

        try:
            from_date = self._parse_date(self.from_var, "From Date")
            to_date   = self._parse_date(self.to_var,   "To Date")
        except ValueError as e:
            self._set_status(str(e), DANGER)
            return

        # Validate date order
        if from_date and to_date and from_date > to_date:
            self._set_status("From Date must be before To Date.", DANGER)
            return

        self._set_status(f"Fetching data for {symbol} ...", FG2)
        self.root.update()

        try:
            df, error = fetch_stock_data(symbol, from_date, to_date)
        except Exception as exc:
            self.df_result = None
            self.export_btn.config(state="disabled")
            self._clear_preview()
            self._set_status(f"Search failed: {exc}", DANGER)
            return

        if error:
            self.df_result = None
            self._set_status(error, DANGER)
            self.export_btn.config(state="disabled")
            self._clear_preview()
            return

        self.df_result = df
        self._populate_preview(df)
        self.export_btn.config(state="normal")

        date_range = (
            f"{self._format_display_date(df['date'].min())}  to  "
            f"{self._format_display_date(df['date'].max())}"
            if not df.empty else ""
        )
        self._set_status(
            f"Found {len(df):,} rows for {symbol}  ({date_range})",
            SUCCESS,
        )

    # ------------------------------------------------------------------
    # PREVIEW
    # ------------------------------------------------------------------

    def _clear_preview(self):
        self.tree.delete(*self.tree.get_children())
        self.tree["columns"] = []
        self.stats_var.set("")

    def _populate_preview(self, df):
        import pandas as pd

        self._clear_preview()

        cols = list(df.columns)
        self.tree["columns"] = cols

        col_widths = {
            "symbol": 90, "company_name": 160, "isin": 110,
            "segment": 65, "instrument_type": 80,
            "date": 90, "open": 75, "high": 75,
            "low": 75, "close": 75, "volume": 90,
        }

        for col in cols:
            w = col_widths.get(col, 90)
            self.tree.heading(col, text=col.upper())
            self.tree.column(col, width=w, anchor="e" if col not in
                             ("symbol", "company_name", "isin", "segment",
                              "instrument_type", "date") else "w",
                             minwidth=50)

        # Show first 200 rows in preview
        preview = df.head(200)
        for _, row in preview.iterrows():
            vals = []
            for col in cols:
                v = row[col]
                if col in ("open", "high", "low", "close") and pd.notna(v):
                    vals.append(f"{float(v):,.2f}")
                elif col == "volume" and pd.notna(v):
                    vals.append(f"{int(v):,}")
                else:
                    vals.append(str(v) if pd.notna(v) else "")
            self.tree.insert("", "end", values=vals)

        total = len(df)
        shown = min(200, total)
        extra = f"  (showing first {shown} of {total:,})" if total > 200 else ""
        self.stats_var.set(
            f"{total:,} rows  |  "
            f"{df['date'].min()} to {df['date'].max()}"
            f"{extra}"
        )

    # ------------------------------------------------------------------
    # EXPORT
    # ------------------------------------------------------------------

    def _export(self):
        if self.df_result is None or self.df_result.empty:
            self._set_status("No data to export. Search first.", DANGER)
            return

        symbol   = self.symbol_var.get().strip().upper()
        filename = f"{symbol}.csv"
        out_path = self.save_dir / filename

        try:
            self.df_result.to_csv(out_path, index=False)
            self._set_status(
                f"Exported {len(self.df_result):,} rows to  {out_path}",
                SUCCESS,
            )
            import logging
            logging.getLogger(__name__).info(f"Exported {symbol} -> {out_path}")

            # Show confirmation popup
            messagebox.showinfo(
                "Export Complete",
                f"File saved successfully!\n\n"
                f"File   : {filename}\n"
                f"Rows   : {len(self.df_result):,}\n"
                f"Folder : {self.save_dir}",
            )

        except Exception as exc:
            self._set_status(f"Export failed: {exc}", DANGER)
            import logging
            logging.getLogger(__name__).error(f"Export failed: {exc}")

    # ------------------------------------------------------------------
    # CHANGE SAVE DIR
    # ------------------------------------------------------------------

    def _change_save_dir(self):
        folder = filedialog.askdirectory(
            title="Select folder to save CSV files",
            initialdir=str(self.save_dir),
        )
        if folder:
            self.save_dir = Path(folder)
            self.save_label.config(
                text=f"Save to: {self.save_dir}"
            )

    # ------------------------------------------------------------------
    # CLEAR
    # ------------------------------------------------------------------

    def _clear(self):
        self.symbol_var.set("")
        self.from_var.set("")
        self.to_var.set("")
        self.df_result = None
        self.export_btn.config(state="disabled")
        self.suggest_frame.pack_forget()
        self.symbol_info.config(text="")
        self._clear_preview()
        self._set_status("Enter a stock symbol and click Search Data", FG2)
        self._unhighlight(self.from_frame)
        self._unhighlight(self.to_frame)


# ===========================================================================
# ENTRY POINT
# ===========================================================================

def main():
    root = tk.Tk()
    try:
        root.iconbitmap(default="")
    except Exception:
        pass

    style = ttk.Style()
    style.theme_use("clam")
    style.configure(
        "Vertical.TScrollbar",
        background=BG3, troughcolor=BG2,
        bordercolor=BORDER, arrowcolor=FG3, relief="flat",
    )
    style.configure(
        "Horizontal.TScrollbar",
        background=BG3, troughcolor=BG2,
        bordercolor=BORDER, arrowcolor=FG3, relief="flat",
    )

    StockExporterGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
