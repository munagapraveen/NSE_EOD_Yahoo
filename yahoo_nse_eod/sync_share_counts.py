"""Download historical shares outstanding from Yahoo Finance."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import sys
import threading
import time
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

from config import DEFAULT_HISTORY_START, FAILED_SHARES_FILE
from db import get_active_symbols, get_connection, setup_schema, upsert_share_history
from logger import get_logger

log = get_logger(__name__)

MAX_WORKERS = 8
DB_LOCK = threading.Lock()


def fetch_share_history(yahoo_symbol, start):
    ticker = yf.Ticker(yahoo_symbol)
    series = ticker.get_shares_full(start=start)
    if series is None or len(series) == 0:
        return pd.DataFrame(columns=["date", "shares_outstanding"])

    df = series.reset_index()
    df.columns = ["date", "shares_outstanding"]
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df["shares_outstanding"] = pd.to_numeric(
        df["shares_outstanding"], errors="coerce"
    )
    df = df.dropna(subset=["shares_outstanding"])
    return df


def parse_args(args):
    options = {
        "limit": None,
        "only_missing": "--only-missing" in args,
        "start": DEFAULT_HISTORY_START,
        "sleep_secs": 0.25,
        "retry_sleep_secs": 0.5,
        "workers": 4,
    }
    for i, arg in enumerate(args):
        if arg == "--limit" and i + 1 < len(args):
            options["limit"] = int(args[i + 1])
        if arg == "--start" and i + 1 < len(args):
            options["start"] = args[i + 1].strip()
        if arg == "--recent-days" and i + 1 < len(args):
            recent_days = max(1, int(args[i + 1]))
            options["start"] = (
                datetime.today() - timedelta(days=recent_days)
            ).strftime("%Y-%m-%d")
        if arg == "--sleep" and i + 1 < len(args):
            options["sleep_secs"] = max(0.0, float(args[i + 1]))
        if arg == "--retry-sleep" and i + 1 < len(args):
            options["retry_sleep_secs"] = max(0.0, float(args[i + 1]))
        if arg == "--workers" and i + 1 < len(args):
            options["workers"] = max(1, min(MAX_WORKERS, int(args[i + 1])))
    return options


def load_target_symbols(limit=None, only_missing=False):
    with get_connection() as conn:
        setup_schema(conn)
        symbols = get_active_symbols(conn)
        
        # Only fetch shares for mainboard stocks, not ETFs or Indices
        symbols = symbols[symbols["instrument_type"] == "STOCK"].copy()
        
        if limit:
            symbols = symbols.head(limit).copy()
        if only_missing:
            existing = pd.read_sql("""
                SELECT DISTINCT symbol
                FROM share_history
            """, conn)
            done = set(existing["symbol"].astype(str).str.upper().tolist())
            symbols = symbols[
                ~symbols["symbol"].astype(str).str.upper().isin(done)
            ].copy()
    return symbols


def _records_from_share_df(symbol, share_df):
    return [
        {
            "symbol": symbol,
            "date": row.date,
            "shares_outstanding": float(row.shares_outstanding),
            "source": "yahoo",
        }
        for row in share_df.itertuples(index=False)
    ]


def persist_share_records(records):
    if not records:
        return
    with DB_LOCK:
        with get_connection() as conn:
            upsert_share_history(conn, records)


def save_failure_report(failed_rows):
    if not failed_rows:
        if FAILED_SHARES_FILE.exists():
            FAILED_SHARES_FILE.unlink()
        return

    FAILED_SHARES_FILE.parent.mkdir(exist_ok=True)
    with FAILED_SHARES_FILE.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["symbol", "yahoo_symbol", "stage", "error"],
        )
        writer.writeheader()
        writer.writerows(failed_rows)


def _fetch_one(row, start, fetcher):
    share_df = fetcher(row.yahoo_symbol, start)
    if share_df.empty:
        return {
            "status": "empty",
            "symbol": row.symbol,
            "yahoo_symbol": row.yahoo_symbol,
            "rows": 0,
            "records": [],
            "error": "no share history returned",
        }

    records = _records_from_share_df(row.symbol, share_df)
    return {
        "status": "ok",
        "symbol": row.symbol,
        "yahoo_symbol": row.yahoo_symbol,
        "rows": len(records),
        "records": records,
        "error": "",
    }


def run_share_download(
    symbols,
    start=DEFAULT_HISTORY_START,
    workers=4,
    sleep_secs=0.25,
    retry_sleep_secs=0.5,
    fetcher=fetch_share_history,
    persist_func=persist_share_records,
):
    if symbols.empty:
        log.info("No symbols selected for share-history download.")
        return {
            "total_rows": 0,
            "failed": [],
            "retried": [],
            "success_symbols": 0,
        }

    total_rows = 0
    success_symbols = 0
    failed = []
    workers = max(1, min(MAX_WORKERS, int(workers)))

    log.info("")
    log.info("=" * 55)
    log.info("DOWNLOADING HISTORICAL SHARES OUTSTANDING")
    log.info("=" * 55)
    log.info(f"Total symbols : {len(symbols):,}")
    log.info(f"Workers       : {workers}")
    log.info("")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_fetch_one, row, start, fetcher): row
            for row in symbols.itertuples(index=False)
        }
        for idx, future in enumerate(as_completed(futures), start=1):
            row = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "status": "failed",
                    "symbol": row.symbol,
                    "yahoo_symbol": row.yahoo_symbol,
                    "rows": 0,
                    "records": [],
                    "error": str(exc),
                }

            if result["status"] == "ok":
                persist_func(result["records"])
                total_rows += result["rows"]
                success_symbols += 1
                log.info(
                    f"[{idx}/{len(symbols)}] {result['symbol']}: stored "
                    f"{result['rows']:,} share-history rows"
                )
            else:
                failed.append({
                    "symbol": result["symbol"],
                    "yahoo_symbol": result["yahoo_symbol"],
                    "stage": "parallel",
                    "error": result["error"],
                })
                log.warning(
                    f"[{idx}/{len(symbols)}] {result['symbol']}: share fetch "
                    f"{'returned no data' if result['status'] == 'empty' else 'failed'} "
                    f"({result['error']})"
                )
            time.sleep(sleep_secs)

    retried = []
    if failed:
        log.info("")
        log.info(f"Retrying {len(failed):,} failed/empty symbols sequentially ...")
        for idx, entry in enumerate(failed, start=1):
            try:
                row = type("Row", (), entry)
                result = _fetch_one(row, start, fetcher)
                if result["status"] == "ok":
                    persist_func(result["records"])
                    total_rows += result["rows"]
                    success_symbols += 1
                    retried.append({
                        "symbol": result["symbol"],
                        "yahoo_symbol": result["yahoo_symbol"],
                        "stage": "retry-success",
                        "error": "",
                    })
                    log.info(
                        f"[retry {idx}/{len(failed)}] {result['symbol']}: recovered "
                        f"and stored {result['rows']:,} rows"
                    )
                else:
                    retried.append({
                        "symbol": entry["symbol"],
                        "yahoo_symbol": entry["yahoo_symbol"],
                        "stage": "retry-failed",
                        "error": result["error"],
                    })
                    log.warning(
                        f"[retry {idx}/{len(failed)}] {entry['symbol']}: still no data "
                        f"({result['error']})"
                    )
            except Exception as exc:
                retried.append({
                    "symbol": entry["symbol"],
                    "yahoo_symbol": entry["yahoo_symbol"],
                    "stage": "retry-failed",
                    "error": str(exc),
                })
                log.warning(
                    f"[retry {idx}/{len(failed)}] {entry['symbol']}: retry failed ({exc})"
                )
            time.sleep(retry_sleep_secs)

    unresolved = [entry for entry in retried if entry["stage"] == "retry-failed"]
    save_failure_report(unresolved)
    log.info(
        f"Share-history download complete: {total_rows:,} rows stored "
        f"across {success_symbols:,} symbols."
    )
    if unresolved:
        log.warning(
            f"{len(unresolved):,} symbols still failed. Saved report: {FAILED_SHARES_FILE}"
        )

    return {
        "total_rows": total_rows,
        "failed": failed,
        "retried": retried,
        "success_symbols": success_symbols,
    }


def main():
    options = parse_args(sys.argv[1:])
    symbols = load_target_symbols(
        limit=options["limit"],
        only_missing=options["only_missing"],
    )
    run_share_download(
        symbols,
        start=options["start"],
        workers=options["workers"],
        sleep_secs=options["sleep_secs"],
        retry_sleep_secs=options["retry_sleep_secs"],
    )


if __name__ == "__main__":
    main()
