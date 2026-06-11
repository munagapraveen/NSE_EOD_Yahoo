"""SQLite database layer for the standalone Yahoo/NSE EOD project.

Design note:
    `share_history` is a source/staging table for shares outstanding fetched
    from Yahoo. Once a trading date is materialized into `adjusted_eod_prices`,
    the authoritative historical market-cap series is:

        marketcap.market_cap_cr

    For brand-new dates, market cap is derived from `share_history`.
    For historical corporate-action rebuilds, previously stored
    `market_cap_cr` is preserved by date and `shares_outstanding` inside
    `marketcap` is adjusted to remain consistent with the refreshed
    adjusted close.
"""

from contextlib import contextmanager
import sqlite3

import pandas as pd

from config import DB_FILE
from logger import get_logger

log = get_logger(__name__)

MA_WINDOWS = [5, 10, 20, 50, 100, 200]

def get_indicator_cols():
    return [f"ma_{window}" for window in MA_WINDOWS]


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
            instrument_type   TEXT NOT NULL DEFAULT 'STOCK',
            active            INTEGER NOT NULL DEFAULT 1,
            status            TEXT NOT NULL DEFAULT 'active',
            last_seen_date    TEXT,
            source            TEXT,
            last_synced_at    TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_symbols_isin ON symbols(isin)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_symbols_active ON symbols(active)")

    # Migration for instrument_type
    existing_symbols_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(symbols)")
    }
    if "instrument_type" not in existing_symbols_cols:
        conn.execute("ALTER TABLE symbols ADD COLUMN instrument_type TEXT NOT NULL DEFAULT 'STOCK'")
        log.info("Added instrument_type column to symbols table")

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
            volume            INTEGER,
            split_factor      REAL NOT NULL,
            adjusted_at       TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_adj_eod_symbol_date ON adjusted_eod_prices(symbol, date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_adj_eod_date ON adjusted_eod_prices(date)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS marketcap (
            symbol            TEXT NOT NULL,
            date              TEXT NOT NULL,
            market_cap_cr     REAL,
            shares_outstanding REAL,
            PRIMARY KEY (symbol, date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_marketcap_symbol_date ON marketcap(symbol, date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_marketcap_date ON marketcap(date)")

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

    # Wide format indicators table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS indicators (
            symbol            TEXT NOT NULL,
            date              TEXT NOT NULL,
            ma_5              REAL,
            ma_10             REAL,
            ma_20             REAL,
            ma_50             REAL,
            ma_100            REAL,
            ma_200            REAL,
            PRIMARY KEY (symbol, date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_indicators_symbol_date ON indicators(symbol, date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_indicators_date ON indicators(date)")

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
    # Remove redundant columns from adjusted_eod_prices
    for col in ["ma_5", "ma_10", "ma_20", "ma_50", "ma_100", "ma_200", "shares_outstanding", "market_cap_cr"]:
        if col in existing_columns:
            try:
                conn.execute(f"ALTER TABLE adjusted_eod_prices DROP COLUMN {col}")
                log.info(f"Dropped column {col} from adjusted_eod_prices")
            except Exception as e:
                log.warning(f"Could not drop column {col}: {e}")

    # Ensure indicators table is wide
    indicator_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(indicators)")
    }
    if "indicator" in indicator_cols:
        log.info("Indicators table is in old format. Recreating...")
        conn.execute("DROP TABLE indicators")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS indicators (
                symbol            TEXT NOT NULL,
                date              TEXT NOT NULL,
                ma_5              REAL,
                ma_10             REAL,
                ma_20             REAL,
                ma_50             REAL,
                ma_100            REAL,
                ma_200            REAL,
                PRIMARY KEY (symbol, date)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_indicators_symbol_date ON indicators(symbol, date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_indicators_date ON indicators(date)")
        return

    conn.commit()


def upsert_symbols(conn, records):
    conn.executemany("""
        INSERT INTO symbols (
            symbol, yahoo_symbol, company_name, isin, series, instrument_type,
            active, status, last_seen_date, source, last_synced_at
        ) VALUES (
            :symbol, :yahoo_symbol, :company_name, :isin, :series, :instrument_type,
            :active, :status, :last_seen_date, :source, :last_synced_at
        )
        ON CONFLICT(symbol) DO UPDATE SET
            yahoo_symbol=excluded.yahoo_symbol,
            company_name=excluded.company_name,
            isin=excluded.isin,
            series=excluded.series,
            instrument_type=excluded.instrument_type,
            active=excluded.active,
            status=excluded.status,
            last_seen_date=excluded.last_seen_date,
            source=excluded.source,
            last_synced_at=excluded.last_synced_at
    """, records)
    conn.commit()


def mark_missing_symbols_inactive(conn, active_symbols):
    """
    Marks symbols not in the active_symbols list as inactive.
    Uses a temporary table to avoid SQLite's max variable limit for IN clauses.
    """
    if active_symbols is None:
        log.warning("mark_missing_symbols_inactive called with None — skipping to avoid mass deactivation.")
        return
    if len(active_symbols) == 0:
        log.warning("mark_missing_symbols_inactive called with empty list — skipping to avoid mass deactivation.")
        return

    # Create temporary table for comparison
    conn.execute("CREATE TEMP TABLE temp_active_symbols (symbol TEXT PRIMARY KEY)")
    conn.executemany(
        "INSERT INTO temp_active_symbols (symbol) VALUES (?)",
        [(s,) for s in active_symbols]
    )

    conn.execute(
        """
        UPDATE symbols
        SET active = 0,
            status = CASE
                WHEN status = 'renamed' THEN status
                ELSE 'inactive'
            END
        WHERE symbol NOT IN (SELECT symbol FROM temp_active_symbols)
        """
    )
    conn.execute("DROP TABLE temp_active_symbols")
    conn.commit()


def get_active_symbols(conn):
    return pd.read_sql("""
        SELECT symbol, yahoo_symbol, company_name, isin, series, instrument_type
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
    
    # Convert pd.NA/NaN to None for SQLite compatibility
    clean_df = df[cols].where(pd.notnull(df[cols]), None)

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
        clean_df.itertuples(index=False, name=None),
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
    ]
    
    # Explicitly convert to tuples and replace NAType/NaN with None
    # Ensure date is string to avoid Timestamp binding error
    data = []
    for row in df[cols].itertuples(index=False, name=None):
        clean_row = []
        for i, v in enumerate(row):
            if pd.isna(v):
                clean_row.append(None)
            elif cols[i] == "date" and hasattr(v, "strftime"):
                clean_row.append(v.strftime("%Y-%m-%d"))
            elif cols[i] == "volume" and v is not None:
                try:
                    clean_row.append(int(v))
                except:
                    clean_row.append(v)
            else:
                clean_row.append(v)
        data.append(tuple(clean_row))

    conn.executemany(
        """
        INSERT INTO adjusted_eod_prices (
            symbol, date, open, high, low, close, volume, split_factor
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, date) DO UPDATE SET
            open=excluded.open,
            high=excluded.high,
            low=excluded.low,
            close=excluded.close,
            volume=excluded.volume,
            split_factor=excluded.split_factor,
            adjusted_at=CURRENT_TIMESTAMP
        """,
        data,
    )
    conn.commit()


def load_corporate_actions(conn, symbol):
    """Load split/bonus records for a symbol."""
    return pd.read_sql(
        "SELECT ex_date as date, action_type, value FROM corporate_actions WHERE symbol = ? AND action_type IN ('split', 'bonus')",
        conn,
        params=[symbol],
    )


def save_market_caps(conn, df):
    """
    Saves market_cap_cr and shares_outstanding into the marketcap table.
    """
    if df.empty:
        return
    
    cols = ["symbol", "date", "market_cap_cr", "shares_outstanding"]
    if not all(c in df.columns for c in cols):
        missing = [c for c in cols if c not in df.columns]
        log.warning(f"save_market_caps: skipping — missing columns: {missing}")
        return

    data = [
        tuple(None if pd.isna(v) else v for v in row)
        for row in df[cols].itertuples(index=False, name=None)
    ]
    
    conn.executemany(
        """
        INSERT INTO marketcap (symbol, date, market_cap_cr, shares_outstanding)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(symbol, date) DO UPDATE SET
            market_cap_cr=excluded.market_cap_cr,
            shares_outstanding=excluded.shares_outstanding
        """,
        data,
    )
    conn.commit()


def save_indicators(conn, df):
    """
    Saves all indicator columns from the given dataframe into the indicators table.
    """
    if df.empty:
        return
    
    # Identify available indicator columns (ma_*)
    indicator_cols = [c for c in df.columns if c.startswith("ma_")]
    cols = ["symbol", "date"] + indicator_cols
    
    data = [
        tuple(None if pd.isna(v) else v for v in row)
        for row in df[cols].itertuples(index=False, name=None)
    ]
    
    col_placeholders = ",".join(["?"] * len(cols))
    col_names = ",".join(cols)
    update_clause = ",".join([f"{c}=excluded.{c}" for c in indicator_cols])
    
    conn.executemany(
        f"""
        INSERT INTO indicators ({col_names})
        VALUES ({col_placeholders})
        ON CONFLICT(symbol, date) DO UPDATE SET
            {update_clause}
        """,
        data,
    )
    conn.commit()


def upsert_indicators(conn, df):
    """
    Legacy/Alternative upsert for wide indicator dataframes.
    """
    save_indicators(conn, df)


def load_indicators(conn, symbol, indicator_names=None):
    """
    Returns wide format by default now.
    """
    cols = ["date"]
    if indicator_names:
        cols.extend(indicator_names)
    else:
        # Load all ma_* columns
        info = conn.execute("PRAGMA table_info(indicators)").fetchall()
        cols.extend([row[1] for row in info if row[1].startswith("ma_")])
        
    col_str = ",".join(cols)
    query = f"SELECT {col_str} FROM indicators WHERE symbol = ? ORDER BY date"
    return pd.read_sql(query, conn, params=[symbol])


def load_raw_prices(conn, symbol):
    return pd.read_sql("""
        SELECT symbol, date, open, high, low, close, adj_close, volume, dividends, stock_splits
        FROM raw_eod_prices
        WHERE symbol = ?
        ORDER BY date
    """, conn, params=[symbol])


def upsert_share_history(conn, records):
    conn.executemany("""
        INSERT INTO share_history (symbol, date, shares_outstanding, source)
        VALUES (:symbol, :date, :shares_outstanding, :source)
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
        FROM marketcap
        WHERE symbol = ?
        ORDER BY date
    """, conn, params=[symbol])


def load_active_symbol_map(conn):
    return pd.read_sql("""
        SELECT symbol, isin, company_name, yahoo_symbol, status, active, instrument_type
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
    cutoff_date = effective_date or None

    def should_overwrite_existing(row_date):
        return cutoff_date is not None and str(row_date) < str(cutoff_date)

    for table in [
        "raw_eod_prices",
        "adjusted_eod_prices",
        "share_history",
        "marketcap",
        "indicators",
    ]:
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
                mutable = list(row)
                mutable[symbol_idx] = new_symbol
                if row[date_idx] in existing_dates:
                    if should_overwrite_existing(row[date_idx]):
                        assignments = ",".join(
                            f"{col}=excluded.{col}"
                            for col in cols
                            if col not in {"symbol", "date"}
                        )
                        conn.execute(
                            f"""
                            INSERT INTO {table} VALUES ({placeholders})
                            ON CONFLICT(symbol, date) DO UPDATE SET
                                {assignments}
                            """,
                            mutable,
                        )
                    continue
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
        if should_overwrite_existing(ex_date):
            conn.execute(
                """
                INSERT INTO corporate_actions (
                    symbol, ex_date, action_type, value, source, note
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, ex_date, action_type, source) DO UPDATE SET
                    value=excluded.value,
                    note=excluded.note
                """,
                (new_symbol, ex_date, action_type, value, src, row_note),
            )
        else:
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
