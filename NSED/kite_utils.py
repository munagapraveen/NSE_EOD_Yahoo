"""
kite_utils.py — Kite Connect shared utilities
===============================================
All Kite API helpers. Import from here — never duplicate in task scripts.
"""

import time
import threading
import queue
import pandas as pd
from datetime import datetime, timedelta
from kiteconnect import KiteConnect

from config import (
    API_KEY, API_SECRET, ACCESS_TOKEN,
    CHUNK_DAYS, REQUEST_DELAY, ALLOWED_TYPES,
)
from logger import get_logger

log = get_logger(__name__)

# Retry settings for Kite API calls
MAX_RETRIES   = 3
RETRY_BACKOFF = 2   # seconds — doubles on each retry


# ===========================================================================
# CONFIG VALIDATION
# ===========================================================================

def validate_config():
    """
    Checks that API credentials are set before any API call.
    Raises ValueError with a clear message if placeholders are still present.
    """
    missing = []
    if not API_KEY or "your_api_key" in API_KEY:
        missing.append("API_KEY")
    if not API_SECRET or "your_api_secret" in API_SECRET:
        missing.append("API_SECRET")
    if not ACCESS_TOKEN or "your_access_token" in ACCESS_TOKEN:
        missing.append("ACCESS_TOKEN")

    if missing:
        raise ValueError(
            f"\n\n  Missing credentials in config.py: {', '.join(missing)}\n"
            f"  Open config.py and fill in your Zerodha API details.\n"
            f"  Set GENERATE_TOKEN = True to get a fresh ACCESS_TOKEN.\n"
        )


# ===========================================================================
# AUTH
# ===========================================================================

def generate_token():
    """
    Interactive flow to get a fresh daily access token.
    Prints the login URL, prompts for request_token,
    prints the resulting access_token.
    """
    kite = KiteConnect(api_key=API_KEY)
    print("")
    print("=" * 60)
    print("  GENERATE ACCESS TOKEN")
    print("=" * 60)
    print("")
    print("  Step 1: Open this URL in your browser:")
    print("")
    print(f"  {kite.login_url()}")
    print("")
    print("  Step 2: Log in with Zerodha credentials + OTP")
    print("")
    print("  Step 3: After login, browser redirects to a URL like:")
    print("  http://127.0.0.1/?request_token=XXXXXXXXXX&status=success")
    print("")
    print("  Step 4: Copy ONLY the request_token value from that URL")
    print("")
    request_token = input("  Paste request_token here: ").strip()
    session = kite.generate_session(request_token, api_secret=API_SECRET)
    token   = session["access_token"]
    print("")
    print("=" * 60)
    print("  SUCCESS — Your access token for today:")
    print("")
    print(f"  {token}")
    print("")
    print("  Next steps:")
    print("  1. Copy the token above")
    print("  2. Open config.py")
    print("  3. Paste into ACCESS_TOKEN = \"...\"")
    print("  4. Set GENERATE_TOKEN = False")
    print("  5. Save config.py and run again")
    print("=" * 60)
    print("")
    input("  Press Enter to exit...")


def get_kite():
    """
    Returns an authenticated KiteConnect instance.
    Validates config before attempting connection.
    """
    validate_config()
    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(ACCESS_TOKEN)
    return kite


# ===========================================================================
# INSTRUMENTS
# ===========================================================================

def normalize_symbol(symbol):
    """Strips -BE suffix so T2T/ASM stocks use their clean base symbol."""
    return symbol.replace("-BE", "").strip()


def get_nse_instruments(kite):
    """
    Fetches all NSE EQ + BE instruments from Kite.
    Normalizes BE symbols. Deduplicates — EQ takes priority over BE.

    Returns a DataFrame with columns:
        base_symbol, tradingsymbol, instrument_token,
        name, instrument_type, segment, isin
    """
    log.info("Fetching NSE instrument list from Kite ...")
    instruments = kite.instruments("NSE")
    df = pd.DataFrame(instruments)

    df = df[df["instrument_type"].isin(ALLOWED_TYPES)].copy()

    wanted = ["tradingsymbol", "instrument_token",
              "name", "instrument_type", "segment", "isin"]
    df = df[[c for c in wanted if c in df.columns]].copy()

    if "isin" not in df.columns:
        df["isin"] = ""

    df["base_symbol"] = df["tradingsymbol"].apply(normalize_symbol)
    df["_sort"]       = df["instrument_type"].map({"EQ": 0, "BE": 1})
    df.sort_values(["base_symbol", "_sort"], inplace=True)
    df.drop_duplicates(subset=["base_symbol"], keep="first", inplace=True)
    df.drop(columns=["_sort"], inplace=True)
    df.reset_index(drop=True, inplace=True)

    eq_count = (df["instrument_type"] == "EQ").sum()
    be_count = (df["instrument_type"] == "BE").sum()
    log.info(
        f"Found {len(df):,} instruments "
        f"({eq_count:,} EQ + {be_count:,} BE/T2T)"
    )
    return df


def get_instrument_token(kite, symbol):
    """
    Looks up instrument token for a base symbol (EQ or BE).
    Returns (token, company_name, segment, instrument_type, isin).
    Raises ValueError if not found.
    """
    instruments = kite.instruments("NSE")
    df = pd.DataFrame(instruments)
    df = df[df["instrument_type"].isin({"EQ", "BE"})].copy()
    df["base_symbol"] = df["tradingsymbol"].apply(normalize_symbol)

    match = df[df["base_symbol"] == symbol]
    if match.empty:
        raise ValueError(f"Symbol '{symbol}' not found on NSE.")

    eq  = match[match["instrument_type"] == "EQ"]
    row = eq.iloc[0] if not eq.empty else match.iloc[0]

    return (
        int(row["instrument_token"]),
        row.get("name", ""),
        row.get("segment", ""),
        row.get("instrument_type", "EQ"),
        row.get("isin", ""),
    )


# ===========================================================================
# DATE CHUNKING
# ===========================================================================

def date_chunks(from_date, to_date, chunk_days=CHUNK_DAYS):
    """Yields (start, end) date pairs covering the full range."""
    current = from_date
    while current <= to_date:
        end = min(current + timedelta(days=chunk_days - 1), to_date)
        yield current, end
        current = end + timedelta(days=1)


# ===========================================================================
# HISTORICAL DATA FETCH — with exponential backoff retry
# ===========================================================================

def _fetch_chunk(kite, token, chunk_start, chunk_end):
    """
    Fetches one date chunk from Kite with exponential backoff retry.
    Returns list of records or empty list on permanent failure.
    """
    delay = RETRY_BACKOFF
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return kite.historical_data(
                instrument_token=token,
                from_date=chunk_start,
                to_date=chunk_end,
                interval="day",
                continuous=False,
                oi=False,
            )
        except Exception as exc:
            if attempt < MAX_RETRIES:
                log.warning(
                    f"    Chunk {chunk_start.date()} to {chunk_end.date()} "
                    f"failed (attempt {attempt}/{MAX_RETRIES}): {exc} "
                    f"— retrying in {delay}s"
                )
                time.sleep(delay)
                delay *= 2   # exponential backoff
            else:
                log.error(
                    f"    Chunk {chunk_start.date()} to {chunk_end.date()} "
                    f"permanently failed after {MAX_RETRIES} attempts: {exc}"
                )
    return []


def fetch_ohlcv(kite, instrument_token, from_date, to_date):
    """
    Fetches daily OHLCV candles for one instrument over a date range.
    Automatically chunks the request and retries failures with backoff.
    Returns a cleaned DataFrame or empty DataFrame on failure.
    """
    all_records = []

    for chunk_start, chunk_end in date_chunks(from_date, to_date):
        records = _fetch_chunk(kite, instrument_token, chunk_start, chunk_end)
        all_records.extend(records)
        time.sleep(REQUEST_DELAY)

    if not all_records:
        return pd.DataFrame()

    df = pd.DataFrame(all_records)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df.sort_values("date", inplace=True)
    df.drop_duplicates(subset=["date"], inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def refresh_symbol_data(kite, conn, symbol, from_date, to_date,
                        reason="manual refresh"):
    """
    Deletes and re-downloads all historical data for a symbol.
    Used after splits, bonus issues, or any price adjustment event.
    Returns number of rows inserted.
    """
    from db import delete_symbol_data, insert_eod_rows, log_adjustment

    token, company_name, segment, instrument_type, isin = get_instrument_token(
        kite, symbol
    )

    rows_deleted = delete_symbol_data(conn, symbol)
    log.info(f"  {symbol}: deleted {rows_deleted:,} old rows")

    df = fetch_ohlcv(kite, token, from_date, to_date)
    if df.empty:
        log.warning(f"  {symbol}: no data returned from Kite")
        return 0

    df["symbol"]          = symbol
    df["company_name"]    = company_name
    df["segment"]         = segment
    df["instrument_type"] = "EQ"
    df["isin"]            = isin

    insert_eod_rows(conn, df)
    log_adjustment(conn, symbol, reason, rows_deleted, len(df))
    log.info(f"  {symbol}: inserted {len(df):,} adjusted rows")
    return len(df)


class RateLimiter:
    """Token bucket rate limiter shared across worker threads."""

    def __init__(self, rate=3.0):
        self.rate = rate
        self.tokens = rate
        self.last_time = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self):
        """Block until one request token is available."""
        wait = 0.0
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_time
            self.last_time = now
            self.tokens = min(self.rate, self.tokens + elapsed * self.rate)

            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return

            wait = (1.0 - self.tokens) / self.rate
            self.tokens = 0.0

        time.sleep(wait)


def emit_progress(completed, total, label="Downloading"):
    """
    Emit a machine-readable progress line for the GUI.
    """
    if total <= 0:
        pct = 100.0
    else:
        pct = min(100.0, max(0.0, (completed / total) * 100.0))
    print(
        f"PROGRESS|{pct:.1f}|{label}|{completed}|{total}",
        flush=True,
    )


def run_parallel_ohlcv_tasks(
    kite,
    tasks,
    task_handler,
    workers=3,
    rate_limit=3.0,
    progress_label="Downloading",
):
    """
    Shared parallel OHLCV download runner.

    Each task must include:
      - symbol
      - token
      - fetch_from
      - to_date

    `task_handler(conn, task, df)` should persist the fetched DataFrame and
    return a dict with any of: success, fail, skipped, total_rows.
    """
    if not tasks:
        emit_progress(1, 1, progress_label)
        return {
            "success": 0,
            "fail": 0,
            "skipped": 0,
            "total_rows": 0,
            "completed": 0,
            "total": 0,
        }

    task_queue = queue.Queue()
    for task in tasks:
        task_queue.put(task)

    results = {
        "success": 0,
        "fail": 0,
        "skipped": 0,
        "total_rows": 0,
        "completed": 0,
        "total": len(tasks),
    }
    results_lock = threading.Lock()
    rate_limiter = RateLimiter(rate=rate_limit)

    def worker():
        from db import get_connection

        with get_connection() as conn:
            while True:
                try:
                    task = task_queue.get(timeout=1)
                except queue.Empty:
                    break

                try:
                    rate_limiter.acquire()
                    df = fetch_ohlcv(
                        kite,
                        task["token"],
                        task["fetch_from"],
                        task["to_date"],
                    )
                    outcome = task_handler(conn, task, df) or {}
                except Exception as exc:
                    log.error(f"  {task.get('symbol', 'UNKNOWN')}: {exc}")
                    outcome = {"fail": 1}
                finally:
                    with results_lock:
                        results["success"] += int(outcome.get("success", 0))
                        results["fail"] += int(outcome.get("fail", 0))
                        results["skipped"] += int(outcome.get("skipped", 0))
                        results["total_rows"] += int(outcome.get("total_rows", 0))
                        results["completed"] += 1
                        emit_progress(
                            results["completed"],
                            results["total"],
                            progress_label,
                        )
                    task_queue.task_done()

    thread_count = max(1, min(3, int(workers)))
    threads = [
        threading.Thread(target=worker, daemon=True)
        for _ in range(thread_count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    return results
