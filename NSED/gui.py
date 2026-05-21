"""
gui.py -- Zerodha NSE Data Manager
=====================================
Desktop GUI to run all data management tasks without the terminal.

Features:
  - Run all scripts with one button click
  - Live scrolling log with colour coding
  - Batched log updates -- no GUI freeze during long tasks
  - Progress bar for downloads
  - Merged Live / Historical Sharpe Screener
  - Token management with validation
  - DB stats panel
  - Daily routine button

Usage:
    python gui.py

Requirements:
    tkinter -- built into Python, no install needed
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import subprocess
import threading
import queue
import re
import sys
import os
import time
from datetime import datetime
from pathlib import Path

# Heavy imports (db, pandas) loaded lazily inside functions
# so the window appears instantly

# ===========================================================================
# PATHS & PYTHON DETECTION
# ===========================================================================

BASE_DIR    = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.py"


def _find_python():
    """
    Returns the correct Python executable for running scripts.
    Checks common venv names on both Windows and Linux/Mac.
    Falls back to sys.executable if no venv found.
    """
    venv_names = [".venv", "venv", "env", ".env"]
    bin_paths  = (
        ["Scripts/python.exe", "Scripts/python3.exe"]
        if sys.platform == "win32"
        else ["bin/python", "bin/python3"]
    )
    for name in venv_names:
        for bp in bin_paths:
            candidate = BASE_DIR / name / bp
            if candidate.exists():
                return str(candidate)
    return sys.executable


PYTHON = _find_python()

# ===========================================================================
# COLOURS & FONTS
# ===========================================================================

BG      = "#1e1e2e"
BG2     = "#2a2a3e"
BG3     = "#313145"
ACCENT  = "#7c6af7"
ACCENT2 = "#5a9cf8"
SUCCESS = "#4caf7d"
WARNING = "#f0a500"
DANGER  = "#e05c5c"
FG      = "#e0e0f0"
FG2     = "#9999bb"
FG3     = "#6666aa"
BORDER  = "#4a4a6a"

FONT_MAIN  = ("Segoe UI", 10)
FONT_BOLD  = ("Segoe UI", 10, "bold")
FONT_SMALL = ("Segoe UI", 8)
FONT_MONO  = ("Consolas", 9)
FONT_INPUT = ("Segoe UI", 11)

# ===========================================================================
# TASK DEFINITIONS
# ===========================================================================

TASKS = [
    {
        "id":     "downloader",
        "label":  "Daily EOD Download",
        "desc":   "Download today's EOD data for all NSE stocks",
        "script": "downloader.py",
        "args":   [],
        "icon":   "DL",
        "color":  ACCENT2,
        "daily":  True,
    },
    {
        "id":     "asm",
        "label":  "ASM / BE Handler",
        "desc":   "Detect and fix T2T/ASM series transitions",
        "script": "asm_be_handler.py",
        "args":   [],
        "icon":   "BE",
        "color":  WARNING,
        "daily":  True,
    },
    {
        "id":     "corporate",
        "label":  "Corporate Actions",
        "desc":   "Fetch splits/bonus and refresh affected prices",
        "script": "corporate_actions.py",
        "args":   [],
        "icon":   "CA",
        "color":  ACCENT,
        "daily":  True,
    },
    {
        "id":     "symbol",
        "label":  "Symbol Change Handler",
        "desc":   "Detect renamed or delisted symbols (interactive)",
        "script": "symbol_change_handler.py",
        "args":   [],
        "icon":   "SY",
        "color":  WARNING,
        "daily":  False,
    },
    {
        "id":     "marketcap",
        "label":  "Fetch Market Cap Data",
        "desc":   "Download shares outstanding from Yahoo Finance",
        "script": "marketcap.py",
        "args":   ["--fetch"],
        "icon":   "MC",
        "color":  SUCCESS,
        "daily":  False,
    },
    {
        "id":     "exporter",
        "label":  "Stock Data Exporter",
        "desc":   "Export EOD data for a specific stock to CSV",
        "script": "stock_exporter.py",
        "args":   [],
        "icon":   "EX",
        "color":  ACCENT2,
        "daily":  False,
    },
]

# ===========================================================================
# CONFIG HELPERS
# ===========================================================================

def read_config():
    """Reads key=value pairs from config.py into a dict."""
    config = {}
    if not CONFIG_FILE.exists():
        return config
    with open(CONFIG_FILE, encoding="utf-8") as f:
        for line in f:
            m = re.match(
                r'^(\w+)\s*=\s*(?:["\']([^"\']*)["\']|(True|False|\d+))',
                line.strip()
            )
            if m:
                config[m.group(1)] = m.group(2) or m.group(3)
    return config


def write_config_value(key, value):
    """Updates a single key in config.py in-place. Returns True if saved."""
    if not CONFIG_FILE.exists():
        return False
    with open(CONFIG_FILE, encoding="utf-8") as f:
        lines = f.readlines()
    new_lines = []
    replaced  = False
    for line in lines:
        if re.match(rf'^{key}\s*=', line):
            if value in ("True", "False") or str(value).isdigit():
                new_lines.append(f'{key} = {value}\n')
            else:
                new_lines.append(f'{key} = "{value}"\n')
            replaced = True
        else:
            new_lines.append(line)
    if replaced:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        return True
    return False


def verify_config_saved(key, expected):
    """Reads config.py back to confirm value was saved correctly."""
    return read_config().get(key, "") == expected

# ===========================================================================
# TOOLTIP CLASS
# ===========================================================================

class Tooltip:
    """Creates a hover tooltip for any Tkinter widget."""
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tooltip_window = None
        self.widget.bind("<Enter>", self.show_tooltip)
        self.widget.bind("<Leave>", self.hide_tooltip)

    def show_tooltip(self, event=None):
        if self.tooltip_window or not self.text:
            return
        x, y, _, _ = self.widget.bbox("insert")
        x += self.widget.winfo_rootx() + 25
        y += self.widget.winfo_rooty() + 25
        
        # Create a borderless top-level window
        self.tooltip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        
        label = tk.Label(
            tw, text=self.text, justify="left",
            bg="#2a2a3e", fg="#e0e0f0", relief="solid", borderwidth=1,
            font=("Segoe UI", 9)
        )
        label.pack(ipadx=6, ipady=3)

    def hide_tooltip(self, event=None):
        if self.tooltip_window:
            self.tooltip_window.destroy()
            self.tooltip_window = None

# ===========================================================================
# MAIN GUI CLASS
# ===========================================================================

class ZerodhaGUI:

    def __init__(self, root):
        self.root        = root
        self.root.title("Zerodha NSE Data Manager")
        self.root.configure(bg=BG)
        self.root.geometry("1100x760")
        self.root.minsize(900, 640)

        self.running     = False
        self.status_vars = {}
        self.btn_refs    = {}
        self.task_option_vars = {}
        self.ui_ready     = False

        self.loading_frame = tk.Frame(self.root, bg=BG)
        self.loading_frame.pack(fill="both", expand=True)
        tk.Label(
            self.loading_frame,
            text="Loading Zerodha NSE Data Manager...",
            bg=BG,
            fg=FG,
            font=("Segoe UI", 12, "bold"),
        ).pack(expand=True)

        # Build the full widget tree after the first paint so the window
        # appears immediately instead of staying in a busy state.
        self.root.after_idle(self._finish_startup)

    def _finish_startup(self):
        if self.ui_ready:
            return
        self.ui_ready = True
        self.loading_frame.destroy()
        self._build_ui()
        self.root.after(0, self._refresh_config_panel)

        # Defer heavy DB stats load -- avoids freezing at startup
        self.root.after(400, self._refresh_stats)

    # ------------------------------------------------------------------
    # UI STRUCTURE
    # ------------------------------------------------------------------

    def _build_ui(self):
        # ---- Header ----
        hdr = tk.Frame(self.root, bg=ACCENT, height=52)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        tk.Label(
            hdr, text="  Zerodha NSE Data Manager",
            bg=ACCENT, fg="white",
            font=("Segoe UI", 14, "bold"),
        ).pack(side="left", padx=16, pady=12)

        tk.Label(
            hdr, text=f"Python: {Path(PYTHON).name}",
            bg=ACCENT, fg="#c0b8f8",
            font=FONT_SMALL,
        ).pack(side="left", padx=(0, 20), pady=14)

        self.clock_lbl = tk.Label(
            hdr, text="", bg=ACCENT, fg="#ddd", font=FONT_MAIN
        )
        self.clock_lbl.pack(side="right", padx=20)
        self._tick_clock()

        # ---- Paned layout ----
        paned = tk.PanedWindow(
            self.root, orient="horizontal",
            bg=BG, sashwidth=4, sashrelief="flat",
        )
        paned.pack(fill="both", expand=True)

        left  = tk.Frame(paned, bg=BG, width=450)
        right = tk.Frame(paned, bg=BG2)
        paned.add(left,  minsize=380)
        paned.add(right, minsize=320)

        self._build_left(left)
        self._build_right(right)

    def _build_left(self, parent):
        canvas = tk.Canvas(parent, bg=BG, highlightthickness=0)
        sb     = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg=BG)
        win   = canvas.create_window((0, 0), window=inner, anchor="nw")

        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfig(win, width=e.width)
        )
        inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.bind_all(
            "<MouseWheel>",
            lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units")
        )
        self.left_canvas = canvas
        self.left_inner = inner
        self.left_loading_label = tk.Label(
            inner,
            text="Building controls...",
            bg=BG,
            fg=FG2,
            font=FONT_SMALL,
        )
        self.left_loading_label.pack(anchor="w", padx=16, pady=(16, 8))
        self.root.after(0, self._build_left_sections_stepwise)

    def _build_left_sections_stepwise(self):
        """Build left-panel sections in small chunks to reduce startup lag."""
        if getattr(self, "left_loading_label", None) is not None:
            self.left_loading_label.destroy()
            self.left_loading_label = None

        steps = [
            self._build_config_section,
            self._build_daily_section,
            self._build_tasks_section,
            self._build_sharpe_section,
            self._build_stats_section,
        ]

        def build_step(index=0):
            if index >= len(steps):
                self.left_canvas.configure(
                    scrollregion=self.left_canvas.bbox("all")
                )
                return

            steps[index](self.left_inner)
            self.left_canvas.configure(
                scrollregion=self.left_canvas.bbox("all")
            )
            self.root.after(10, lambda: build_step(index + 1))

        build_step()

    # ------------------------------------------------------------------
    # SECTION HELPERS
    # ------------------------------------------------------------------

    def _section_label(self, parent, text):
        f = tk.Frame(parent, bg=BG)
        f.pack(fill="x", padx=16, pady=(14, 4))
        tk.Label(
            f, text=text, bg=BG, fg=FG2, font=FONT_SMALL
        ).pack(side="left")
        tk.Frame(f, bg=BG3, height=1).pack(
            side="left", fill="x", expand=True, padx=(8, 0), pady=6
        )

    def _card(self, parent):
        c = tk.Frame(parent, bg=BG2)
        c.pack(fill="x", padx=16, pady=3)
        return c

    def _highlight(self, frame, color=ACCENT):
        frame.config(highlightbackground=color, highlightthickness=2)

    def _unhighlight(self, frame):
        frame.config(highlightbackground=BORDER, highlightthickness=1)

    def _entry(self, parent, var, show="", width=0, font=FONT_MONO):
        """Creates a styled entry widget with highlight border."""
        frame = tk.Frame(
            parent, bg=BG2,
            highlightthickness=1, highlightbackground=BORDER,
        )
        kwargs = dict(
            textvariable=var, bg=BG3, fg=FG,
            insertbackground=FG, relief="flat", font=font,
        )
        if show:
            kwargs["show"] = show
        if width:
            kwargs["width"] = width
        e = tk.Entry(frame, **kwargs)
        e.pack(fill="x", ipady=7, padx=8)
        e.bind("<FocusIn>",  lambda ev: self._highlight(frame))
        e.bind("<FocusOut>", lambda ev: self._unhighlight(frame))
        return frame, e

    def _btn(self, parent, text, command, bg=ACCENT, fg="white",
             font=FONT_BOLD, padx=16, pady=7, side="left", px=0):
        b = tk.Button(
            parent, text=text, command=command,
            bg=bg, fg=fg, relief="flat", font=font,
            cursor="hand2", padx=padx, pady=pady,
            activebackground=BG3, activeforeground=fg,
        )
        b.pack(side=side, padx=px)
        return b

    def _task_icon_text(self, task):
        """Return a short ASCII badge for task cards."""
        return {
            "downloader": "DL",
            "asm": "BE",
            "corporate": "CA",
            "symbol": "SY",
            "marketcap": "MC",
            "exporter": "EX",
        }.get(task.get("id", ""), "TK")

    def _resolve_task(self, task):
        """Return a runnable copy of a task after applying UI options."""
        resolved = dict(task)
        resolved["args"] = list(task.get("args", []))

        if task.get("id") == "corporate":
            preview_var = self.task_option_vars.get("corporate_preview")
            if preview_var is not None and preview_var.get():
                resolved["args"].append("--dry-run")
                resolved["label"] = "Corporate Actions (Preview)"

        return resolved

    # ------------------------------------------------------------------
    # CONFIG SECTION
    # ------------------------------------------------------------------

    def _build_config_section(self, parent):
        self._section_label(parent, "ACCESS TOKEN")
        card  = self._card(parent)
        inner = tk.Frame(card, bg=BG2, padx=12, pady=12)
        inner.pack(fill="x")

        # Step 1 -- Open login URL
        tk.Label(
            inner, text="Step 1 -- Open Zerodha login in browser:",
            bg=BG2, fg=FG2, font=FONT_SMALL,
        ).pack(anchor="w", pady=(0, 4))

        # FIX: Create a dedicated frame for the button to prevent Tkinter packer collision
        row1 = tk.Frame(inner, bg=BG2)
        row1.pack(fill="x", pady=(0, 6))

        self._btn(
            row1, "Open Zerodha Login URL",
            self._open_login_url,
            bg=ACCENT2, padx=14, pady=6,
        )

        # Step 2 -- Paste request_token
        tk.Label(
            inner, text="Step 2 -- Paste request_token from redirect URL:",
            bg=BG2, fg=FG2, font=FONT_SMALL,
        ).pack(anchor="w", pady=(10, 2))
        
        tk.Label(
            inner,
            text="(copy the short token after ?request_token= in the browser URL)",
            bg=BG2, fg=FG3, font=("Segoe UI", 7),
        ).pack(anchor="w", pady=(0, 4))

        row2 = tk.Frame(inner, bg=BG2)
        row2.pack(fill="x", pady=(0, 4))

        self.req_token_var = tk.StringVar()
        ef, self.req_entry = self._entry(row2, self.req_token_var, width=30)
        ef.pack(side="left", fill="x", expand=True, padx=(0, 6))

        # Placeholder
        self.req_token_var.set("Paste request_token here...")
        self.req_entry.config(fg=FG3)
        self.req_entry.bind("<FocusIn>",  self._req_focus_in)
        self.req_entry.bind("<FocusOut>", self._req_focus_out)

        get_token_btn = self._btn(
            row2, "Get Token",
            self._exchange_token,
            bg=SUCCESS, padx=12, pady=6,
        )
        # Attach the tooltip
        Tooltip(get_token_btn, "Get Access Token")

        # Step 3 -- Access token display (read-only)
        tk.Label(
            inner,
            text="Step 3 -- Access Token (auto-saved, read-only):",
            bg=BG2, fg=FG2, font=FONT_SMALL,
        ).pack(anchor="w", pady=(10, 2))

        self.token_var = tk.StringVar()
        row3 = tk.Frame(inner, bg=BG2)
        row3.pack(fill="x", pady=(0, 4))

        token_frame = tk.Frame(
            row3, bg=BG2,
            highlightthickness=1, highlightbackground=BORDER,
        )
        token_frame.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.token_entry = tk.Entry(
            token_frame,
            textvariable=self.token_var,
            bg=BG3, fg=FG2,
            font=FONT_MONO,
            relief="flat",
            state="readonly",
            show="•",
        )
        self.token_entry.pack(fill="x", ipady=7, padx=8)

        self.show_token = False
        def toggle_show():
            self.show_token = not self.show_token
            self.token_entry.config(show="" if self.show_token else "•")
            show_btn.config(text="Hide" if self.show_token else "Show")
        show_btn = self._btn(
            row3, "Show", toggle_show,
            bg=BG3, fg=FG2, font=FONT_SMALL,
            padx=10, pady=6,
        )

        self.config_status = tk.Label(
            inner, text="", bg=BG2, fg=SUCCESS, font=FONT_SMALL
        )
        self.config_status.pack(anchor="w", pady=(4, 0))

    def _req_focus_in(self, e=None):
        if self.req_token_var.get() == "Paste request_token here...":
            self.req_token_var.set("")
            self.req_entry.config(fg=FG)

    def _req_focus_out(self, e=None):
        if not self.req_token_var.get().strip():
            self.req_token_var.set("Paste request_token here...")
            self.req_entry.config(fg=FG3)

    def _open_login_url(self):
        cfg = read_config()
        api_key = cfg.get("API_KEY", "")
        if not api_key or "your_api_key" in api_key:
            self.config_status.config(
                text="Set API_KEY in config.py first", fg=DANGER
            )
            self.root.after(3000, lambda: self.config_status.config(text=""))
            return
        import webbrowser
        url = (
            f"https://kite.zerodha.com/connect/login"
            f"?v=3&api_key={api_key}"
        )
        webbrowser.open(url)
        self.config_status.config(
            text="Browser opened -- log in and paste request_token below",
            fg=FG2
        )

    def _exchange_token(self):
        req_token = self.req_token_var.get().strip()

        if not req_token or req_token == "Paste request_token here...":
            self.config_status.config(
                text="Paste request_token first", fg=DANGER
            )
            self.root.after(3000, lambda: self.config_status.config(text=""))
            return

        if len(req_token) > 60:
            self.config_status.config(
                text="This looks like an ACCESS TOKEN not a REQUEST TOKEN. "
                     "Paste the short token from the redirect URL.",
                fg=DANGER
            )
            self.root.after(5000, lambda: self.config_status.config(text=""))
            return

        self.config_status.config(text="Exchanging token...", fg=WARNING)
        self.root.update_idletasks()

        def do_exchange():
            try:
                # Import lazily -- avoids slowing GUI startup
                from kiteconnect import KiteConnect
                cfg        = read_config()
                api_key    = cfg.get("API_KEY", "")
                api_secret = cfg.get("API_SECRET", "")

                if not api_key or not api_secret:
                    self.root.after(0, self.config_status.config,
                                    {"text": "API_KEY or API_SECRET missing in config.py",
                                     "fg": DANGER})
                    return

                kite         = KiteConnect(api_key=api_key)
                session_data = kite.generate_session(
                    req_token, api_secret=api_secret
                )
                access_token = session_data["access_token"]

                # Save to config.py
                saved = write_config_value("ACCESS_TOKEN", access_token)

                def update_ui():
                    if saved:
                        self.token_var.set(access_token)
                        self.config_status.config(
                            text="Access token saved to config.py ✓",
                            fg=SUCCESS
                        )
                        self._log("Access token generated and saved.", "success")
                        self._log(f"  Config: {CONFIG_FILE}", "muted")
                        # Clear request token field
                        self.req_token_var.set("Paste request_token here...")
                        self.req_entry.config(fg=FG3)
                    else:
                        self.config_status.config(
                            text="Token generated but FAILED to save to config.py",
                            fg=DANGER
                        )
                        self._log(
                            f"WARNING: Save failed. "
                            f"Manually update config.py:\n"
                            f"  ACCESS_TOKEN = \"{access_token}\"",
                            "warning"
                        )
                    self.root.after(5000, lambda: self.config_status.config(text=""))

                self.root.after(0, update_ui)

            except Exception as exc:
                def show_err():
                    self.config_status.config(
                        text=f"Exchange failed: {exc}", fg=DANGER
                    )
                    self._log(f"Token exchange error: {exc}", "error")
                    self.root.after(5000, lambda: self.config_status.config(text=""))
                self.root.after(0, show_err)

        threading.Thread(target=do_exchange, daemon=True).start()

    # ------------------------------------------------------------------
    # DAILY ROUTINE
    # ------------------------------------------------------------------

    def _build_daily_section(self, parent):
        """Daily routine card."""
        self._section_label(parent, "DAILY ROUTINE")
        card  = self._card(parent)
        inner = tk.Frame(card, bg=BG2, padx=12, pady=12)
        inner.pack(fill="x")

        tk.Label(
            inner,
            text="Runs: EOD Download -> ASM Handler -> Corporate Actions",
            bg=BG2, fg=FG2, font=FONT_SMALL,
        ).pack(anchor="w", pady=(0, 8))

        tk.Button(
            inner, text="Run Daily Routine",
            command=self._run_daily_routine,
            bg=SUCCESS, fg="white", relief="flat",
            font=FONT_BOLD, cursor="hand2",
            padx=20, pady=8,
            activebackground="#3d9966",
        ).pack(fill="x")

    # ------------------------------------------------------------------
    # INDIVIDUAL TASKS
    # ------------------------------------------------------------------

    def _build_tasks_section(self, parent):
        self._section_label(parent, "INDIVIDUAL TASKS")
        holder = tk.Frame(parent, bg=BG)
        holder.pack(fill="x")

        def build_task(index=0):
            if index >= len(TASKS):
                return
            self._task_card(holder, TASKS[index])
            self.root.after(5, lambda: build_task(index + 1))

        build_task()

    def _task_card(self, parent, task):
        card = tk.Frame(parent, bg=BG2)
        card.pack(fill="x", padx=16, pady=3)

        top = tk.Frame(card, bg=BG2)
        top.pack(fill="x", padx=12, pady=(10, 2))

        tk.Label(
            top, text=self._task_icon_text(task),
            bg=BG2, fg=task["color"],
            font=("Segoe UI", 13),
        ).pack(side="left", padx=(0, 8))

        info = tk.Frame(top, bg=BG2)
        info.pack(side="left", fill="x", expand=True)
        tk.Label(info, text=task["label"], bg=BG2, fg=FG,
                 font=FONT_BOLD).pack(anchor="w")
        tk.Label(info, text=task["desc"], bg=BG2, fg=FG2,
                 font=FONT_SMALL).pack(anchor="w")

        if task.get("id") == "corporate":
            preview_var = tk.BooleanVar(value=False)
            self.task_option_vars["corporate_preview"] = preview_var
            tk.Checkbutton(
                info,
                text="Only Preview",
                variable=preview_var,
                onvalue=True,
                offvalue=False,
                bg=BG2,
                fg=FG2,
                selectcolor=BG3,
                activebackground=BG2,
                activeforeground=FG,
                font=FONT_SMALL,
            ).pack(anchor="w", pady=(4, 0))

        sv = tk.StringVar(value="Idle")
        self.status_vars[task["id"]] = sv
        tk.Label(
            top, textvariable=sv, bg=BG2, fg=FG3,
            font=FONT_SMALL, width=10, anchor="e",
        ).pack(side="right")

        btn = tk.Button(
            card, text="Run",
            command=lambda t=task: self._run_task(self._resolve_task(t)),
            bg=task["color"], fg="white", relief="flat",
            font=FONT_SMALL, cursor="hand2",
            padx=12, pady=4,
            activebackground=BG3,
        )
        btn.pack(anchor="e", padx=12, pady=(2, 10))
        self.btn_refs[task["id"]] = btn

    # ------------------------------------------------------------------
    # SHARPE SCREENER (merged Live + Historical)
    # ------------------------------------------------------------------

    def _build_sharpe_section(self, parent):
        self._section_label(parent, "SHARPE SCREENER")
        card  = self._card(parent)
        inner = tk.Frame(card, bg=BG2, padx=12, pady=12)
        inner.pack(fill="x")

        # Mode radio buttons
        self.sharpe_mode = tk.StringVar(value="live")
        mode_row = tk.Frame(inner, bg=BG2)
        mode_row.pack(fill="x", pady=(0, 8))

        for text, val in [("Live (latest data)", "live"),
                          ("Historical (as-of date)", "historical")]:
            tk.Radiobutton(
                mode_row, text=text,
                variable=self.sharpe_mode, value=val,
                bg=BG2, fg=FG, selectcolor=BG3,
                activebackground=BG2, activeforeground=FG,
                font=FONT_MAIN,
                command=self._on_sharpe_mode_change,
            ).pack(side="left", padx=(0, 16))

        # Date input row -- hidden until historical selected
        self.sharpe_date_row = tk.Frame(inner, bg=BG2)

        tk.Label(
            self.sharpe_date_row,
            text="As-of Date (dd/mm/yyyy):",
            bg=BG2, fg=FG2, font=FONT_MAIN,
        ).pack(side="left", padx=(0, 8))

        self.sharpe_date_var = tk.StringVar(
            value=datetime.today().replace(day=1).strftime("%d/%m/%Y")
        )
        df, self.sharpe_date_entry = self._entry(
            self.sharpe_date_row, self.sharpe_date_var, width=14
        )
        df.pack(side="left")
        self.sharpe_date_entry.bind(
            "<FocusOut>", lambda e: self._validate_sharpe_date()
        )

        # Sharpe window inputs
        self.sharpe_window_row = tk.Frame(inner, bg=BG2)
        self.sharpe_window_row.pack(fill="x", pady=(0, 6))

        tk.Label(
            self.sharpe_window_row,
            text="Sharpe Windows (months):",
            bg=BG2, fg=FG2, font=FONT_MAIN,
        ).pack(side="left", padx=(0, 8))

        self.sharpe_long_months_var = tk.StringVar(value="6")
        long_frame, self.sharpe_long_months_entry = self._entry(
            self.sharpe_window_row,
            self.sharpe_long_months_var,
            width=5,
            font=FONT_MAIN,
        )
        long_frame.pack(side="left")
        self.sharpe_long_months_entry.bind(
            "<FocusOut>", lambda e: self._validate_sharpe_windows()
        )

        tk.Label(
            self.sharpe_window_row,
            text="and",
            bg=BG2, fg=FG2, font=FONT_MAIN,
        ).pack(side="left", padx=8)

        self.sharpe_short_months_var = tk.StringVar(value="3")
        short_frame, self.sharpe_short_months_entry = self._entry(
            self.sharpe_window_row,
            self.sharpe_short_months_var,
            width=5,
            font=FONT_MAIN,
        )
        short_frame.pack(side="left")
        self.sharpe_short_months_entry.bind(
            "<FocusOut>", lambda e: self._validate_sharpe_windows()
        )

        tk.Label(
            self.sharpe_window_row,
            text="months",
            bg=BG2, fg=FG3, font=FONT_SMALL,
        ).pack(side="left", padx=(8, 0))

        # Status label
        self.sharpe_status = tk.Label(
            inner, text="", bg=BG2, fg=FG3, font=FONT_SMALL
        )
        self.sharpe_status.pack(anchor="w", pady=(2, 6))

        # Buttons
        btn_row = tk.Frame(inner, bg=BG2)
        btn_row.pack(fill="x")

        sharpe_run_btn = tk.Button(
            btn_row, text="Run Screener",
            command=lambda: self._run_sharpe(50),
            bg="#2ecc9a", fg="white", relief="flat",
            font=FONT_BOLD, cursor="hand2",
            padx=16, pady=7,
            activebackground="#27a87e",
        )
        sharpe_run_btn.pack(side="left")
        self.btn_refs["sharpe_run"] = sharpe_run_btn

        sharpe_top_btn = tk.Button(
            btn_row, text="Top 100",
            command=lambda: self._run_sharpe(100),
            bg=BG3, fg=FG2, relief="flat",
            font=FONT_MAIN, cursor="hand2",
            padx=12, pady=7,
            activebackground=BG2,
        )
        sharpe_top_btn.pack(side="left", padx=(8, 0))
        self.btn_refs["sharpe_top"] = sharpe_top_btn

        self.sharpe_run_status = tk.StringVar(value="Idle")
        self.status_vars["sharpe_merged"] = self.sharpe_run_status
        tk.Label(
            btn_row, textvariable=self.sharpe_run_status,
            bg=BG2, fg=FG3, font=FONT_SMALL, anchor="e",
        ).pack(side="right")

    def _on_sharpe_mode_change(self):
        if self.sharpe_mode.get() == "historical":
            self.sharpe_date_row.pack(fill="x", pady=(0, 4),
                                      before=self.sharpe_status)
            self.sharpe_status.config(
                text="Enter a past date to run screener on historical data",
                fg=FG3
            )
        else:
            self.sharpe_date_row.pack_forget()
            self.sharpe_status.config(text="", fg=FG3)

    def _validate_sharpe_date(self):
        val = self.sharpe_date_var.get().strip()
        try:
            datetime.strptime(val, "%d/%m/%Y")
            if not getattr(self, "_sharpe_window_error", False):
                self.sharpe_status.config(text="", fg=FG3)
            return True
        except ValueError:
            self.sharpe_status.config(
                text="Invalid date -- use dd/mm/yyyy", fg=DANGER
            )
            return False

    def _validate_sharpe_windows(self):
        try:
            long_months = int(self.sharpe_long_months_var.get().strip())
            short_months = int(self.sharpe_short_months_var.get().strip())
        except ValueError:
            self._sharpe_window_error = True
            self.sharpe_status.config(
                text="Sharpe windows must be whole months", fg=DANGER
            )
            return None

        if long_months <= 0 or short_months <= 0:
            self._sharpe_window_error = True
            self.sharpe_status.config(
                text="Sharpe windows must be positive", fg=DANGER
            )
            return None

        if long_months > 12 or short_months > 12:
            self._sharpe_window_error = True
            self.sharpe_status.config(
                text="Sharpe windows cannot exceed 12 months", fg=DANGER
            )
            return None

        self._sharpe_window_error = False
        if self.sharpe_mode.get() == "historical":
            self.sharpe_status.config(
                text="Enter a past date to run screener on historical data",
                fg=FG3,
            )
        else:
            self.sharpe_status.config(text="", fg=FG3)
        return long_months, short_months

    def _run_sharpe(self, top=50):
        mode = self.sharpe_mode.get()
        args = ["--top", str(top)]
        windows = self._validate_sharpe_windows()
        if windows is None:
            return
        long_months, short_months = windows
        args += ["--long-months", str(long_months),
                 "--short-months", str(short_months)]

        if mode == "historical":
            date_val = self.sharpe_date_var.get().strip()
            if not self._validate_sharpe_date():
                return
            date_obj = datetime.strptime(date_val, "%d/%m/%Y")
            if date_obj.date() > datetime.today().date():
                self.sharpe_status.config(
                    text="Date cannot be in the future", fg=DANGER
                )
                return
            date_arg = date_obj.strftime("%Y-%m-%d")
            args  += ["--date", date_arg]
            label  = (
                f"Sharpe Screener -- Historical ({date_val}, "
                f"{long_months}M/{short_months}M)"
            )
            outfile = f"{date_arg}.xlsx"
        else:
            label   = f"Sharpe Screener -- Live ({long_months}M/{short_months}M)"
            outfile = f"{datetime.today().strftime('%Y-%m-%d')}.xlsx"

        task = {
            "id":     "sharpe_merged",
            "label":  label,
            "script": "sharpe_screener.py",
            "args":   args,
        }

        self.sharpe_run_status.set("Running...")
        self.sharpe_status.config(text=f"Running {label} ...", fg=WARNING)

        def on_done():
            self.sharpe_run_status.set("Done")
            self.sharpe_status.config(
                text=f"Done. Check {outfile} in your folder.", fg=SUCCESS
            )

        self._run_task(task, on_done=on_done)

    # ------------------------------------------------------------------
    # DB STATS
    # ------------------------------------------------------------------

    def _build_stats_section(self, parent):
        self._section_label(parent, "DATABASE STATS")
        card = self._card(parent)
        card.pack(fill="x", padx=16, pady=(4, 16))

        self.stats_inner = tk.Frame(card, bg=BG2)
        self.stats_inner.pack(fill="x", padx=12, pady=10)
        tk.Label(
            self.stats_inner,
            text="Stats will load shortly...",
            bg=BG2, fg=FG2, font=FONT_SMALL,
        ).pack(anchor="w")

        tk.Button(
            card, text="⟳ Refresh Stats",
            command=self._refresh_stats,
            bg=BG3, fg=FG2, relief="flat",
            font=FONT_SMALL, cursor="hand2",
            padx=10, pady=4,
            activebackground=BG2,
        ).pack(anchor="w", padx=12, pady=(0, 10))

    def _refresh_stats(self):
        if not hasattr(self, "stats_inner"):
            return

        # Prevent overlapping background threads that lock the UI
        if getattr(self, "_is_counting_stats", False):
            return
        self._is_counting_stats = True

        for w in self.stats_inner.winfo_children():
            w.destroy()
        tk.Label(
            self.stats_inner,
            text="Loading stats...",
            bg=BG2, fg=FG2, font=FONT_SMALL,
        ).pack(anchor="w")

        def load_stats():
            try:
                from db import get_db_stats
                payload = ("ok", get_db_stats())
            except Exception as exc:
                payload = ("error", str(exc))

            def render():
                self._is_counting_stats = False # Release the lock
                
                if not hasattr(self, "stats_inner"):
                    return
                for w in self.stats_inner.winfo_children():
                    w.destroy()

                kind, data = payload
                if kind == "error":
                    tk.Label(
                        self.stats_inner,
                        text=f"Stats error: {data}",
                        bg=BG2, fg=DANGER, font=FONT_SMALL,
                    ).pack(anchor="w")
                    return

                stats = data
                if not stats.get("exists"):
                    tk.Label(
                        self.stats_inner,
                        text="Database not found -- run downloader first.",
                        bg=BG2, fg=DANGER, font=FONT_SMALL,
                    ).pack(anchor="w")
                    return

                if "error" in stats:
                    tk.Label(
                        self.stats_inner,
                        text=f"Error: {stats['error']}",
                        bg=BG2, fg=DANGER, font=FONT_SMALL,
                    ).pack(anchor="w")
                    return

                items = [
                    ("Total rows", stats["rows"], FG),
                    ("Symbols", stats["symbols"], FG),
                    ("Earliest date", stats["earliest"], FG2),
                    ("Latest date", stats["latest"], FG2),
                    ("DB size", f"{stats['size_mb']} MB", ACCENT2),
                ]
                grid = tk.Frame(self.stats_inner, bg=BG2)
                grid.pack(fill="x")
                for i, (lbl, val, col) in enumerate(items):
                    tk.Label(
                        grid, text=lbl, bg=BG2, fg=FG3,
                        font=FONT_SMALL, anchor="w",
                    ).grid(row=i, column=0, sticky="w", pady=2, padx=(0, 20))
                    tk.Label(
                        grid, text=val, bg=BG2, fg=col,
                        font=FONT_BOLD, anchor="w",
                    ).grid(row=i, column=1, sticky="w", pady=2)

            self.root.after(0, render)

        threading.Thread(target=load_stats, daemon=True).start()

    # ------------------------------------------------------------------
    # RIGHT PANEL -- LOG
    # ------------------------------------------------------------------

    def _build_right(self, parent):
        hdr = tk.Frame(parent, bg=BG2)
        hdr.pack(fill="x")

        tk.Label(
            hdr, text="  Output Log",
            bg=BG2, fg=FG, font=FONT_BOLD,
        ).pack(side="left", padx=4, pady=10)

        tk.Button(
            hdr, text="Clear",
            command=self._clear_log,
            bg=BG3, fg=FG2, relief="flat",
            font=FONT_SMALL, cursor="hand2",
            padx=10, pady=4, activebackground=BG2,
        ).pack(side="right", padx=8)

        # Progress bar row
        self.prog_frame = tk.Frame(parent, bg=BG2)
        self.progress_label = tk.Label(
            self.prog_frame, text="", bg=BG2, fg=FG2, font=FONT_SMALL
        )
        self.progress_label.pack(side="left", padx=(8, 6))
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(
            self.prog_frame,
            variable=self.progress_var,
            maximum=100, mode="determinate",
        )
        self.progress_bar.pack(side="left", fill="x", expand=True, padx=(0, 8))
        # Hidden until task runs
        self.prog_frame.pack_forget()

        # Log box
        self.log_box = scrolledtext.ScrolledText(
            parent,
            bg=BG3, fg=FG, insertbackground=FG,
            font=FONT_MONO, relief="flat",
            state="disabled", wrap="word",
        )
        self.log_box.pack(fill="both", expand=True)

        self.log_box.tag_config("info",    foreground=FG)
        self.log_box.tag_config("success", foreground=SUCCESS)
        self.log_box.tag_config("warning", foreground=WARNING)
        self.log_box.tag_config("error",   foreground=DANGER)
        self.log_box.tag_config("header",  foreground=ACCENT)
        self.log_box.tag_config("muted",   foreground=FG3)

    # ------------------------------------------------------------------
    # CLOCK
    # ------------------------------------------------------------------

    def _tick_clock(self):
        self.clock_lbl.config(
            text=datetime.now().strftime("%a %d %b %Y   %H:%M:%S")
        )
        self.root.after(1000, self._tick_clock)

    # ------------------------------------------------------------------
    # CONFIG PANEL REFRESH
    # ------------------------------------------------------------------

    def _refresh_config_panel(self):
        cfg = read_config()
        token = cfg.get("ACCESS_TOKEN", "")
        if token and "your_access_token" not in token:
            self.token_var.set(token)

    # ------------------------------------------------------------------
    # LOGGING
    # ------------------------------------------------------------------

    def _log(self, text, tag="info"):
        self.log_box.config(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        if tag == "info":
            lower = text.lower()
            if any(w in lower for w in ["error", "failed", "fail"]):
                tag = "error"
            elif any(w in lower for w in ["complete", "success", "saved", "done"]):
                tag = "success"
            elif any(w in lower for w in ["warning", "skipped", "skip"]):
                tag = "warning"
            elif text.startswith("===") or text.startswith("---"):
                tag = "header"
        self.log_box.insert("end", f"[{ts}] {text}\n", tag)
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def _clear_log(self):
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")

    # ------------------------------------------------------------------
    # PROGRESS BAR
    # ------------------------------------------------------------------

    def _parse_progress(self, line):
        if line.startswith("PROGRESS|"):
            parts = line.split("|", 4)
            if len(parts) >= 5:
                try:
                    pct = float(parts[1])
                    label = parts[2]
                    completed = parts[3]
                    total = parts[4]
                    text = f"{label} ({pct:.0f}% - {completed}/{total})"
                    return pct, text
                except ValueError:
                    return None
        m = re.search(r"(\d+)%\|", line)
        if m:
            pct = float(m.group(1))
            return pct, f"{self.progress_label.cget('text').split(' (')[0]} ({pct:.0f}%)"
        return None

    def _show_progress(self, label=""):
        self.progress_label.config(text=label)
        self.progress_var.set(0)
        self.prog_frame.pack(fill="x", padx=8, pady=(0, 4),
                             before=self.log_box)

    def _hide_progress(self):
        self.prog_frame.pack_forget()
        self.progress_var.set(0)

    def _maybe_log_progress(self, pct, label, progress_state):
        """Mirror coarse progress updates into the log without flooding it."""
        threshold = 100 if pct >= 100 else int(pct // 10) * 10
        if threshold <= progress_state["last_logged"]:
            return
        progress_state["last_logged"] = threshold
        self._log(f"{label}: {threshold}% complete", "muted")

    # ------------------------------------------------------------------
    # TASK RUNNER
    # ------------------------------------------------------------------

    def _set_status(self, task_id, text, color=FG3):
        if task_id in self.status_vars:
            self.status_vars[task_id].set(text)

    def _set_all_buttons(self, state):
        for btn in self.btn_refs.values():
            try:
                btn.config(state=state)
            except Exception:
                pass

    def _run_task(self, task, on_done=None):
        if self.running:
            messagebox.showwarning(
                "Busy", "A task is already running. Please wait."
            )
            return

        self.running = True
        self._set_all_buttons("disabled")
        self._set_status(task.get("id", ""), "Running...", WARNING)
        self._show_progress(task.get("label", ""))

        cmd = [PYTHON, str(BASE_DIR / task["script"])] + task.get("args", [])

        self._log("")
        self._log(f"{'=' * 50}", "header")
        self._log(f"  Starting: {task.get('label', task['script'])}", "header")
        self._log(f"{'=' * 50}", "header")

        output_queue = queue.Queue()
        state = {"done": False, "returncode": None}
        progress_state = {"last_logged": -10}

        def finalize(ok):
            self.running = False
            self._set_status(
                task.get("id", ""),
                "Done" if ok else "Failed",
                SUCCESS if ok else DANGER,
            )
            self._log(
                "Task completed successfully."
                if ok else f"Task failed (exit code {state['returncode']})",
                "success" if ok else "error",
            )
            self._set_all_buttons("normal")
            self._hide_progress()
            self.root.after(150, self._refresh_stats)
            if on_done:
                self.root.after(100, on_done)

        def poll_output():
            while True:
                try:
                    kind, payload = output_queue.get_nowait()
                except queue.Empty:
                    break

                if kind == "line":
                    progress = self._parse_progress(payload)
                    if progress is not None:
                        pct, label = progress
                        self.progress_var.set(pct)
                        self.progress_label.config(text=label)
                        self._maybe_log_progress(
                            pct,
                            label.split(" (")[0],
                            progress_state,
                        )
                    else:
                        self._log(payload)
                elif kind == "error":
                    self._log(f"Error launching task: {payload}", "error")
                    self._set_status(task.get("id", ""), "Error", DANGER)
                    state["done"] = True
                    state["returncode"] = 1
                elif kind == "done":
                    state["done"] = True
                    state["returncode"] = payload

            if state["done"] and output_queue.empty():
                finalize(state["returncode"] == 0)
            else:
                self.root.after(75, poll_output)

        def run():
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
                    line = line.rstrip()
                    if line:
                        output_queue.put(("line", line))

                proc.wait()
                output_queue.put(("done", proc.returncode))
            except Exception as exc:
                output_queue.put(("error", str(exc)))

        threading.Thread(target=run, daemon=True).start()
        self.root.after(75, poll_output)

    def _run_daily_routine(self):
        """Runs all daily tasks in sequence."""
        daily = [t for t in TASKS if t.get("daily")]

        def run_next(tasks):
            if not tasks:
                self._log("Daily routine complete!", "success")
                self._refresh_stats()
                return
            self._run_task(
                self._resolve_task(tasks[0]),
                on_done=lambda: run_next(tasks[1:]),
            )

        run_next(daily)


# ===========================================================================
# ENTRY POINT
# ===========================================================================

def main():
    root = tk.Tk()

    style = ttk.Style()
    style.theme_use("clam")
    style.configure(
        "Vertical.TScrollbar",
        background=BG3, troughcolor=BG2,
        bordercolor=BG2, arrowcolor=FG3, relief="flat",
    )
    style.configure(
        "TProgressbar",
        troughcolor=BG3, background=ACCENT,
        bordercolor=BG2, lightcolor=ACCENT, darkcolor=ACCENT,
    )

    ZerodhaGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
