"""
marketcap.py -- Fundamentals fetcher & daily market-cap calculator
==================================================================
Fetches slow-moving fundamentals from Yahoo Finance and combines them
with locally stored close prices to calculate market cap.

Design:
  - Fundamentals are cached in SQLite.
  - Daily market cap is computed from close * shares_outstanding.
  - Refreshes can be full, missing-only, or stale-only.

Usage:
    python marketcap.py --fetch
    python marketcap.py --bootstrap
    python marketcap.py --only-missing
    python marketcap.py --stale-days 30
    python marketcap.py --workers 6 --limit 500

Requirements:
    pip install yfinance pandas
"""

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from random import uniform
import logging

import pandas as pd
import yfinance as yf

from analytics_store import rebuild_marketcap_for_symbols
from db import (
    get_connection,
    setup_schema,
    upsert_fundamentals,
)
from logger import get_logger

log = get_logger(__name__)

# Silence yfinance's noisy HTTP 404 log spam in GUI/task output.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

DEFAULT_STALE_DAYS = 30
DEFAULT_WORKERS = 15
BATCH_SIZE = 50
REQUEST_COOLDOWN = 0.35
MAX_RETRIES = 4
RETRY_BASE_DELAY = 2.0


def emit_progress(completed, total, label="Fetch Market Cap Data"):
    """Emit a machine-readable progress line for the GUI."""
    pct = 100.0 if total <= 0 else min(100.0, (completed / total) * 100.0)
    print(f"PROGRESS|{pct:.1f}|{label}|{completed}|{total}", flush=True)


def parse_args(args):
    """Parse command-line flags into a simple options dict."""
    opts = {
        "fetch": False,
        "bootstrap": False,
        "only_missing": False,
        "stale_days": None,
        "workers": DEFAULT_WORKERS,
        "limit": None,
        "recent_days": None,
    }

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--fetch":
            opts["fetch"] = True
        elif arg == "--bootstrap":
            opts["fetch"] = True
            opts["bootstrap"] = True
        elif arg == "--only-missing":
            opts["fetch"] = True
            opts["only_missing"] = True
        elif arg == "--stale-days" and i + 1 < len(args):
            opts["fetch"] = True
            try:
                opts["stale_days"] = max(0, int(args[i + 1]))
            except ValueError:
                pass
            i += 1
        elif arg == "--workers" and i + 1 < len(args):
            try:
                opts["workers"] = max(1, min(16, int(args[i + 1])))
            except ValueError:
                pass
            i += 1
        elif arg == "--limit" and i + 1 < len(args):
            try:
                opts["limit"] = max(1, int(args[i + 1]))
            except ValueError:
                pass
            i += 1
        elif arg == "--recent-days" and i + 1 < len(args):
            opts["fetch"] = True
            try:
                opts["recent_days"] = max(1, int(args[i + 1]))
            except ValueError:
                pass
            i += 1
        i += 1

    if opts["fetch"] and opts["stale_days"] is None and not opts["bootstrap"] and not opts["only_missing"]:
        opts["stale_days"] = DEFAULT_STALE_DAYS

    return opts


def fetch_fundamentals(symbol):
    """Fetch key fundamentals for one NSE symbol from yfinance with retry/backoff."""
    last_exc = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            ticker = yf.Ticker(f"{symbol}.NS")
            info = ticker.info

            time.sleep(REQUEST_COOLDOWN + uniform(0.0, 0.2))

            if not info or (
                info.get("regularMarketPrice") is None
                and info.get("sharesOutstanding") is None
            ):
                return None

            return {
                "symbol": symbol,
                "company_name": info.get("longName", ""),
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
                "shares_outstanding": info.get("sharesOutstanding"),
                "face_value": info.get("faceValue"),
                "book_value": info.get("bookValue"),
                "pe_ratio": info.get("trailingPE"),
                "dividend_yield": info.get("dividendYield"),
                "week_52_high": info.get("fiftyTwoWeekHigh"),
                "week_52_low": info.get("fiftyTwoWeekLow"),
                "last_updated": datetime.today().strftime("%Y-%m-%d"),
            }
        except Exception as exc:
            last_exc = exc
            message = str(exc)
            if "Too Many Requests" in message and attempt < MAX_RETRIES:
                wait = RETRY_BASE_DELAY * attempt
                log.warning(
                    f"  {symbol}: rate limited (attempt {attempt}/{MAX_RETRIES}) "
                    f"-- retrying in {wait:.1f}s"
                )
                time.sleep(wait)
                continue
            if attempt < MAX_RETRIES:
                time.sleep(0.5 * attempt)

    raise last_exc


def get_symbol_refresh_plan(conn):
    """Return all EQ symbols with their fundamentals last_updated value."""
    rows = conn.execute("""
        SELECT
            e.symbol,
            MAX(f.last_updated) AS last_updated
        FROM eod_data e
        LEFT JOIN fundamentals f ON e.symbol = f.symbol
        WHERE e.instrument_type = 'EQ'
          AND e.segment = 'NSE'
          AND COALESCE(TRIM(e.company_name), '') != ''
          AND e.symbol NOT LIKE '%-%'
        GROUP BY e.symbol
        ORDER BY e.symbol
    """).fetchall()
    return [{"symbol": row[0], "last_updated": row[1]} for row in rows]


def get_recent_active_symbols(conn, recent_days):
    """Return recently traded symbols for active refresh mode."""
    rows = conn.execute("""
        SELECT DISTINCT symbol
        FROM eod_data
        WHERE instrument_type = 'EQ'
          AND segment = 'NSE'
          AND COALESCE(TRIM(company_name), '') != ''
          AND symbol NOT LIKE '%-%'
          AND date >= date('now', ?)
        ORDER BY symbol
    """, (f"-{recent_days} days",)).fetchall()
    return {row[0] for row in rows}


def select_symbols_to_fetch(conn, opts):
    """Select symbols based on bootstrap / missing / stale rules."""
    plan = get_symbol_refresh_plan(conn)
    today = datetime.today().date()
    selected = []
    recent_active = None

    if opts["recent_days"] is not None:
        recent_active = get_recent_active_symbols(conn, opts["recent_days"])

    for item in plan:
        if recent_active is not None and item["symbol"] not in recent_active:
            continue

        last_updated = item["last_updated"]
        should_fetch = False

        if opts["bootstrap"]:
            should_fetch = True
        elif opts["only_missing"]:
            should_fetch = not last_updated
        elif last_updated is None:
            should_fetch = True
        elif opts["stale_days"] is not None:
            try:
                age_days = (today - datetime.strptime(last_updated, "%Y-%m-%d").date()).days
            except ValueError:
                age_days = opts["stale_days"] + 1
            should_fetch = age_days >= opts["stale_days"]

        if should_fetch:
            selected.append(item["symbol"])

    if opts["limit"]:
        selected = selected[:opts["limit"]]

    return selected, len(plan)


def fetch_selected(conn, opts):
    """Fetch fundamentals for the selected symbol universe."""
    symbols, total_symbols = select_symbols_to_fetch(conn, opts)
    skipped = total_symbols - len(symbols)

    mode = (
        "bootstrap"
        if opts["bootstrap"]
        else "missing-only"
        if opts["only_missing"]
        else f"stale >= {opts['stale_days']} days"
    )
    if opts["recent_days"] is not None:
        mode += f", recent active <= {opts['recent_days']} days"

    log.info("")
    log.info("=" * 55)
    log.info("FETCHING FUNDAMENTALS FROM YFINANCE")
    log.info("=" * 55)
    log.info(f"Universe      : {total_symbols:,} EQ symbols")
    log.info(f"Mode          : {mode}")
    log.info(f"To fetch      : {len(symbols):,}")
    log.info(f"Skipped       : {skipped:,}")
    log.info(f"Workers       : {opts['workers']}")
    if opts["limit"]:
        log.info(f"Limit         : {opts['limit']:,}")
    log.info("")

    if not symbols:
        emit_progress(1, 1)
        log.info("Nothing to fetch.")
        return

    batch = []
    success = 0
    fail = 0
    completed = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=opts["workers"]) as executor:
        future_map = {
            executor.submit(fetch_fundamentals, symbol): symbol
            for symbol in symbols
        }

        for future in as_completed(future_map):
            symbol = future_map[future]
            try:
                result = future.result()
            except Exception as exc:
                log.warning(f"  {symbol}: fetch failed ({exc})")
                result = None

            if result:
                batch.append(result)
                success += 1
            else:
                fail += 1

            if len(batch) >= BATCH_SIZE:
                upsert_fundamentals(conn, batch)
                batch = []

            completed += 1
            emit_progress(completed, len(symbols))

    if batch:
        upsert_fundamentals(conn, batch)

    elapsed = time.time() - t0
    mins, secs = divmod(int(elapsed), 60)

    log.info("")
    log.info("=" * 55)
    log.info("COMPLETE")
    log.info("=" * 55)
    log.info(f"  Fetched : {success:,} symbols")
    log.info(f"  Failed  : {fail:,} symbols")
    log.info(f"  Skipped : {skipped:,} symbols")
    log.info(f"  Time    : {mins}m {secs}s")
    log.info("=" * 55)

    rows = rebuild_marketcap_for_symbols(symbols if not opts["bootstrap"] else None)
    if rows:
        log.info(f"Persisted marketcap rows: {rows:,}")


def get_daily_marketcap(conn, symbol=None, date=None):
    """
    Return daily market cap in Rs. crore.
    Optionally filter by symbol and/or date.
    """
    where = []
    params = []
    if symbol:
        where.append("e.symbol = ?")
        params.append(symbol)
    if date:
        where.append("e.date = ?")
        params.append(date)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    return pd.read_sql(f"""
        SELECT
            e.symbol, f.sector, m.date, e.close,
            m.shares_outstanding, m.market_cap_cr
        FROM marketcap m
        JOIN eod_data e ON e.symbol = m.symbol AND e.date = m.date
        JOIN fundamentals f ON e.symbol = f.symbol
        {where_sql}
        ORDER BY e.symbol, m.date
    """, conn, params=params)


def get_marketcap_ranked(conn, date):
    """Return all stocks ranked by market cap on a given date."""
    return pd.read_sql("""
        SELECT
            e.symbol, f.company_name, f.sector, e.close,
            m.shares_outstanding,
            m.market_cap_cr,
            RANK() OVER (ORDER BY m.market_cap_cr DESC) AS rank
        FROM marketcap m
        JOIN eod_data e ON e.symbol = m.symbol AND e.date = m.date
        JOIN fundamentals f ON e.symbol = f.symbol
        WHERE m.date = ?
        ORDER BY market_cap_cr DESC
    """, conn, params=[date])


def classify_cap(market_cap_cr):
    """SEBI market cap classification."""
    if market_cap_cr >= 20000:
        return "Large Cap"
    if market_cap_cr >= 5000:
        return "Mid Cap"
    return "Small Cap"


def print_examples(conn):
    """Show quick interactive examples when no fetch flags are passed."""
    print("")
    print("Usage:")
    print("  python marketcap.py --fetch")
    print("  python marketcap.py --bootstrap")
    print("  python marketcap.py --only-missing")
    print("  python marketcap.py --stale-days 30")
    print("  python marketcap.py --recent-days 7")
    print("  python marketcap.py --workers 6 --limit 500")
    print("")

    print("Example 1 -- RELIANCE daily market cap (last 5 rows):")
    try:
        df = get_daily_marketcap(conn, symbol="RELIANCE")
        print(
            df.tail().to_string(index=False)
            if not df.empty else "  No data -- run a fetch first"
        )
    except Exception as exc:
        print(f"  Error: {exc}")

    print("")
    print("Example 2 -- Top 10 stocks by market cap today:")
    try:
        today = datetime.today().strftime("%Y-%m-%d")
        df = get_marketcap_ranked(conn, today)
        if not df.empty:
            df["cap_type"] = df["market_cap_cr"].apply(classify_cap)
            print(df.head(10)[
                ["rank", "symbol", "company_name", "sector", "market_cap_cr", "cap_type"]
            ].to_string(index=False))
        else:
            print("  No data for today -- try a recent trading date")
    except Exception as exc:
        print(f"  Error: {exc}")


def main():
    opts = parse_args(sys.argv[1:])

    with get_connection() as conn:
        setup_schema(conn)

        if opts["fetch"] or opts["bootstrap"] or opts["only_missing"]:
            fetch_selected(conn, opts)
            return

        print_examples(conn)


if __name__ == "__main__":
    main()
