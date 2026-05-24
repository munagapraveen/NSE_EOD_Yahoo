"""
downloader.py -- NSE EOD Downloader (Parallel)
================================================
Downloads EOD OHLCV for all NSE EQ + BE stocks into SQLite.
On subsequent runs fetches only missing dates (incremental).

SPEED OPTIMISATIONS vs old version:
  - Parallel workers (default 3) -- saturates Kite's 3 req/s limit properly
  - Token bucket rate limiter    -- precise rate control, no wasted sleep time
  - DB writes batched per worker -- reduces lock contention
  - Symbols pre-filtered in DB   -- skips already-up-to-date symbols before
                                    spawning any threads
  - Instrument list cached once  -- not re-fetched per symbol

Expected time:
  - Daily update (1 day per symbol) : 3-5 minutes  (was ~60 min)
  - Full 5-year download            : 20-30 minutes (was 3+ hours)

Usage:
    python downloader.py
    python downloader.py --workers 3    # adjust parallel workers (max 3)

Requirements:
    pip install kiteconnect pandas tqdm
"""

import os
import sys
import time
from datetime import datetime, timedelta

from config import GENERATE_TOKEN, DB_FILE, YEARS_BACK
from analytics_store import rebuild_analytics_for_symbols
from db import get_connection, setup_schema, get_last_date, insert_eod_rows
from kite_utils import (
    generate_token,
    get_kite,
    get_nse_instruments,
    run_parallel_ohlcv_tasks,
)
from logger import get_logger
from nse_master import run_master_sync

log = get_logger(__name__)

TO_DATE   = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
FROM_DATE = TO_DATE - timedelta(days=YEARS_BACK * 365)

def save_download_result(conn, task, df):
    """Persist one downloaded symbol and return summary counters."""
    if df.empty:
        return {"fail": 1}

    df["symbol"] = task["symbol"]
    df["company_name"] = task["company_name"]
    df["segment"] = task["segment"]
    df["instrument_type"] = "EQ"
    df["isin"] = task["isin"]

    insert_eod_rows(conn, df)
    return {"success": 1, "total_rows": len(df)}


# ===========================================================================
# MAIN
# ===========================================================================

def main():

    if GENERATE_TOKEN:
        generate_token()
        return

    # Parse --workers argument
    n_workers = 3   # default -- matches Kite's 3 req/s limit
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--workers" and i + 1 < len(args):
            try:
                n_workers = max(1, min(3, int(args[i + 1])))
            except ValueError:
                pass

    kite = get_kite()

    log.info("=" * 55)
    log.info("NSE EOD DOWNLOADER  (parallel)")
    log.info("=" * 55)
    log.info(f"Database   : {os.path.abspath(DB_FILE)}")
    log.info(f"Date range : {FROM_DATE.date()} to {TO_DATE.date()}")
    log.info(f"Segments   : EQ + BE (T2T/ASM), stored as EQ")
    log.info(f"Workers    : {n_workers}  (rate limit: 3 req/s)")
    log.info("")

    t_start = time.time()

    # Step 1 -- Sync clean NSE master universe (equity + ETF)
    master_df, _ = run_master_sync()
    master_symbols = set(master_df["symbol"].astype(str).str.upper().tolist())

    # Step 2 -- Get Zerodha instrument list once
    instruments_df = get_nse_instruments(kite)
    instruments_df = instruments_df[
        instruments_df["base_symbol"].astype(str).str.upper().isin(master_symbols)
    ].copy()

    matched_symbols = set(instruments_df["base_symbol"].astype(str).str.upper().tolist())
    missing_symbols = sorted(master_symbols - matched_symbols)

    # Step 3 -- Pre-check DB to find which symbols need updating
    # Done in a single connection before spawning threads
    log.info("Checking which symbols need updating ...")
    tasks       = []
    skip_count  = 0

    with get_connection() as conn:
        setup_schema(conn)
        for _, row in instruments_df.iterrows():
            symbol       = row["base_symbol"]
            token        = int(row["instrument_token"])
            company_name = row.get("name", "")
            segment      = row.get("segment", "")
            isin         = row.get("isin", "")

            last_date = get_last_date(conn, symbol)

            if last_date is None:
                fetch_from = FROM_DATE
            else:
                fetch_from = (
                    datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)
                )
                if fetch_from > TO_DATE:
                    skip_count += 1
                    continue

            tasks.append({
                "symbol": symbol,
                "token": token,
                "company_name": company_name,
                "segment": segment,
                "isin": isin,
                "fetch_from": fetch_from,
                "to_date": TO_DATE,
            })

    total      = len(tasks)
    log.info(f"  Master universe    : {len(master_symbols):,} symbols")
    log.info(f"  Matched in Zerodha : {len(matched_symbols):,} symbols")
    if missing_symbols:
        sample = ", ".join(missing_symbols[:15])
        suffix = " ..." if len(missing_symbols) > 15 else ""
        log.warning(f"  Missing in Zerodha : {len(missing_symbols):,} symbols")
        log.warning(f"    Sample missing   : {sample}{suffix}")
    log.info(f"  Already up to date : {skip_count:,} symbols")
    log.info(f"  To download        : {total:,} symbols")
    log.info("")

    if not tasks:
        log.info("Nothing to download -- all symbols up to date.")
        return

    # Step 3 -- Start shared parallel runner
    log.info(f"Starting {n_workers} parallel worker(s) ...")

    results = run_parallel_ohlcv_tasks(
        kite,
        tasks,
        save_download_result,
        workers=n_workers,
        progress_label="Daily EOD Download",
    )

    updated_symbols = [task["symbol"] for task in tasks]
    if updated_symbols:
        rebuild_analytics_for_symbols(updated_symbols)

    # Step 6 -- Summary
    elapsed    = time.time() - t_start
    db_size_mb = os.path.getsize(DB_FILE) / (1024 * 1024)
    mins, secs = divmod(int(elapsed), 60)

    log.info("")
    log.info("=" * 55)
    log.info("COMPLETE")
    log.info("=" * 55)
    log.info(f"  Updated   : {results['success']:,} symbols")
    log.info(f"  Skipped   : {skip_count:,} (already up to date)")
    log.info(f"  Failed    : {results['fail']:,} symbols")
    log.info(f"  New rows  : {results['total_rows']:,} candles")
    log.info(f"  DB size   : {db_size_mb:.1f} MB")
    log.info(f"  Time      : {mins}m {secs}s")
    log.info("=" * 55)


if __name__ == "__main__":
    main()
