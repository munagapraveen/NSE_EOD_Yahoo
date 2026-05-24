"""
analytics_store.py -- Derived analytics persistence
===================================================
Rebuilds and stores moving averages and daily market cap derived from eod_data.
"""

import pandas as pd

from db import (
    get_connection,
    upsert_indicator_rows,
    upsert_marketcap_rows,
)
from logger import get_logger

log = get_logger(__name__)

MA_WINDOWS = [5, 10, 20, 50, 100, 200]


def _normalize_symbols(symbols):
    if symbols is None:
        return None
    cleaned = []
    seen = set()
    for symbol in symbols or []:
        value = str(symbol).strip().upper()
        if value and value not in seen:
            cleaned.append(value)
            seen.add(value)
    return cleaned


def _load_prices(conn, symbols):
    if symbols is None:
        return pd.read_sql("""
            SELECT symbol, date, close
            FROM eod_data
            WHERE close IS NOT NULL
              AND close > 0
            ORDER BY symbol, date
        """, conn)
    placeholders = ",".join("?" for _ in symbols)
    return pd.read_sql(f"""
        SELECT symbol, date, close
        FROM eod_data
        WHERE symbol IN ({placeholders})
          AND close IS NOT NULL
          AND close > 0
        ORDER BY symbol, date
    """, conn, params=symbols)


def _load_fundamentals(conn, symbols):
    if symbols is None:
        return pd.read_sql("""
            SELECT symbol, shares_outstanding
            FROM fundamentals
            WHERE shares_outstanding IS NOT NULL
        """, conn)
    placeholders = ",".join("?" for _ in symbols)
    return pd.read_sql(f"""
        SELECT symbol, shares_outstanding
        FROM fundamentals
        WHERE symbol IN ({placeholders})
          AND shares_outstanding IS NOT NULL
    """, conn, params=symbols)


def rebuild_indicators_for_symbols(symbols):
    symbols = _normalize_symbols(symbols)
    if symbols == []:
        return 0

    with get_connection() as conn:
        prices = _load_prices(conn, symbols)
        if prices.empty:
            return 0

        records = []
        for symbol, grp in prices.groupby("symbol"):
            work = grp.copy()
            close_series = pd.to_numeric(work["close"], errors="coerce")
            row_data = {
                "symbol": work["symbol"].astype(str),
                "date": work["date"].astype(str),
            }
            for window in MA_WINDOWS:
                row_data[f"ma_{window}"] = (
                    close_series.rolling(window=window, min_periods=window)
                    .mean()
                    .round(2)
                )
            frame = pd.DataFrame(row_data).where(pd.notnull(pd.DataFrame(row_data)), None)
            records.extend(frame.to_dict("records"))

        upsert_indicator_rows(conn, records)
        return len(records)


def rebuild_marketcap_for_symbols(symbols):
    symbols = _normalize_symbols(symbols)
    if symbols == []:
        return 0

    with get_connection() as conn:
        prices = _load_prices(conn, symbols)
        fundamentals = _load_fundamentals(conn, symbols)
        if prices.empty or fundamentals.empty:
            return 0

        merged = prices.merge(fundamentals, on="symbol", how="inner")
        if merged.empty:
            return 0

        merged["market_cap_cr"] = (
            pd.to_numeric(merged["close"], errors="coerce") *
            pd.to_numeric(merged["shares_outstanding"], errors="coerce") / 1e7
        ).round(2)

        records = merged[
            ["symbol", "date", "market_cap_cr", "shares_outstanding"]
        ].where(pd.notnull(merged[["symbol", "date", "market_cap_cr", "shares_outstanding"]]), None).to_dict("records")

        upsert_marketcap_rows(conn, records)
        return len(records)


def rebuild_analytics_for_symbols(symbols):
    symbols = _normalize_symbols(symbols)
    if symbols == []:
        return {"indicator_rows": 0, "marketcap_rows": 0}

    indicator_rows = rebuild_indicators_for_symbols(symbols)
    marketcap_rows = rebuild_marketcap_for_symbols(symbols)
    log.info(
        f"Rebuilt analytics for {('all' if symbols is None else f'{len(symbols):,}')} symbol(s): "
        f"{indicator_rows:,} indicator rows, {marketcap_rows:,} marketcap rows"
    )
    return {
        "indicator_rows": indicator_rows,
        "marketcap_rows": marketcap_rows,
    }
