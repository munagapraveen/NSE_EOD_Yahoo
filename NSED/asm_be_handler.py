"""
asm_be_handler.py -- ASM / BE series transition handler
=======================================================
Detects stocks that moved between EQ and BE (T2T/ASM) series
and fetches missing data under the clean base symbol.
RELIANCE-BE is always stored as RELIANCE -- no -BE in the DB.

Usage:
    python asm_be_handler.py

Requirements:
    pip install kiteconnect pandas
"""

from datetime import datetime, timedelta

from analytics_store import rebuild_analytics_for_symbols
from config import YEARS_BACK
from db import (
    get_connection,
    get_last_date,
    get_symbols_with_isin,
    insert_eod_rows,
    log_asm_transition,
    setup_schema,
)
from kite_utils import (
    get_kite,
    get_nse_instruments,
    normalize_symbol,
    run_parallel_ohlcv_tasks,
)
from logger import get_logger

log = get_logger(__name__)
TO_DATE = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)


def detect_transitions(live_df, db_df):
    """
    Use ISIN to find stocks where Kite symbol changed (EQ <-> BE).
    """
    live_by_isin = {}
    for _, row in live_df.iterrows():
        live_by_isin.setdefault(row["isin"], []).append(row)

    db_by_isin = {row["isin"]: row["symbol"] for _, row in db_df.iterrows()}

    transitions = []
    seen = set()

    for isin, db_symbol in db_by_isin.items():
        if isin not in live_by_isin:
            continue

        db_base = normalize_symbol(db_symbol)

        for live_row in live_by_isin[isin]:
            kite_symbol = live_row["tradingsymbol"]
            live_base = normalize_symbol(kite_symbol)
            live_type = live_row["instrument_type"]

            if live_base != db_base or kite_symbol == db_symbol or live_base in seen:
                continue

            db_type = "BE" if db_symbol.endswith("-BE") else "EQ"
            direction = (
                "EQ_to_BE" if db_type == "EQ" and live_type == "BE"
                else "BE_to_EQ" if db_type == "BE" and live_type == "EQ"
                else "unknown"
            )

            transitions.append({
                "base_symbol": live_base,
                "kite_symbol": kite_symbol,
                "isin": isin,
                "direction": direction,
                "token": int(live_row["instrument_token"]),
                "company_name": live_row.get("name", ""),
                "segment": live_row.get("segment", ""),
            })
            seen.add(live_base)

    return transitions


def save_transition_rows(conn, task, df):
    """Persist missing rows for one BE/EQ transition."""
    base = task["symbol"]

    if df.empty:
        log.warning(f"  {base}: no data returned")
        log_asm_transition(
            conn,
            base,
            task["kite_symbol"],
            task["isin"],
            task["direction"],
            0,
        )
        return {"fail": 1}

    df["symbol"] = base
    df["company_name"] = task["company_name"]
    df["segment"] = task["segment"]
    df["instrument_type"] = "EQ"
    df["isin"] = task["isin"]

    insert_eod_rows(conn, df)
    log_asm_transition(
        conn,
        base,
        task["kite_symbol"],
        task["isin"],
        task["direction"],
        len(df),
    )
    log.info(f"  {base}: saved {len(df):,} rows")
    return {"success": 1, "total_rows": len(df)}


def main():
    kite = get_kite()
    from_date = TO_DATE - timedelta(days=YEARS_BACK * 365)

    with get_connection() as conn:
        setup_schema(conn)

        log.info("")
        log.info("=" * 55)
        log.info("ASM / BE SERIES HANDLER")
        log.info("=" * 55)
        log.info("")

        live_df = get_nse_instruments(kite)
        db_df = get_symbols_with_isin(conn)
        transitions = detect_transitions(live_df, db_df)

        log.info(f"Symbols in database : {len(db_df):,}")

        if not transitions:
            log.info("No EQ <-> BE transitions detected.")
            return

        log.info(f"Detected {len(transitions)} transition(s):")
        for transition in transitions:
            log.info(
                f"  {transition['kite_symbol']:25s} -> stored as "
                f"{transition['base_symbol']:20s}  [{transition['direction']}]"
            )
        log.info("")

        tasks = []
        skipped = 0
        for transition in transitions:
            base = transition["base_symbol"]
            last = get_last_date(conn, base)
            fetch_from = (
                datetime.strptime(last, "%Y-%m-%d") + timedelta(days=1)
                if last else from_date
            )

            if fetch_from > TO_DATE:
                log.info(f"  {base}: already up to date")
                log_asm_transition(
                    conn,
                    base,
                    transition["kite_symbol"],
                    transition["isin"],
                    transition["direction"],
                    0,
                )
                skipped += 1
                continue

            tasks.append({
                "symbol": base,
                "kite_symbol": transition["kite_symbol"],
                "token": transition["token"],
                "company_name": transition["company_name"],
                "segment": transition["segment"],
                "isin": transition["isin"],
                "direction": transition["direction"],
                "fetch_from": fetch_from,
                "to_date": TO_DATE,
            })

    if tasks:
        log.info(f"Fetching missing data for {len(tasks)} transition(s) ...")
        results = run_parallel_ohlcv_tasks(
            kite,
            tasks,
            save_transition_rows,
            workers=3,
            progress_label="ASM / BE Handler",
        )
        rebuild_analytics_for_symbols([task["symbol"] for task in tasks])
    else:
        results = {"success": 0, "fail": 0, "total_rows": 0}

    log.info("")
    log.info("=" * 55)
    log.info("COMPLETE -- transitions in 'asm_series_log' table")
    log.info("=" * 55)
    log.info(f"  Updated : {results['success']}")
    log.info(f"  Skipped : {skipped}")
    log.info(f"  Failed  : {results['fail']}")
    log.info("=" * 55)


if __name__ == "__main__":
    main()
