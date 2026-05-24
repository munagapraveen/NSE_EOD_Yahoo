"""
corporate_actions.py -- Split / Bonus detector & refresher
==========================================================
Fetches NSE split/bonus actions since last run and re-downloads
adjusted prices for affected stocks.

Usage:
    python corporate_actions.py              # auto from last run date
    python corporate_actions.py --days 30    # override: last 30 days
    python corporate_actions.py --dry-run    # preview, no DB changes

Requirements:
    pip install kiteconnect pandas requests
"""

import sys
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

from analytics_store import rebuild_analytics_for_symbols
from config import DEFAULT_DAYS_FIRST_RUN, YEARS_BACK
from db import (
    get_connection,
    get_last_run_date,
    log_corporate_action,
    mark_action_refreshed,
    save_run_date,
    setup_schema,
)
from kite_utils import get_instrument_token, get_kite, run_parallel_ohlcv_tasks
from logger import get_logger

log = get_logger(__name__)
TO_DATE = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": (
        "https://www.nseindia.com/companies-listing/"
        "corporate-filings-actions"
    ),
}


def fetch_nse_actions(from_date, to_date, action_type):
    """Fetch split or bonus actions from NSE's public API."""
    session = requests.Session()
    try:
        session.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=10)
        time.sleep(1)
        resp = session.get(
            "https://www.nseindia.com/api/corporates-corporateActions",
            headers=NSE_HEADERS,
            params={
                "index": "equities",
                "from_date": from_date.strftime("%d-%m-%Y"),
                "to_date": to_date.strftime("%d-%m-%Y"),
                "subject": action_type,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error(f"Failed to fetch {action_type} actions: {exc}")
        return pd.DataFrame()

    if not data:
        return pd.DataFrame()

    df = pd.DataFrame(data)
    col = {c.lower(): c for c in df.columns}

    rename = {}
    for target, keys in [
        ("symbol", ["symbol"]),
        ("company", ["comp", "company"]),
        ("ex_date", ["exdate", "ex_date", "ex date"]),
        ("details", ["subject", "purpose"]),
    ]:
        for key in keys:
            if key in col:
                rename[col[key]] = target
                break

    df.rename(columns=rename, inplace=True)
    df["action_type"] = action_type

    if "series" in df.columns:
        df = df[df["series"] == "EQ"]

    keep = [
        column
        for column in ["symbol", "company", "ex_date", "action_type", "details"]
        if column in df.columns
    ]
    return df[keep].reset_index(drop=True)


def get_all_actions(from_date, to_date):
    """Fetch splits and bonuses combined."""
    log.info(f"Fetching splits  {from_date.date()} to {to_date.date()} ...")
    splits = fetch_nse_actions(from_date, to_date, "split")
    time.sleep(1)
    log.info(f"Fetching bonuses {from_date.date()} to {to_date.date()} ...")
    bonuses = fetch_nse_actions(from_date, to_date, "bonus")

    if splits.empty and bonuses.empty:
        return pd.DataFrame()

    df = pd.concat([splits, bonuses], ignore_index=True)
    df.drop_duplicates(subset=["symbol", "action_type"], inplace=True)
    return df


def save_adjusted_symbol(conn, task, df):
    """Replace one symbol's stored history with adjusted candles."""
    from db import delete_symbol_data, insert_eod_rows, log_adjustment

    symbol = task["symbol"]
    rows_deleted = delete_symbol_data(conn, symbol)
    log.info(f"  {symbol}: deleted {rows_deleted:,} old rows")

    if df.empty:
        log.warning(f"  {symbol}: no data returned from Kite")
        return {"fail": 1}

    df["symbol"] = symbol
    df["company_name"] = task["company_name"]
    df["segment"] = task["segment"]
    df["instrument_type"] = "EQ"
    df["isin"] = task["isin"]

    insert_eod_rows(conn, df)
    log_adjustment(
        conn,
        symbol,
        task.get("reason", "split/bonus adjustment"),
        rows_deleted,
        len(df),
    )
    mark_action_refreshed(conn, symbol)
    log.info(f"  {symbol}: inserted {len(df):,} adjusted rows")
    return {"success": 1, "total_rows": len(df)}


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    manual_days = None

    for i, arg in enumerate(args):
        if arg == "--days" and i + 1 < len(args):
            try:
                manual_days = int(args[i + 1])
            except ValueError:
                pass

    results = {"success": 0, "fail": 0}
    actions_df = pd.DataFrame()

    with get_connection() as conn:
        setup_schema(conn)

        if manual_days:
            from_date = TO_DATE - timedelta(days=manual_days)
            date_source = f"manual (--days {manual_days})"
        else:
            last_run = get_last_run_date(conn, "corporate_actions")
            if last_run:
                from_date = last_run + timedelta(days=1)
                date_source = f"last run on {last_run.date()}"
            else:
                from_date = TO_DATE - timedelta(days=DEFAULT_DAYS_FIRST_RUN)
                date_source = f"first run -- last {DEFAULT_DAYS_FIRST_RUN} days"

        if not manual_days and from_date > TO_DATE:
            log.info("Already up to date -- ran today. Use --days N to force.")
            return

        log.info("")
        log.info("=" * 55)
        log.info("CORPORATE ACTIONS")
        log.info("=" * 55)
        log.info(f"Date source : {date_source}")
        log.info(f"Date range  : {from_date.date()} to {TO_DATE.date()}")
        if dry_run:
            log.info("DRY RUN -- no DB changes")
        log.info("")

        actions_df = get_all_actions(from_date, TO_DATE)

        if actions_df.empty:
            log.info("No split or bonus actions found.")
            if not dry_run:
                save_run_date(conn, "corporate_actions", "success - no actions")
            return

        log.info(f"Found {len(actions_df)} action(s):")
        print(actions_df.to_string(index=False))
        print("")

        if dry_run:
            log.info("Dry run done -- remove --dry-run to apply.")
            return

        kite = get_kite()
        from_refresh = TO_DATE - timedelta(days=YEARS_BACK * 365)
        symbols = actions_df["symbol"].dropna().astype(str).unique().tolist()

        for _, row in actions_df.iterrows():
            log_corporate_action(
                conn,
                row.get("symbol", ""),
                row.get("action_type", ""),
                row.get("ex_date", ""),
                row.get("details", ""),
            )

        tasks = []
        lookup_fail = 0
        for symbol in symbols:
            try:
                token, company_name, segment, instrument_type, isin = (
                    get_instrument_token(kite, symbol)
                )
                tasks.append({
                    "symbol": symbol,
                    "token": token,
                    "company_name": company_name,
                    "segment": segment,
                    "isin": isin,
                    "fetch_from": from_refresh,
                    "to_date": TO_DATE,
                    "reason": "split/bonus adjustment",
                })
            except Exception as exc:
                log.error(f"  {symbol}: token lookup failed -- {exc}")
                lookup_fail += 1

        log.info(f"Refreshing {len(tasks)} affected symbol(s) ...")
        results = run_parallel_ohlcv_tasks(
            kite,
            tasks,
            save_adjusted_symbol,
            workers=3,
            progress_label="Corporate Actions Refresh",
        )
        results["fail"] += lookup_fail

        if tasks:
            rebuild_analytics_for_symbols([task["symbol"] for task in tasks])

        save_run_date(conn, "corporate_actions", "success")

    log.info("")
    log.info("=" * 55)
    log.info("COMPLETE")
    log.info("=" * 55)
    log.info(f"  Actions   : {len(actions_df)}")
    log.info(f"  Refreshed : {results['success']} symbols")
    log.info(f"  Failed    : {results['fail']} symbols")
    log.info(f"  Next run auto-fetches from {TO_DATE.date()}")
    log.info("=" * 55)


if __name__ == "__main__":
    main()
