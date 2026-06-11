"""Sync corporate actions (splits, bonuses) from NSE into the database."""

import sys
import pandas as pd

from adjust_splits import rebuild_symbols
from db import get_connection, setup_schema, upsert_corporate_actions
from logger import get_logger
from nse import fetch_nse_corporate_actions

log = get_logger(__name__)

def _normalize_note(value):
    return None if value is None or pd.isna(value) else str(value)


def run_corporate_sync(start_date="01-01-2024", end_date=None, rebuild=False, rebuild_func=rebuild_symbols):
    log.info(f"Starting NSE corporate actions sync (from {start_date})...")
    df = fetch_nse_corporate_actions(start_date=start_date, end_date=end_date)
    
    if df.empty:
        log.info("No corporate actions found or could not fetch.")
        return {"synced": 0, "rebuilt_symbols": []}
        
    records = df.to_dict(orient="records")
    changed_symbols = set()
    
    with get_connection() as conn:
        setup_schema(conn)
        existing = pd.read_sql(
            """
            SELECT symbol, ex_date, action_type, source, value, note
            FROM corporate_actions
            WHERE source = 'nse'
            """,
            conn,
        )
        existing_map = {
            (row.symbol, row.ex_date, row.action_type, row.source): (
                row.value,
                _normalize_note(row.note),
            )
            for row in existing.itertuples(index=False)
        }
        for record in records:
            key = (
                record["symbol"],
                record["ex_date"],
                record["action_type"],
                record["source"],
            )
            # Rebuild only if action is new OR value has changed (ignore cosmetic note changes) (M-5)
            existing_entry = existing_map.get(key)
            if existing_entry is None:
                changed_symbols.add(record["symbol"])
            else:
                existing_val, existing_note = existing_entry
                if existing_val != record["value"]:
                    changed_symbols.add(record["symbol"])
        upsert_corporate_actions(conn, records)

    log.info(f"Successfully synced {len(records)} corporate actions from NSE.")
    rebuilt_symbols = sorted(changed_symbols)
    if rebuild and rebuilt_symbols:
        log.info(
            f"Rebuilding {len(rebuilt_symbols):,} symbol(s) affected by new/updated corporate actions..."
        )
        rebuild_func(rebuilt_symbols, preserve_market_cap=True)
    return {
        "synced": len(records),
        "rebuilt_symbols": rebuilt_symbols if rebuild else [],
    }

if __name__ == "__main__":
    args = sys.argv[1:]
    start = "01-01-2024"
    end = None
    rebuild = "--rebuild" in args
    for i, arg in enumerate(args):
        if arg == "--start" and i + 1 < len(args):
            start = args[i + 1]
        if arg == "--end" and i + 1 < len(args):
            end = args[i + 1]
            
    run_corporate_sync(start_date=start, end_date=end, rebuild=rebuild)
