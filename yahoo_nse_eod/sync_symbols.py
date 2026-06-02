"""Sync active NSE symbols into the standalone Yahoo/NSE EOD database."""

from datetime import datetime

import pandas as pd
from config import YAHOO_SUFFIX, INDEX_MAP
from db import get_connection, mark_missing_symbols_inactive, setup_schema, upsert_symbols
from logger import get_logger
from nse import fetch_securities_master, fetch_etf_master, fetch_indices_master

log = get_logger(__name__)


def run_sync():
    master = fetch_securities_master()
    etf_master = pd.DataFrame()
    try:
        log.info("Fetching dedicated ETF master list...")
        etf_master = fetch_etf_master()
    except Exception as e:
        log.warning(f"Could not fetch dedicated ETF list: {e}")

    indices_master = pd.DataFrame()
    try:
        indices_master = fetch_indices_master()
    except Exception as e:
        log.warning(f"Could not fetch indices list: {e}")

    today = datetime.today().strftime("%Y-%m-%d")
    records = []

    # 1. Process Stocks (Equities)
    for row in master.to_dict("records"):
        if row["series"] not in {"EQ", "BE"}:
            continue
        records.append({
            "symbol": row["symbol"],
            "yahoo_symbol": f"{row['symbol']}{YAHOO_SUFFIX}",
            "company_name": row["company_name"],
            "isin": row["isin"],
            "series": row["series"],
            "instrument_type": "STOCK",
            "active": 1,
            "status": "active",
            "last_seen_date": today,
            "source": "nse-securities-master",
            "last_synced_at": today,
        })

    # 2. Process ETFs
    for row in etf_master.to_dict("records"):
        records.append({
            "symbol": row["symbol"],
            "yahoo_symbol": f"{row['symbol']}{YAHOO_SUFFIX}",
            "company_name": row["company_name"],
            "isin": row["isin"],
            "series": row["series"],
            "instrument_type": "ETF",
            "active": 1,
            "status": "active",
            "last_seen_date": today,
            "source": "nse-etf-master",
            "last_synced_at": today,
        })

    # 3. Process Indices dynamically based on NSE API + Yahoo mapping
    for row in indices_master.to_dict("records"):
        symbol = row["symbol"]
        if symbol in INDEX_MAP:
            yahoo_symbol = INDEX_MAP[symbol]
            records.append({
                "symbol": symbol,
                "yahoo_symbol": yahoo_symbol,
                "company_name": symbol,
                "isin": f"IDX_{symbol.replace(' ', '_')}",
                "series": "INDEX",
                "instrument_type": "INDEX",
                "active": 1,
                "status": "active",
                "last_seen_date": today,
                "source": "nse-api",
                "last_synced_at": today,
            })

    # Deduplicate by symbol (preferring the first appearance, which is STOCK if it exists in both)
    seen_symbols = set()
    unique_records = []
    for r in records:
        if r["symbol"] not in seen_symbols:
            unique_records.append(r)
            seen_symbols.add(r["symbol"])

    with get_connection() as conn:
        setup_schema(conn)
        upsert_symbols(conn, unique_records)
        mark_missing_symbols_inactive(conn, [record["symbol"] for record in unique_records])

    log.info(f"NSE symbol sync complete: {len(unique_records):,} symbols (STOCK/ETF/INDEX).")


def main():
    run_sync()


if __name__ == "__main__":
    main()
