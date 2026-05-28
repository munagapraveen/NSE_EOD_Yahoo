"""Sync corporate actions (splits, bonuses) from NSE into the database."""

import sys
from db import get_connection, setup_schema, upsert_corporate_actions
from logger import get_logger
from nse import fetch_nse_corporate_actions

log = get_logger(__name__)

def run_corporate_sync(start_date="01-01-2024", end_date=None):
    log.info(f"Starting NSE corporate actions sync (from {start_date})...")
    df = fetch_nse_corporate_actions(start_date=start_date, end_date=end_date)
    
    if df.empty:
        log.info("No corporate actions found or could not fetch.")
        return
        
    records = df.to_dict(orient="records")
    
    with get_connection() as conn:
        setup_schema(conn)
        upsert_corporate_actions(conn, records)
        
    log.info(f"Successfully synced {len(records)} corporate actions from NSE.")

if __name__ == "__main__":
    args = sys.argv[1:]
    start = "01-01-2024"
    end = None
    for i, arg in enumerate(args):
        if arg == "--start" and i + 1 < len(args):
            start = args[i + 1]
        if arg == "--end" and i + 1 < len(args):
            end = args[i + 1]
            
    run_corporate_sync(start_date=start, end_date=end)
