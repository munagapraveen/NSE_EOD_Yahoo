"""Detect and apply symbol changes using NSE rename files and ISIN continuity."""

import sys

from db import (
    apply_symbol_rename,
    get_connection,
    load_active_symbol_map,
    setup_schema,
    upsert_symbol_aliases,
)
from logger import get_logger
from nse import fetch_securities_master, fetch_symbol_changes

log = get_logger(__name__)


def build_isin_suggestions(db_df, latest_df):
    latest_by_isin = latest_df[latest_df["isin"] != ""].copy()
    if latest_by_isin.empty:
        return []

    latest_lookup = latest_by_isin.groupby("isin")["symbol"].apply(list).to_dict()
    suggestions = []
    for row in db_df.itertuples(index=False):
        if not row.isin:
            continue
        candidates = latest_lookup.get(row.isin, [])
        candidates = [sym for sym in candidates if sym != row.symbol]
        if len(candidates) == 1:
            suggestions.append({
                "old_symbol": row.symbol,
                "new_symbol": candidates[0],
                "effective_date": None,
                "source": "nse-isin-match",
                "note": f"ISIN continuity match for {row.isin}",
            })
    return suggestions


def main():
    apply_direct = "--apply" in sys.argv[1:]
    apply_isin = "--apply-isin" in sys.argv[1:]

    with get_connection() as conn:
        setup_schema(conn)
        db_df = load_active_symbol_map(conn)

    latest = fetch_securities_master()
    direct = fetch_symbol_changes()
    records = [
        {
            "old_symbol": row.old_symbol,
            "new_symbol": row.new_symbol,
            "effective_date": row.effective_date or None,
            "source": "nse-symbol-changes",
            "note": "Direct NSE symbol change file",
        }
        for row in direct.itertuples(index=False)
    ]

    existing_pairs = {(r["old_symbol"], r["new_symbol"]) for r in records}
    for suggestion in build_isin_suggestions(db_df, latest):
        pair = (suggestion["old_symbol"], suggestion["new_symbol"])
        if pair not in existing_pairs:
            records.append(suggestion)

    if not records:
        log.info("No symbol changes detected.")
        return

    log.info(f"Detected {len(records):,} symbol change candidates.")
    for record in records:
        log.info(
            f"  {record['old_symbol']} -> {record['new_symbol']} "
            f"[{record['source']}]"
        )

    with get_connection() as conn:
        upsert_symbol_aliases(conn, records)
        if apply_direct or apply_isin:
            for record in records:
                should_apply = False
                if record["source"] == "nse-symbol-changes" and apply_direct:
                    should_apply = True
                elif record["source"] == "nse-isin-match" and apply_isin:
                    should_apply = True

                if should_apply:
                    apply_symbol_rename(
                        conn,
                        record["old_symbol"],
                        record["new_symbol"],
                        effective_date=record["effective_date"],
                        source=record["source"],
                        note=record["note"],
                    )
                    log.info(f"Applied symbol change: {record['old_symbol']} -> {record['new_symbol']} ({record['source']})")
            log.info("Completed applying selected symbol changes.")
        else:
            log.info("Dry run only. Re-run with --apply to rename direct changes and/or --apply-isin to rename ISIN matches.")


if __name__ == "__main__":
    main()
