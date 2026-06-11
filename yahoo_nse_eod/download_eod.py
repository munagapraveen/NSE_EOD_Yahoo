"""Download NSE EOD history from Yahoo Finance into the standalone database."""

import csv
import sys
import time
from datetime import datetime, timedelta

import pandas as pd

from config import DEFAULT_BATCH_SIZE, DEFAULT_HISTORY_START, FAILED_EOD_FILE, YAHOO_SUFFIX
from adjust_splits import rebuild_symbols, refresh_latest_rows
from db import (
    get_active_symbols,
    get_connection,
    get_symbol_last_dates,
    insert_raw_prices,
    setup_schema,
    upsert_corporate_actions,
)
from logger import get_logger
from yahoo_client import download_history_batch
from sync_symbols import run_sync
from sync_share_counts import run_share_download

log = get_logger(__name__)

MAX_BATCH_RETRIES = 3


def parse_args(args):
    options = {
        "bootstrap": "--bootstrap" in args,
        "sync": "--sync" in args,
        "refetch_last": "--refetch-last" in args,
        "limit": None,
        "batch_size": DEFAULT_BATCH_SIZE,
        "retry_sleep_secs": 2.0,
        "single_retry_sleep_secs": 0.6,
        "symbols": None,
    }
    for i, arg in enumerate(args):
        if arg == "--limit" and i + 1 < len(args):
            options["limit"] = int(args[i + 1])
        if arg == "--batch-size" and i + 1 < len(args):
            options["batch_size"] = max(1, int(args[i + 1]))
        if arg == "--retry-sleep" and i + 1 < len(args):
            options["retry_sleep_secs"] = max(0.0, float(args[i + 1]))
        if arg == "--single-retry-sleep" and i + 1 < len(args):
            options["single_retry_sleep_secs"] = max(0.0, float(args[i + 1]))
        if arg == "--symbols" and i + 1 < len(args):
            raw = args[i + 1].strip()
            options["symbols"] = [
                part.strip().upper() for part in raw.split(",") if part.strip()
            ]
    return options


def chunked(df, size):
    for idx in range(0, len(df), size):
        yield df.iloc[idx: idx + size].copy()


def load_target_symbols(limit=None, only_symbols=None):
    with get_connection() as conn:
        setup_schema(conn)
        symbols = get_active_symbols(conn)
        if only_symbols:
            wanted = {symbol.strip().upper() for symbol in only_symbols if symbol.strip()}
            symbols = symbols[
                symbols["symbol"].astype(str).str.upper().isin(wanted)
            ].copy()
        if limit:
            symbols = symbols.head(limit).copy()
        last_dates = get_symbol_last_dates(conn, symbols["symbol"].tolist())
    return symbols, last_dates


def build_action_records(df):
    """
    Yahoo corporate actions (splits/dividends) are now ignored.
    We rely solely on verified data from NSE synced via sync_corporate_actions.py.
    """
    return []


def collect_touched_dates(history):
    touched_symbols = (
        history["symbol"].dropna().astype(str).str.upper().unique().tolist()
    )
    touched_dates = (
        history[["symbol", "date"]]
        .dropna()
        .assign(symbol=lambda df: df["symbol"].astype(str).str.upper())
        .groupby("symbol")["date"]
        .apply(lambda series: sorted(series.astype(str).unique().tolist()))
        .to_dict()
    )
    return touched_symbols, touched_dates


def identify_missing_symbols(batch, history):
    requested = set(batch["symbol"].astype(str).str.upper().tolist())
    received = set(history["symbol"].dropna().astype(str).str.upper().tolist())
    return sorted(requested - received)


def persist_history(history, bootstrap):
    action_records = build_action_records(history)
    touched_symbols, touched_dates = collect_touched_dates(history)
    with get_connection() as conn:
        insert_raw_prices(conn, history)
        if action_records:
            upsert_corporate_actions(conn, action_records)
    # Skip batch-level rebuilding on bootstrap; single rebuild will run at the end (M-9)
    if not bootstrap:
        if touched_dates:
            refresh_latest_rows(touched_dates)
    return {
        "rows": len(history),
        "actions": len(action_records),
        "touched_symbols": touched_symbols,
        "touched_dates": touched_dates,
    }


def is_probable_rate_limit_error(exc):
    text = str(exc).lower()
    return (
        "too many requests" in text or
        "rate limit" in text or
        "429" in text
    )


def download_with_retries(batch, start, downloader=download_history_batch, retry_sleep_secs=2.0):
    last_exc = None
    for attempt in range(1, MAX_BATCH_RETRIES + 1):
        try:
            history = downloader(batch, start=start, end=None)
            if history is not None and not history.empty:
                missing_symbols = identify_missing_symbols(batch, history)
                if not missing_symbols:
                    return history, []
                log.warning(
                    "Batch download returned partial data; "
                    f"{len(missing_symbols):,} symbols missing. Falling back for them."
                )
                missing_batch = batch[
                    batch["symbol"].astype(str).str.upper().isin(missing_symbols)
                ].copy()
                recovered, failures = fallback_single_symbol_download(
                    missing_batch, start, downloader, retry_sleep_secs
                )
                if recovered is not None and not recovered.empty:
                    history = pd.concat([history, recovered], ignore_index=True)
                return history, failures
            last_exc = RuntimeError("no data returned")
            log.warning(
                f"Batch download returned no data on attempt {attempt}/{MAX_BATCH_RETRIES}"
            )
        except Exception as exc:
            last_exc = exc
            log.warning(
                f"Batch download failed on attempt {attempt}/{MAX_BATCH_RETRIES} ({exc})"
            )
        if attempt < MAX_BATCH_RETRIES:
            time.sleep(retry_sleep_secs * attempt)

    log.warning("Falling back to per-symbol retries for this batch.")
    return fallback_single_symbol_download(batch, start, downloader, retry_sleep_secs)


def fallback_single_symbol_download(batch, start, downloader, retry_sleep_secs):
    frames = []
    failures = []
    for row in batch.itertuples(index=False):
        one = pd.DataFrame([row._asdict()])
        try:
            history = downloader(one, start=start, end=None)
            if history is None or history.empty:
                failures.append({
                    "symbol": row.symbol,
                    "yahoo_symbol": row.yahoo_symbol,
                    "error": "no data returned",
                    "stage": "single-fallback",
                })
            else:
                frames.append(history)
        except Exception as exc:
            failures.append({
                "symbol": row.symbol,
                "yahoo_symbol": row.yahoo_symbol,
                "error": str(exc),
                "stage": "single-fallback",
            })
            if is_probable_rate_limit_error(exc):
                time.sleep(retry_sleep_secs)

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return combined, failures


def save_failure_report(failures):
    if not failures:
        if FAILED_EOD_FILE.exists():
            FAILED_EOD_FILE.unlink()
        return
    FAILED_EOD_FILE.parent.mkdir(exist_ok=True)
    with FAILED_EOD_FILE.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["symbol", "yahoo_symbol", "stage", "error"],
        )
        writer.writeheader()
        writer.writerows(failures)


def check_market_availability(last_dates, refetch_last=False):
    """
    Check if there is actually any new data on Yahoo for a major symbol.
    Returns (True, latest_date) if we should proceed, (False, last_date) otherwise.
    """
    if not last_dates:
        return True, None

    if len(last_dates) < 10:
        # Skip canary check for small subsets to avoid false negatives (M-4)
        return True, None

    # Use a priority list of liquid benchmark symbols for canary check (M-4)
    canary_sym = None
    for sym in ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK"]:
        if sym in last_dates:
            canary_sym = sym
            break

    if not canary_sym:
        # Fallback to skipping canary check if no major benchmark stock is present
        return True, None

    last_stored = last_dates[canary_sym]
    yahoo_sym = f"{canary_sym}{YAHOO_SUFFIX}"
    
    log.info(f"Checking market availability using {canary_sym}...")
    try:
        # Fetch just the last day from Yahoo
        import yfinance as yf
        ticker = yf.Ticker(yahoo_sym)
        df = ticker.history(period="1d")
        if df.empty:
            return False, last_stored
        
        latest_yahoo = df.index[-1].strftime("%Y-%m-%d")
        
        if refetch_last:
            return True, latest_yahoo
            
        if latest_yahoo > last_stored:
            return True, latest_yahoo
        else:
            return False, last_stored
    except Exception as e:
        log.warning(f"Market check failed ({e}). Proceeding with full check just in case.")
        return True, None


def run_eod_download(
    symbols,
    last_dates,
    bootstrap=False,
    refetch_last=False,
    batch_size=DEFAULT_BATCH_SIZE,
    downloader=download_history_batch,
    retry_sleep_secs=2.0,
    single_retry_sleep_secs=0.6,
):
    if symbols.empty:
        log.warning("No active symbols found. Run sync_symbols.py first.")
        return {
            "total_rows": 0,
            "total_actions": 0,
            "failures": [],
        }

    # Sort symbols by last date to group similar start dates together (M-3)
    def get_sort_key(sym):
        val = last_dates.get(sym)
        return val if val else "1970-01-01"
    
    symbols["_sort_key"] = symbols["symbol"].apply(get_sort_key)
    symbols = symbols.sort_values("_sort_key").drop(columns=["_sort_key"])

    # Optimization: Canary Check
    if not bootstrap:
        has_new_data, latest_date = check_market_availability(last_dates, refetch_last)
        if not has_new_data:
            log.info(f"Market check: No new data found beyond {latest_date}. Skipping full download.")
            return {
                "total_rows": 0,
                "total_actions": 0,
                "failures": [],
            }
        else:
            log.info(f"Market check: New data detected (Latest: {latest_date}). Proceeding...")

    total_rows = 0
    total_actions = 0
    failures = []
    today = datetime.today().strftime("%Y-%m-%d")

    for batch_no, batch in enumerate(chunked(symbols, batch_size), start=1):
        if bootstrap:
            start = DEFAULT_HISTORY_START
        else:
            batch_last_dates = [
                datetime.strptime(last_dates[symbol], "%Y-%m-%d")
                for symbol in batch["symbol"]
                if symbol in last_dates and last_dates[symbol]
            ]
            if batch_last_dates:
                min_last = min(batch_last_dates)
                # If refetch_last is True, start from the last date to overwrite/refresh
                # If last date is today, always start from today to refresh mid-day prices
                if refetch_last or min_last.strftime("%Y-%m-%d") == today:
                    start = min_last.strftime("%Y-%m-%d")
                else:
                    start = (min_last + timedelta(days=1)).strftime("%Y-%m-%d")
            else:
                start = DEFAULT_HISTORY_START

        if not bootstrap and start > today:
            log.info(
                f"Batch {batch_no}: already up to date (last date: {start}) - skipping."
            )
            continue

        log.info(
            f"Batch {batch_no}: downloading {len(batch):,} symbols "
            f"from {start} to {today}"
        )
        history, batch_failures = download_with_retries(
            batch,
            start,
            downloader=downloader,
            retry_sleep_secs=retry_sleep_secs,
        )
        if batch_failures:
            for failure in batch_failures:
                if failure.get("stage") == "single-fallback" and is_probable_rate_limit_error(failure.get("error", "")):
                    time.sleep(single_retry_sleep_secs)
            failures.extend(batch_failures)

        if history.empty:
            log.warning(f"Batch {batch_no}: no data stored after retries")
            continue

        persisted = persist_history(history, bootstrap=bootstrap)
        total_rows += persisted["rows"]
        total_actions += persisted["actions"]
        log.info(
            f"Batch {batch_no}: stored {persisted['rows']:,} rows "
            f"and {persisted['actions']:,} action records; "
            f"{'rebuilt full adjusted prices/MAs' if bootstrap else 'updated adjusted prices/MAs'} "
            f"for {len(persisted['touched_symbols']):,} symbols"
        )

    save_failure_report(failures)
    log.info(f"Download complete: {total_rows:,} rows, {total_actions:,} actions.")
    if failures:
        log.warning(f"{len(failures):,} symbols still failed. Saved report: {FAILED_EOD_FILE}")
    return {
        "total_rows": total_rows,
        "total_actions": total_actions,
        "failures": failures,
    }


def main():
    options = parse_args(sys.argv[1:])
    if options["sync"] or options["bootstrap"]:
        log.info(f"Starting symbol sync before {'bootstrap' if options['bootstrap'] else 'refresh'}...")
        run_sync()

    symbols, last_dates = load_target_symbols(
        limit=options["limit"],
        only_symbols=options["symbols"],
    )
    run_eod_download(
        symbols,
        last_dates,
        bootstrap=options["bootstrap"],
        refetch_last=options["refetch_last"],
        batch_size=options["batch_size"],
        retry_sleep_secs=options["retry_sleep_secs"],
        single_retry_sleep_secs=options["single_retry_sleep_secs"],
    )

    if options["bootstrap"]:
        log.info("Bootstrap complete: Automatically downloading historical share counts...")
        run_share_download(symbols)

        log.info("Share download complete: Performing final rebuild of all symbols to calculate market cap...")
        rebuild_symbols(symbols["symbol"].tolist())
        log.info("Full bootstrap process complete. Everything is ready for the screener.")


if __name__ == "__main__":
    main()
