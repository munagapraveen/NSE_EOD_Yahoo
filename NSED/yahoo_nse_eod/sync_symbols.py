"""Sync active NSE symbols into the standalone Yahoo/NSE EOD database."""

from datetime import datetime

from config import YAHOO_SUFFIX
from db import get_connection, mark_missing_symbols_inactive, setup_schema, upsert_symbols
from logger import get_logger
from nse import fetch_securities_master

log = get_logger(__name__)


def run_sync():
    master = fetch_securities_master()
    today = datetime.today().strftime("%Y-%m-%d")
    records = []

    for row in master.itertuples(index=False):
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

    with get_connection() as conn:
        setup_schema(conn)
        upsert_symbols(conn, records)
        mark_missing_symbols_inactive(conn, [record["symbol"] for record in records])

    log.info(f"NSE symbol sync complete: {len(records):,} active EQ/BE symbols.")


def main():
    run_sync()


if __name__ == "__main__":
    main()
