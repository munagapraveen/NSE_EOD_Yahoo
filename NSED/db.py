"""
db.py — Database layer
=======================
All SQLite operations. Import from here — never write raw SQL in task scripts.

Connection usage — always use context manager to prevent leaks:
    with get_connection() as conn:
        ...  # conn auto-closes on exit, even on exception
"""

import sqlite3
import pandas as pd
from contextlib import contextmanager
from config import DB_FILE
from pathlib import Path


# ===========================================================================
# CONNECTION — context manager prevents leaks on crash
# ===========================================================================

@contextmanager
def get_connection(db_file=DB_FILE):
    """
    Context manager for SQLite connections.
    Always closes the connection on exit, even if an exception occurs.

    Usage:
        with get_connection() as conn:
            conn.execute(...)
    """
    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
    finally:
        conn.close()


# ===========================================================================
# SCHEMA — safe to call on every run
# ===========================================================================

def setup_schema(conn):
    """Creates all tables and indexes. Safe to call repeatedly."""

    conn.execute("""
        CREATE TABLE IF NOT EXISTS eod_data (
            symbol          TEXT    NOT NULL,
            company_name    TEXT,
            segment         TEXT,
            instrument_type TEXT,
            isin            TEXT,
            date            TEXT    NOT NULL,
            open            REAL,
            high            REAL,
            low             REAL,
            close           REAL,
            volume          INTEGER,
            PRIMARY KEY (symbol, date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_symbol      ON eod_data (symbol)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_date        ON eod_data (date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_symbol_date ON eod_data (symbol, date)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS fundamentals (
            symbol              TEXT PRIMARY KEY,
            company_name        TEXT,
            sector              TEXT,
            industry            TEXT,
            shares_outstanding  INTEGER,
            face_value          REAL,
            book_value          REAL,
            pe_ratio            REAL,
            dividend_yield      REAL,
            week_52_high        REAL,
            week_52_low         REAL,
            last_updated        TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS symbols_master (
            symbol          TEXT PRIMARY KEY,
            company_name    TEXT,
            isin            TEXT,
            category        TEXT,
            series          TEXT,
            active          INTEGER DEFAULT 1,
            source          TEXT,
            last_synced_on  TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_symbols_master_category ON symbols_master (category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_symbols_master_active ON symbols_master (active)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS marketcap (
            symbol              TEXT NOT NULL,
            date                TEXT NOT NULL,
            market_cap_cr       REAL,
            shares_outstanding  INTEGER,
            PRIMARY KEY (symbol, date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_marketcap_symbol_date ON marketcap (symbol, date)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS indicators (
            symbol    TEXT NOT NULL,
            date      TEXT NOT NULL,
            ma_5      REAL,
            ma_10     REAL,
            ma_20     REAL,
            ma_50     REAL,
            ma_100    REAL,
            ma_200    REAL,
            PRIMARY KEY (symbol, date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_indicators_symbol_date ON indicators (symbol, date)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS corporate_actions (
            symbol       TEXT,
            action_type  TEXT,
            ex_date      TEXT,
            details      TEXT,
            detected_on  TEXT,
            refreshed    INTEGER DEFAULT 0,
            PRIMARY KEY (symbol, action_type, ex_date)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS symbol_history (
            old_symbol  TEXT,
            new_symbol  TEXT,
            changed_on  TEXT,
            note        TEXT,
            PRIMARY KEY (old_symbol, new_symbol)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS asm_series_log (
            base_symbol  TEXT,
            kite_symbol  TEXT,
            isin         TEXT,
            direction    TEXT,
            detected_on  TEXT,
            rows_fetched INTEGER DEFAULT 0,
            PRIMARY KEY (base_symbol, kite_symbol, detected_on)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS adjustment_log (
            symbol        TEXT,
            refreshed_on  TEXT,
            reason        TEXT,
            rows_deleted  INTEGER,
            rows_inserted INTEGER
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS script_run_log (
            script_name   TEXT PRIMARY KEY,
            last_run_date TEXT,
            last_run_time TEXT,
            status        TEXT
        )
    """)

    conn.commit()


# ===========================================================================
# EOD DATA
# ===========================================================================

EOD_COLUMNS = [
    "symbol", "company_name", "segment", "instrument_type", "isin",
    "date", "open", "high", "low", "close", "volume",
]


def get_last_date(conn, symbol):
    """Returns the latest stored date for a symbol, or None."""
    cur = conn.execute(
        "SELECT MAX(date) FROM eod_data WHERE symbol = ?", (symbol,)
    )
    return cur.fetchone()[0]


def insert_eod_rows(conn, df):
    """Inserts EOD rows. Silently skips duplicate (symbol, date) pairs."""
    df = df[EOD_COLUMNS].copy()
    placeholders = ",".join(["?"] * len(EOD_COLUMNS))
    conn.executemany(
        f"INSERT OR IGNORE INTO eod_data VALUES ({placeholders})",
        df.itertuples(index=False, name=None),
    )
    conn.commit()


def delete_symbol_data(conn, symbol):
    """Deletes all rows for a symbol. Returns number of rows deleted."""
    cur = conn.execute("DELETE FROM eod_data WHERE symbol = ?", (symbol,))
    conn.execute("DELETE FROM marketcap WHERE symbol = ?", (symbol,))
    conn.execute("DELETE FROM indicators WHERE symbol = ?", (symbol,))
    conn.commit()
    return cur.rowcount


def get_symbols_with_isin(conn):
    """Returns DataFrame of (symbol, isin) for all symbols in the DB."""
    cur = conn.execute("""
        SELECT DISTINCT symbol, isin FROM eod_data
        WHERE isin IS NOT NULL AND isin != ''
    """)
    return pd.DataFrame(cur.fetchall(), columns=["symbol", "isin"])


def get_all_symbols(conn):
    """Returns set of all symbols currently in eod_data."""
    cur = conn.execute("SELECT DISTINCT symbol FROM eod_data")
    return {row[0] for row in cur.fetchall()}


def load_symbols_master(conn):
    """Returns the stored NSE master universe."""
    return pd.read_sql("""
        SELECT symbol, company_name, isin, category, series, active, source, last_synced_on
        FROM symbols_master
        ORDER BY symbol
    """, conn)


def upsert_symbols_master(conn, records):
    """Insert or replace NSE master symbols."""
    if not records:
        return
    conn.executemany("""
        INSERT OR REPLACE INTO symbols_master VALUES (
            :symbol, :company_name, :isin, :category,
            :series, :active, :source, :last_synced_on
        )
    """, records)
    conn.commit()


def mark_missing_master_symbols_inactive(conn, active_symbols):
    """Mark symbols not present in the latest NSE master as inactive."""
    placeholders = ",".join("?" for _ in active_symbols) or "''"
    conn.execute(
        f"""
        UPDATE symbols_master
        SET active = 0
        WHERE symbol NOT IN ({placeholders})
        """,
        tuple(active_symbols),
    )
    conn.commit()


def get_active_master_symbols(conn, categories=None):
    """Return active NSE master symbols, optionally filtered by category."""
    params = []
    where = ["active = 1"]
    if categories:
        placeholders = ",".join("?" for _ in categories)
        where.append(f"category IN ({placeholders})")
        params.extend(categories)
    return pd.read_sql(f"""
        SELECT symbol, company_name, isin, category, series
        FROM symbols_master
        WHERE {' AND '.join(where)}
        ORDER BY symbol
    """, conn, params=params)


def get_db_stats(db_file=DB_FILE):
    """
    Returns a dict of database statistics for display in the GUI.
    Safe to call even if the DB doesn't exist yet.
    """
    path = Path(db_file)
    if not path.exists():
        return {"exists": False}

    try:
        with get_connection(db_file) as conn:
            size     = path.stat().st_size / (1024 * 1024)
            total    = conn.execute("SELECT COUNT(*) FROM eod_data").fetchone()[0]
            syms     = conn.execute("SELECT COUNT(DISTINCT symbol) FROM eod_data").fetchone()[0]
            latest   = conn.execute("SELECT MAX(date) FROM eod_data").fetchone()[0]
            earliest = conn.execute("SELECT MIN(date) FROM eod_data").fetchone()[0]
        return {
            "exists":   True,
            "size_mb":  round(size, 1),
            "rows":     f"{total:,}",
            "symbols":  f"{syms:,}",
            "latest":   latest   or "—",
            "earliest": earliest or "—",
        }
    except Exception as exc:
        return {"exists": True, "error": str(exc)}


# ===========================================================================
# FUNDAMENTALS
# ===========================================================================

def upsert_fundamentals(conn, records):
    """Insert or replace fundamentals rows (list of dicts)."""
    conn.executemany("""
        INSERT OR REPLACE INTO fundamentals VALUES (
            :symbol, :company_name, :sector, :industry,
            :shares_outstanding, :face_value, :book_value,
            :pe_ratio, :dividend_yield, :week_52_high, :week_52_low,
            :last_updated
        )
    """, records)
    conn.commit()


def upsert_marketcap_rows(conn, records):
    """Insert or replace daily market-cap rows (list of dicts)."""
    if not records:
        return
    conn.executemany("""
        INSERT OR REPLACE INTO marketcap VALUES (
            :symbol, :date, :market_cap_cr, :shares_outstanding
        )
    """, records)
    conn.commit()


def upsert_indicator_rows(conn, records):
    """Insert or replace moving-average rows (list of dicts)."""
    if not records:
        return
    conn.executemany("""
        INSERT OR REPLACE INTO indicators VALUES (
            :symbol, :date, :ma_5, :ma_10, :ma_20, :ma_50, :ma_100, :ma_200
        )
    """, records)
    conn.commit()


def load_indicator_snapshot(conn, date):
    return pd.read_sql("""
        SELECT symbol, date, ma_5, ma_10, ma_20, ma_50, ma_100, ma_200
        FROM indicators
        WHERE date = ?
    """, conn, params=[date])


# ===========================================================================
# CORPORATE ACTIONS
# ===========================================================================

def log_corporate_action(conn, symbol, action_type, ex_date, details):
    conn.execute(
        "INSERT OR IGNORE INTO corporate_actions VALUES (?,?,?,?,date('now'),0)",
        (symbol, action_type, ex_date, details),
    )
    conn.commit()


def mark_action_refreshed(conn, symbol):
    conn.execute(
        "UPDATE corporate_actions SET refreshed=1 WHERE symbol=?", (symbol,)
    )
    conn.commit()


# ===========================================================================
# ADJUSTMENT LOG
# ===========================================================================

def log_adjustment(conn, symbol, reason, rows_deleted, rows_inserted):
    conn.execute(
        "INSERT INTO adjustment_log VALUES (?,date('now'),?,?,?)",
        (symbol, reason, rows_deleted, rows_inserted),
    )
    conn.commit()


# ===========================================================================
# SYMBOL HISTORY
# ===========================================================================

def log_symbol_change(conn, old_symbol, new_symbol, note=""):
    conn.execute(
        "INSERT OR IGNORE INTO symbol_history VALUES (?,?,date('now'),?)",
        (old_symbol, new_symbol, note),
    )
    conn.commit()


def rename_symbol(conn, old_symbol, new_symbol):
    """
    Moves all rows from old_symbol to new_symbol.
    Skips dates already existing under new_symbol.
    Returns number of rows moved.
    """
    moved_rows = 0
    for table in ["eod_data", "marketcap", "indicators"]:
        existing = {
            row[0] for row in conn.execute(
                f"SELECT date FROM {table} WHERE symbol=?", (new_symbol,)
            )
        }
        cur = conn.execute(f"SELECT * FROM {table} WHERE symbol=?", (old_symbol,))
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()

        sym_idx = cols.index("symbol")
        date_idx = cols.index("date")
        to_move = [r for r in rows if r[date_idx] not in existing]

        if to_move:
            ph = ",".join(["?"] * len(cols))
            for row in to_move:
                r = list(row)
                r[sym_idx] = new_symbol
                conn.execute(f"INSERT OR IGNORE INTO {table} VALUES ({ph})", r)
            if table == "eod_data":
                moved_rows = len(to_move)

        # Always remove the old symbol rows once the surviving dates were copied
        # or intentionally skipped due to existing collisions under new_symbol.
        conn.execute(f"DELETE FROM {table} WHERE symbol=?", (old_symbol,))

    conn.commit()

    log_symbol_change(conn, old_symbol, new_symbol)
    return moved_rows


# ===========================================================================
# ASM LOG
# ===========================================================================

def log_asm_transition(conn, base_symbol, kite_symbol, isin, direction, rows):
    conn.execute(
        "INSERT OR IGNORE INTO asm_series_log VALUES (?,?,?,?,date('now'),?)",
        (base_symbol, kite_symbol, isin, direction, rows),
    )
    conn.commit()


# ===========================================================================
# SCRIPT RUN LOG
# ===========================================================================

def get_last_run_date(conn, script_name):
    """Returns last successful run date as datetime, or None."""
    from datetime import datetime
    cur = conn.execute(
        "SELECT last_run_date FROM script_run_log WHERE script_name=?",
        (script_name,),
    )
    row = cur.fetchone()
    return datetime.strptime(row[0], "%Y-%m-%d") if row and row[0] else None


def save_run_date(conn, script_name, status="success"):
    from datetime import datetime
    now = datetime.today()
    conn.execute(
        "INSERT OR REPLACE INTO script_run_log VALUES (?,?,?,?)",
        (script_name, now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), status),
    )
    conn.commit()
