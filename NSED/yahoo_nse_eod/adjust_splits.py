"""Materialize split-adjusted OHLCV prices from raw Yahoo history."""

import sys
from decimal import Decimal, ROUND_DOWN

import pandas as pd

from db import (
    get_active_symbols,
    load_adjusted_market_caps,
    get_connection,
    load_raw_prices,
    load_share_history,
    replace_adjusted_prices,
    setup_schema,
    upsert_adjusted_prices,
)
from logger import get_logger

log = get_logger(__name__)


def truncate_to_2dp(value):
    if pd.isna(value):
        return value
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_DOWN))


def build_split_adjusted(df):
    if df.empty:
        return df

    work = df.copy()
    work["stock_splits"] = pd.to_numeric(work["stock_splits"], errors="coerce").fillna(0.0)
    split_multiplier = work["stock_splits"].replace(0.0, 1.0)
    future_factor = split_multiplier.iloc[::-1].cumprod().iloc[::-1].shift(-1, fill_value=1.0)
    work["split_factor"] = future_factor.astype(float)

    for price_col in ["open", "high", "low", "close"]:
        work[price_col] = (
            pd.to_numeric(work[price_col], errors="coerce") / work["split_factor"]
        ).apply(truncate_to_2dp)
    work["volume"] = pd.to_numeric(work["volume"], errors="coerce") * work["split_factor"]

    close_series = pd.to_numeric(work["close"], errors="coerce")
    for window in [5, 10, 20, 50, 100, 200]:
        work[f"ma_{window}"] = close_series.rolling(window=window, min_periods=window).mean().round(4)

    work["shares_outstanding"] = pd.NA
    work["market_cap_cr"] = pd.NA

    return work[
        [
            "symbol", "date", "open", "high", "low", "close", "volume", "split_factor",
            "shares_outstanding", "market_cap_cr",
            "ma_5", "ma_10", "ma_20", "ma_50", "ma_100", "ma_200",
        ]
    ]


def attach_market_cap(adjusted_df, share_df):
    """
    Align historical shares to price dates, then convert them onto the same
    split-adjusted basis as the adjusted close series.

    This is used for first-time insertion and for newly appended dates that do
    not yet have an authoritative stored historical market cap.
    """
    if adjusted_df.empty:
        return adjusted_df

    work = adjusted_df.copy()
    if share_df.empty:
        return work

    shares = share_df.copy()
    shares["date"] = pd.to_datetime(shares["date"])
    shares["shares_outstanding"] = pd.to_numeric(
        shares["shares_outstanding"], errors="coerce"
    )
    shares = shares.dropna(subset=["shares_outstanding"]).sort_values("date")
    if shares.empty:
        return work

    work["date"] = pd.to_datetime(work["date"])
    work = work.sort_values("date")

    merged = pd.merge_asof(
        work,
        shares[["date", "shares_outstanding"]],
        on="date",
        direction="backward",
    )
    merged["shares_outstanding"] = merged["shares_outstanding"].fillna(
        shares["shares_outstanding"].iloc[0]
    )
    merged["shares_outstanding"] = (
        pd.to_numeric(merged["shares_outstanding"], errors="coerce") *
        pd.to_numeric(merged["split_factor"], errors="coerce")
    )
    merged["market_cap_cr"] = (
        pd.to_numeric(merged["close"], errors="coerce") *
        pd.to_numeric(merged["shares_outstanding"], errors="coerce") / 1e7
    ).round(4)
    merged["date"] = merged["date"].dt.strftime("%Y-%m-%d")
    return merged


def preserve_existing_market_cap(adjusted_df, existing_df):
    """
    Keep previously stored historical market cap values by date, and derive the
    corresponding adjusted shares outstanding from refreshed adjusted close.

    This is used during corporate-action rebuilds so historical market cap
    remains stable even when adjusted close is rewritten for old dates.
    """
    if adjusted_df.empty or existing_df.empty:
        return adjusted_df

    work = adjusted_df.copy()
    existing = existing_df.copy()
    existing["date"] = existing["date"].astype(str)
    existing["market_cap_cr"] = pd.to_numeric(existing["market_cap_cr"], errors="coerce")
    existing = existing.dropna(subset=["market_cap_cr"])
    if existing.empty:
        return work

    work = work.merge(
        existing[["date", "market_cap_cr"]],
        on="date",
        how="left",
        suffixes=("", "_existing"),
    )
    has_existing = work["market_cap_cr_existing"].notna()
    work.loc[has_existing, "market_cap_cr"] = work.loc[has_existing, "market_cap_cr_existing"]

    close_series = pd.to_numeric(work["close"], errors="coerce")
    market_cap_series = pd.to_numeric(work["market_cap_cr"], errors="coerce")
    valid = has_existing & close_series.notna() & (close_series != 0)
    work.loc[valid, "shares_outstanding"] = (
        market_cap_series[valid] * 1e7 / close_series[valid]
    ).round(4)

    work.drop(columns=["market_cap_cr_existing"], inplace=True)
    return work


def rebuild_symbols(symbols, preserve_market_cap=False):
    if not symbols:
        log.warning("No symbols to adjust.")
        return

    for idx, symbol in enumerate(symbols, start=1):
        with get_connection() as conn:
            raw = load_raw_prices(conn, symbol)
            adjusted = build_split_adjusted(raw)
            if preserve_market_cap:
                shares = load_share_history(conn, symbol)
                adjusted = attach_market_cap(adjusted, shares)
                existing_market_caps = load_adjusted_market_caps(conn, symbol)
                adjusted = preserve_existing_market_cap(adjusted, existing_market_caps)
            else:
                shares = load_share_history(conn, symbol)
                adjusted = attach_market_cap(adjusted, shares)
            replace_adjusted_prices(conn, symbol, adjusted)
        log.info(
            f"[{idx}/{len(symbols)}] rebuilt split-adjusted history, market cap, and moving averages for {symbol}"
        )


def refresh_latest_rows(symbol_date_map):
    """
    Recompute adjusted rows and moving averages, but only write the requested dates.
    Used for routine incremental updates where only newly downloaded dates changed.
    """
    if not symbol_date_map:
        return

    items = list(symbol_date_map.items())
    for idx, (symbol, changed_dates) in enumerate(items, start=1):
        with get_connection() as conn:
            raw = load_raw_prices(conn, symbol)
            adjusted = build_split_adjusted(raw)
            shares = load_share_history(conn, symbol)
            adjusted = attach_market_cap(adjusted, shares)
            target_dates = {str(date_val) for date_val in changed_dates}
            subset = adjusted[adjusted["date"].isin(target_dates)].copy()
            upsert_adjusted_prices(conn, subset)
        log.info(
            f"[{idx}/{len(items)}] updated market cap and moving averages for {len(target_dates):,} date(s) in {symbol}"
        )


def main():
    requested = [arg.strip().upper() for arg in sys.argv[1:] if not arg.startswith("-")]

    with get_connection() as conn:
        setup_schema(conn)
        if requested:
            symbols = requested
        else:
            symbols = get_active_symbols(conn)["symbol"].tolist()

    rebuild_symbols(symbols)


if __name__ == "__main__":
    main()
