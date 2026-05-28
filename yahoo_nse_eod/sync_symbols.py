"""Sync active NSE symbols into the standalone Yahoo/NSE EOD database."""

from datetime import datetime

import pandas as pd
from config import YAHOO_SUFFIX, INDEX_MAP
from db import get_connection, mark_missing_symbols_inactive, setup_schema, upsert_symbols
from logger import get_logger
from nse import fetch_securities_master, fetch_etf_master

log = get_logger(__name__)


def run_sync():
    master = fetch_securities_master()
    etf_master = pd.DataFrame()
    try:
        log.info("Fetching dedicated ETF master list...")
        etf_master = fetch_etf_master()
    except Exception as e:
        log.warning(f"Could not fetch dedicated ETF list: {e}")

    # Combine lists
    combined_master = pd.concat([master, etf_master], ignore_index=True).drop_duplicates(subset=["symbol"])

    today = datetime.today().strftime("%Y-%m-%d")
    records = []

    # Process Equities and ETFs from NSE Masters
    for row in combined_master.itertuples(index=False):
        if row.series not in {"EQ", "BE"}:
            continue
        records.append({
            "symbol": row.symbol,
            "yahoo_symbol": f"{row.symbol}{YAHOO_SUFFIX}",
            "company_name": row.company_name,
            "isin": row.isin,
            "series": row.series,
            "active": 1,
            "status": "active",
            "last_seen_date": today,
            "source": "nse-securities-master",
            "last_synced_at": today,
        })

    # Process Indices from Config
    for zerodha_symbol, yahoo_symbol in INDEX_MAP.items():
        records.append({
            "symbol": zerodha_symbol,
            "yahoo_symbol": yahoo_symbol,
            "company_name": zerodha_symbol,
            "isin": f"IDX_{zerodha_symbol.replace(' ', '_')}",
            "series": "INDEX",
            "active": 1,
            "status": "active",
            "last_seen_date": today,
            "source": "manual-config",
            "last_synced_at": today,
        })

    with get_connection() as conn:
        setup_schema(conn)
        upsert_symbols(conn, records)
        mark_missing_symbols_inactive(conn, [record["symbol"] for record in records])

    log.info(f"NSE symbol sync complete: {len(records):,} symbols (EQ/BE/INDEX).")


def main():
    run_sync()


if __name__ == "__main__":
    main()
