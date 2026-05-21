"""
symbol_change_handler.py -- Symbol rename / delist detector
===========================================================
Detects probable renames and delistings by comparing stored DB symbols
against live NSE instruments. Uses ISIN first for high-confidence rename
suggestions, then falls back to missing-symbol review.

Usage:
    python symbol_change_handler.py

Requirements:
    pip install kiteconnect pandas
"""

import pandas as pd

from db import (
    get_all_symbols,
    get_connection,
    get_symbols_with_isin,
    log_symbol_change,
    rename_symbol,
    setup_schema,
)
from kite_utils import get_kite, get_nse_instruments
from logger import get_logger

log = get_logger(__name__)


def get_live_nse_symbols_and_isin(kite):
    """Return live symbol set plus live instrument DataFrame keyed by ISIN."""
    live_df = get_nse_instruments(kite).copy()
    live_symbols = set(live_df["base_symbol"].tolist())
    return live_symbols, live_df


def build_isin_suggestions(db_df, live_df):
    """
    Build probable old_symbol -> new_symbol rename suggestions using ISIN.
    Only suggests when the DB symbol is missing live and ISIN maps cleanly.
    """
    db_map = {
        str(row["symbol"]).strip().upper(): str(row["isin"]).strip().upper()
        for _, row in db_df.iterrows()
        if str(row["isin"]).strip()
    }

    live_by_isin = {}
    for _, row in live_df.iterrows():
        isin = str(row.get("isin", "")).strip().upper()
        if not isin:
            continue
        live_by_isin.setdefault(isin, []).append(row)

    suggestions = []
    for old_symbol, isin in db_map.items():
        candidates = live_by_isin.get(isin, [])
        if not candidates:
            continue

        candidate_symbols = {
            str(row["base_symbol"]).strip().upper()
            for row in candidates
        }

        if len(candidate_symbols) != 1:
            continue

        new_symbol = next(iter(candidate_symbols))
        if new_symbol != old_symbol:
            chosen = candidates[0]
            suggestions.append({
                "old_symbol": old_symbol,
                "new_symbol": new_symbol,
                "isin": isin,
                "company_name": chosen.get("name", ""),
                "segment": chosen.get("segment", ""),
                "reason": "isin_match",
            })

    suggestions.sort(key=lambda item: item["old_symbol"])
    return suggestions


def show_symbol_stats(conn, symbol):
    """Return (min_date, max_date, count) for a stored symbol."""
    return conn.execute(
        "SELECT MIN(date), MAX(date), COUNT(*) FROM eod_data WHERE symbol=?",
        (symbol,),
    ).fetchone()


def review_isin_suggestions(conn, live_symbols, suggestions):
    """Review ISIN-based rename suggestions interactively."""
    if not suggestions:
        return set()

    print("")
    print("High-confidence rename suggestions (matched by ISIN):")
    print("")

    handled = set()
    for item in suggestions:
        old_symbol = item["old_symbol"]
        new_symbol = item["new_symbol"]
        min_d, max_d, cnt = show_symbol_stats(conn, old_symbol)
        print(
            f"  {old_symbol:20s} -> {new_symbol:20s}  "
            f"{cnt:>6} rows  ({min_d} to {max_d})"
        )

    print("")
    print("Options:")
    print("  Press Enter / Y  -- accept suggested rename")
    print("  Type N           -- skip")
    print("  Type DELISTED    -- mark as delisted")
    print("  Type another symbol name to override")
    print("")

    for item in suggestions:
        old_symbol = item["old_symbol"]
        new_symbol = item["new_symbol"]
        answer = input(
            f"  Rename '{old_symbol}' to suggested '{new_symbol}'? "
        ).strip().upper()

        if answer in ("", "Y", "YES"):
            rows = rename_symbol(conn, old_symbol, new_symbol)
            log.info(
                f"  Moved {rows:,} rows: {old_symbol} -> {new_symbol} "
                f"[isin match]"
            )
            handled.add(old_symbol)
        elif answer in ("N", "NO"):
            log.info(f"  Skipped {old_symbol}")
            handled.add(old_symbol)
        elif answer == "DELISTED":
            log_symbol_change(conn, old_symbol, "", "delisted")
            log.info(f"  Marked {old_symbol} as delisted")
            handled.add(old_symbol)
        elif answer in live_symbols:
            rows = rename_symbol(conn, old_symbol, answer)
            log.info(f"  Moved {rows:,} rows: {old_symbol} -> {answer}")
            handled.add(old_symbol)
        else:
            print(f"  '{answer}' not in live NSE -- skipped")
            handled.add(old_symbol)

    return handled


def review_missing_symbols(conn, live_symbols, missing):
    """Review remaining missing symbols interactively."""
    if not missing:
        return

    print("")
    print("Remaining symbols in your DB not found in live NSE feed:")
    print("(May be renamed or delisted)")
    print("")

    for sym in missing:
        min_d, max_d, cnt = show_symbol_stats(conn, sym)
        print(f"  {sym:20s}  {cnt:>6} rows  ({min_d} to {max_d})")

    print("")
    print("Options:")
    print("  Enter new symbol  -- merges history to new name")
    print("  Press Enter       -- skip (keep as-is)")
    print("  Type DELISTED     -- marks as delisted")
    print("")

    for sym in missing:
        answer = input(f"  New symbol for '{sym}': ").strip().upper()
        if answer == "":
            log.info(f"  Skipped {sym}")
        elif answer == "DELISTED":
            log_symbol_change(conn, sym, "", "delisted")
            log.info(f"  Marked {sym} as delisted")
        elif answer in live_symbols:
            rows = rename_symbol(conn, sym, answer)
            log.info(f"  Moved {rows:,} rows: {sym} -> {answer}")
        else:
            print(f"  '{answer}' not in live NSE -- skipped")


def print_audit_log(conn):
    """Print symbol history audit log."""
    log.info("")
    log.info("=" * 55)
    log.info("SYMBOL HISTORY LOG")
    log.info("=" * 55)

    rows = conn.execute(
        "SELECT old_symbol, new_symbol, changed_on, note "
        "FROM symbol_history ORDER BY changed_on DESC"
    ).fetchall()

    if rows:
        for old, new, date, note in rows:
            log.info(
                f"  {date}  {old:20s} -> "
                f"{new or '(delisted)':20s}  [{note}]"
            )
    else:
        log.info("  No changes recorded yet.")

    log.info("=" * 55)


def main():
    kite = get_kite()

    with get_connection() as conn:
        setup_schema(conn)

        log.info("")
        log.info("=" * 55)
        log.info("SYMBOL CHANGE HANDLER")
        log.info("=" * 55)
        log.info("")

        stored = get_all_symbols(conn)
        db_isin_df = get_symbols_with_isin(conn)
        live_symbols, live_df = get_live_nse_symbols_and_isin(kite)
        missing = sorted(stored - live_symbols)
        suggestions = build_isin_suggestions(db_isin_df, live_df)

        log.info(f"Symbols in DB           : {len(stored):,}")
        log.info(f"Live NSE symbols        : {len(live_symbols):,}")
        log.info(f"Possibly changed        : {len(missing):,}")
        log.info(f"ISIN rename suggestions : {len(suggestions):,}")
        log.info("")

        if not missing and not suggestions:
            log.info("No symbol changes detected.")
            return

        handled = review_isin_suggestions(conn, live_symbols, suggestions)
        remaining_missing = [sym for sym in missing if sym not in handled]
        review_missing_symbols(conn, live_symbols, remaining_missing)
        print_audit_log(conn)


if __name__ == "__main__":
    main()
