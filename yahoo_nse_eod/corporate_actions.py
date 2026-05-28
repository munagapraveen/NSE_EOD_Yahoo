"""Review stored corporate actions and rebuild affected symbols selectively."""

import sys

import pandas as pd

from adjust_splits import rebuild_symbols
from db import get_connection, setup_schema
from logger import get_logger

log = get_logger(__name__)


def fetch_actions(conn, action_type=None):
    query = """
        SELECT symbol, ex_date, action_type, value, source, note
        FROM corporate_actions
    """
    params = []
    if action_type:
        query += " WHERE action_type = ?"
        params.append(action_type)
    query += " ORDER BY ex_date DESC, symbol"
    return pd.read_sql(query, conn, params=params)


def main():
    args = sys.argv[1:]
    action_type = None
    rebuild_flag = "--rebuild" in args or "--rebuild-splits" in args

    for i, arg in enumerate(args):
        if arg == "--type" and i + 1 < len(args):
            action_type = args[i + 1].strip().lower()

    with get_connection() as conn:
        setup_schema(conn)
        actions = fetch_actions(conn, action_type=action_type)

    if actions.empty:
        log.info("No corporate actions found.")
        return

    log.info(f"Found {len(actions):,} corporate action rows.")
    preview = actions.head(20)
    for row in preview.itertuples(index=False):
        log.info(
            f"  {row.ex_date}  {row.symbol:<15}  {row.action_type:<8}  {row.value}"
        )

    if rebuild_flag:
        symbols = (
            actions["symbol"].dropna().astype(str).str.upper().unique().tolist()
        )
        rebuild_symbols(symbols, preserve_market_cap=True)
        log.info(
            "Rebuilt affected symbols while preserving previously stored historical market cap."
        )


if __name__ == "__main__":
    main()
