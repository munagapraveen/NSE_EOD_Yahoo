"""
refresh_adjusted.py — Manual price adjustment refresher
=========================================================
Deletes and re-downloads adjusted historical data for specific symbols.

Usage:
    python refresh_adjusted.py RELIANCE
    python refresh_adjusted.py INFY TCS HDFCBANK
    python refresh_adjusted.py --file splits_today.txt

Requirements:
    pip install kiteconnect pandas
"""

import sys
from datetime import datetime, timedelta

from config import YEARS_BACK
from db import get_connection, setup_schema
from kite_utils import get_kite, refresh_symbol_data
from logger import get_logger

log = get_logger(__name__)

TO_DATE   = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
FROM_DATE = TO_DATE - timedelta(days=YEARS_BACK * 365)


def main():
    args = sys.argv[1:]

    if not args:
        print("")
        print("Usage:")
        print("  python refresh_adjusted.py SYMBOL1 SYMBOL2 ...")
        print("  python refresh_adjusted.py --file splits_today.txt")
        print("")
        return

    if args[0] == "--file":
        if len(args) < 2:
            print("Provide filename: --file splits_today.txt")
            return
        with open(args[1]) as f:
            symbols = [line.strip().upper() for line in f if line.strip()]
    else:
        symbols = [s.upper() for s in args]

    if not symbols:
        print("No symbols provided.")
        return

    log.info("")
    log.info("=" * 55)
    log.info("PRICE ADJUSTMENT REFRESH")
    log.info("=" * 55)
    log.info(f"Symbols    : {', '.join(symbols)}")
    log.info(f"Date range : {FROM_DATE.date()} to {TO_DATE.date()}")
    log.info("")

    print(f"Will DELETE and RE-DOWNLOAD: {', '.join(symbols)}")
    if input("Type YES to confirm: ").strip().upper() != "YES":
        print("Aborted.")
        return

    kite    = get_kite()
    success = fail = 0

    with get_connection() as conn:
        setup_schema(conn)
        for symbol in symbols:
            log.info(f"Processing {symbol} ...")
            try:
                rows = refresh_symbol_data(
                    kite, conn, symbol, FROM_DATE, TO_DATE,
                    reason="manual refresh",
                )
                if rows > 0:
                    success += 1
                else:
                    fail += 1
            except Exception as exc:
                log.error(f"  {symbol}: failed — {exc}")
                fail += 1

    log.info("")
    log.info("=" * 55)
    log.info(f"  Done — {success} refreshed, {fail} failed")
    log.info("  Adjustment history in 'adjustment_log' table")
    log.info("=" * 55)


if __name__ == "__main__":
    main()
