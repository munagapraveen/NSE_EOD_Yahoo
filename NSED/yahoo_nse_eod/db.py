"""SQLite database layer for the standalone Yahoo/NSE EOD project.

Design note:
    `share_history` is a source/staging table for shares outstanding fetched
    from Yahoo. Once a trading date is materialized into `adjusted_eod_prices`,
    the authoritative historical market-cap series is:

        adjusted_eod_prices.market_cap_cr

    For brand-new dates, market cap is derived from `share_history`.
    For historical corporate-action rebuilds, previously stored
    `market_cap_cr` is preserved by date and `shares_outstanding` inside
    `adjusted_eod_prices` is adjusted to remain consistent with the refreshed
    adjusted close.
"""

from contextlib import contextmanager
import sqlite3

import pandas as pd

from config import DB_FILE

MA_WINDOWS = [5, 10, 20, 50, 100, 200]


@contextmanager
def get_connection(db_file=DB_FILE):
    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
    finally:
        conn.close()


def setup_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS symbols (
            symbol            TEXT PRIMARY KEY,
            yahoo_symbol      TEXT NOT NULL,
            company_name      TEXT,
            isin              TEXT,
            series            TEXT,
            active            INTEGER NOT NULL DEFAULT 1,
            status            TEXT NOT NULL DEFAULT 'active',
            last_seen_date    TEXT,
            source            TEXT,
            last_synced_at    TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_symbols_isin ON symbols(isin)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_symbols_active ON symbols(active)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS symbol_aliases (
            old_symbol        TEXT,
            new_symbol        TEXT,
            effective_date    TEXT,
            source            TEXT,
            note              TEXT,
            detected_at       TEXT DEFAULT CURRENT_DATE,
            PRIMARY KEY (old_symbol, new_symbol)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_eod_prices (
            symbol            TEXT NOT NULL,
            date              TEXT NOT NULL,
            open              REAL,
            high              REAL,
            low               REAL,
            close             REAL,
            adj_close         REAL,
            volume            INTEGER,
            dividends         REAL DEFAULT 0,
            stock_splits      REAL DEFAULT 0,
            source            TEXT NOT NULL DEFAULT 'yahoo',
            downloaded_at     TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_eod_symbol_date ON raw_eod_prices(symbol, date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_eod_date ON raw_eod_prices(date)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS adjusted_eod_prices (
            symbol            TEXT NOT NULL,
            date              TEXT NOT NULL,
            open              REAL,
            high              REAL,
            low               REAL,
            close             REAL,
            volume            REAL,
            split_factor      REAL NOT NULL,
            shares_outstanding REAL,
            market_cap_cr     REAL,
            ma_5              REAL,
            ma_10             REAL,
            ma_20             REAL,
            ma_50             REAL,
            ma_100            REAL,
            ma_200            REAL,
            adjusted_at       TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_adj_eod_symbol_date ON adjusted_eod_prices(symbol, date)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS share_history (
            symbol            TEXT NOT NULL,
            date              TEXT NOT NULL,
            shares_outstanding REAL,
            source            TEXT NOT NULL DEFAULT 'yahoo',
            fetched_at        TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_share_history_symbol_date ON share_history(symbol, date)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS corporate_actions (
            symbol            TEXT NOT NULL,
            ex_date           TEXT NOT NULL,
            action_type       TEXT NOT NULL,
            value             REAL,
            source            TEXT NOT NULL,
            note              TEXT,
            PRIMARY KEY (symbol, ex_date, action_type, source)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_actions_symbol_date ON corporate_actions(symbol, ex_date)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS download_runs (
            run_id            INTEGER PRIMARY KEY AUTOINCREMENT,
            job_name          TEXT NOT NULL,
            started_at        TEXT DEFAULT CURRENT_TIMESTAMP,
            finished_at       TEXT,
            status            TEXT,
            details           TEXT
        )
    """)

    existing_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(adjusted_eod_prices)")
    }
    for col in ["shares_outstanding", "market_cap_cr"]:
        if col not in existing_columns:
            conn.execute(f"ALTER TABLE adjusted_eod_prices ADD COLUMN {col} REAL")
    for window in MA_WINDOWS:
        col = f"ma_{window}"
        if col not in existing_columns:
            conn.execute(f"ALTER TABLE adjusted_eod_prices ADD COLUMN {col} REAL")

    conn.commit()


def upsert_symbols(conn, records):
    conn.executemany("""
        INSERT INTO symbols (
            symbol, yahoo_symbol, company_name, isin, series,
            active, status, last_seen_date, source, last_synced_at
        ) VALUES (
            :symbol, :yahoo_symbol, :company_name, :isin, :series,
            :active, :status, :last_seen_date, :source, :last_synced_at
        )
        ON CONFLICT(symbol) DO UPDATE SET
            yahoo_symbol=excluded.yahoo_symbol,
            company_name=excluded.company_name,
            isin=excluded.isin,
            series=excluded.series,
            active=excluded.active,
            status=excluded.status,
            last_seen_date=excluded.last_seen_date,
            source=excluded.source,
            last_synced_at=excluded.last_synced_at
    """, records)
    conn.commit()


def mark_missing_symbols_inactive(conn, active_symbols):
    placeholders = ",".join("?" for _ in active_symbols) or "''"
    conn.execute(
        f"""
        UPDATE symbols
        SET active = 0,
            status = CASE
                WHEN status = 'renamed' THEN status
                ELSE 'inactive'
            END
        WHERE symbol NOT IN ({placeholders})
        """,
        tuple(active_symbols),
    )
    conn.commit()


def get_active_symbols(conn):
    return pd.read_sql("""
        SELECT symbol, yahoo_symbol, company_name, isin, series
        FROM symbols
        WHERE active = 1
        ORDER BY symbol
    """, conn)


def get_symbol_last_dates(conn, symbols=None):
    query = """
        SELECT symbol, MAX(date) AS last_date
        FROM raw_eod_prices
    """
    params = []
    if symbols:
        placeholders = ",".join("?" for _ in symbols)
        query += f" WHERE symbol IN ({placeholders})"
        params.extend(symbols)
    query += " GROUP BY symbol"
    rows = conn.execute(query, params).fetchall()
    return {symbol: last_date for symbol, last_date in rows}


def insert_raw_prices(conn, df):
    cols = [
        "symbol", "date", "open", "high", "low", "close",
        "adj_close", "volume", "dividends", "stock_splits", "source",
    ]
    conn.executemany(
        """
        INSERT INTO raw_eod_prices (
            symbol, date, open, high, low, close,
            adj_close, volume, dividends, stock_splits, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, date) DO UPDATE SET
            open=excluded.open,
            high=excluded.high,
            low=excluded.low,
            close=excluded.close,
            adj_close=excluded.adj_close,
            volume=excluded.volume,
            dividends=excluded.dividends,
            stock_splits=excluded.stock_splits,
            source=excluded.source,
            downloaded_at=CURRENT_TIMESTAMP
        """,
        df[cols].itertuples(index=False, name=None),
    )
    conn.commit()


def upsert_corporate_actions(conn, records):
    conn.executemany("""
        INSERT INTO corporate_actions (
            symbol, ex_date, action_type, value, source, note
        ) VALUES (
            :symbol, :ex_date, :action_type, :value, :source, :note
        )
        ON CONFLICT(symbol, ex_date, action_type, source) DO UPDATE SET
            value=excluded.value,
            note=excluded.note
    """, records)
    conn.commit()


def replace_adjusted_prices(conn, symbol, df):
    conn.execute("DELETE FROM adjusted_eod_prices WHERE symbol = ?", (symbol,))
    upsert_adjusted_prices(conn, df)
    conn.commit()


def upsert_adjusted_prices(conn, df):
    if df.empty:
        return

    cols = [
        "symbol", "date", "open", "high", "low", "close", "volume", "split_factor",
        "shares_outstanding", "market_cap_cr",
        "ma_5", "ma_10", "ma_20", "ma_50", "ma_100", "ma_200",
    ]
    conn.executemany(
        """
        INSERT INTO adjusted_eod_prices (
            symbol, date, open, high, low, close, volume, split_factor,
            shares_outstanding, market_cap_cr,
            ma_5, ma_10, ma_20, ma_50, ma_100, ma_200
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, date) DO UPDATE SET
            open=excluded.open,
            high=excluded.high,
            low=excluded.low,
            close=excluded.close,
            volume=excluded.volume,
            split_factor=excluded.split_factor,
            shares_outstanding=excluded.shares_outstanding,
            market_cap_cr=excluded.market_cap_cr,
            ma_5=excluded.ma_5,
            ma_10=excluded.ma_10,
            ma_20=excluded.ma_20,
            ma_50=excluded.ma_50,
            ma_100=excluded.ma_100,
            ma_200=excluded.ma_200,
            adjusted_at=CURRENT_TIMESTAMP
        """,
        df[cols].itertuples(index=False, name=None),
    )
    conn.commit()


def load_raw_prices(conn, symbol):
    return pd.read_sql("""
        SELECT symbol, date, open, high, low, close, adj_close, volume, dividends, stock_splits
        FROM raw_eod_prices
        WHERE symbol = ?
        ORDER BY date
    """, conn, params=[symbol])


def upsert_share_history(conn, records):
    conn.executemany("""
        INSERT INTO share_history (
            symbol, date, shares_outstanding, source
        ) VALUES (
            :symbol, :date, :shares_outstanding, :source
        )
        ON CONFLICT(symbol, date) DO UPDATE SET
            shares_outstanding=excluded.shares_outstanding,
            source=excluded.source,
            fetched_at=CURRENT_TIMESTAMP
    """, records)
    conn.commit()


def load_share_history(conn, symbol):
    return pd.read_sql("""
        SELECT symbol, date, shares_outstanding
        FROM share_history
        WHERE symbol = ?
        ORDER BY date
    """, conn, params=[symbol])


def load_adjusted_market_caps(conn, symbol):
    return pd.read_sql("""
        SELECT symbol, date, market_cap_cr, shares_outstanding
        FROM adjusted_eod_prices
        WHERE symbol = ?
        ORDER BY date
    """, conn, params=[symbol])


def load_active_symbol_map(conn):
    return pd.read_sql("""
        SELECT symbol, isin, company_name, yahoo_symbol, status, active
        FROM symbols
        ORDER BY symbol
    """, conn)


def upsert_symbol_aliases(conn, records):
    conn.executemany("""
        INSERT INTO symbol_aliases (
            old_symbol, new_symbol, effective_date, source, note
        ) VALUES (
            :old_symbol, :new_symbol, :effective_date, :source, :note
        )
        ON CONFLICT(old_symbol, new_symbol) DO UPDATE SET
            effective_date=excluded.effective_date,
            source=excluded.source,
            note=excluded.note
    """, records)
    conn.commit()


def apply_symbol_rename(conn, old_symbol, new_symbol, effective_date=None, source="nse", note=""):
    for table in ["raw_eod_prices", "adjusted_eod_prices", "share_history"]:
        existing_dates = {
            row[0] for row in conn.execute(
                f"SELECT date FROM {table} WHERE symbol = ?",
                (new_symbol,),
            )
        }
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE symbol = ? ORDER BY date",
            (old_symbol,),
        ).fetchall()
        if rows:
            cols = [col[1] for col in conn.execute(f"PRAGMA table_info({table})")]
            symbol_idx = cols.index("symbol")
            date_idx = cols.index("date")
            placeholders = ",".join("?" for _ in cols)
            for row in rows:
                if row[date_idx] in existing_dates:
                    continue
                mutable = list(row)
                mutable[symbol_idx] = new_symbol
                conn.execute(
                    f"INSERT OR IGNORE INTO {table} VALUES ({placeholders})",
                    mutable,
                )
            conn.execute(f"DELETE FROM {table} WHERE symbol = ?", (old_symbol,))

    action_rows = conn.execute(
        """
        SELECT ex_date, action_type, value, source, note
        FROM corporate_actions
        WHERE symbol = ?
        """,
        (old_symbol,),
    ).fetchall()
    for ex_date, action_type, value, src, row_note in action_rows:
        conn.execute(
            """
            INSERT OR IGNORE INTO corporate_actions (
                symbol, ex_date, action_type, value, source, note
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (new_symbol, ex_date, action_type, value, src, row_note),
        )
    conn.execute("DELETE FROM corporate_actions WHERE symbol = ?", (old_symbol,))
    conn.execute(
        """
        UPDATE symbols
        SET active = 0, status = 'renamed'
        WHERE symbol = ?
        """,
        (old_symbol,),
    )
    upsert_symbol_aliases(conn, [{
        "old_symbol": old_symbol,
        "new_symbol": new_symbol,
        "effective_date": effective_date,
        "source": source,
        "note": note,
    }])
    conn.commit()
