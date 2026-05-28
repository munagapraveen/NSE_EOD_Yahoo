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
    load_corporate_actions,
    replace_adjusted_prices,
    save_indicators,
    save_market_caps,
    setup_schema,
    upsert_adjusted_prices,
)
from logger import get_logger

log = get_logger(__name__)


def truncate_to_2dp(value):
    if pd.isna(value):
        return value
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_DOWN))


def build_split_adjusted(df, actions=None):
    if df.empty:
        return df

    work = df.copy()
    work["date"] = pd.to_datetime(work["date"])
    work = work.sort_values("date")

    # 1. Initialize split factor column
    # Yahoo's 'stock_splits' column in the 'work' dataframe is now completely ignored.
    work["stock_splits"] = 1.0
    
    # 2. POPULATE exclusively with verified corporate actions (from NSE)
    if actions is not None and not actions.empty:
        actions = actions.copy()
        actions["date"] = pd.to_datetime(actions["date"])
        
        # Match actions to our price dates
        for row in actions.itertuples():
            # Find the exact date or closest following trading date
            matches = work[work["date"] >= row.date]
            if not matches.empty:
                target_idx = matches.index[0]
                actual_dt = work.loc[target_idx, "date"].strftime("%Y-%m-%d")
                log.info(f"Applying verified NSE {row.action_type} {row.value} for {work.loc[target_idx, 'symbol']} on {actual_dt}")
                # MULTIPLY if there are multiple actions on the same day
                work.loc[target_idx, "stock_splits"] *= row.value

    # 3. Double-check NSE splits against raw price action for sanity
    prev_close = work["close"].shift(1)
    raw_ratio = work["close"] / prev_close
    
    split_indices = work[(work["stock_splits"] > 1.0)].index
    
    for idx in split_indices:
        split = work.loc[idx, "stock_splits"]
        ratio = raw_ratio.loc[idx]
        symbol = work.loc[idx, "symbol"]
        dt = work.loc[idx, "date"].strftime("%Y-%m-%d")
        
        if pd.notna(ratio) and ratio > 0:
            expected_ratio = 1.0 / split
            # Even for NSE data, we verify if the price actually moved.
            # If it didn't move as expected but stayed near 1.0, it's likely pre-adjusted by the provider.
            if abs(ratio - 1.0) < abs(ratio - expected_ratio):
                log.info(
                    f"Detected likely pre-adjusted prices for {symbol} on {dt} (Ratio {ratio:.2f}). "
                    f"Un-adjusting raw data before this date to compensate for split {split}."
                )
                # Un-adjust all prices PRIOR to this split date
                # Use positional indexing to be safe with the sorted dataframe
                idx_pos = work.index.get_loc(idx)
                if idx_pos > 0:
                    for col in ["open", "high", "low", "close"]:
                        work.iloc[:idx_pos, work.columns.get_loc(col)] *= split
                    work.iloc[:idx_pos, work.columns.get_loc("volume")] /= split
                # We KEEP the stock_splits value so the cumulative split_factor calculation 
                # correctly adjusts the shares and provides a consistent history.
            else:
                log.info(f"Verified NSE split {split} for {symbol} on {dt}")

    # 4. Calculate cumulative split factor
    split_multiplier = work["stock_splits"].replace(0.0, 1.0)
    # Price factor: product of splits AFTER this date
    future_factor = split_multiplier.iloc[::-1].cumprod().iloc[::-1].shift(-1, fill_value=1.0)
    work["split_factor"] = future_factor.astype(float)
    # Share factor: product of splits ON OR AFTER this date
    work["share_factor"] = (work["split_factor"] * split_multiplier).astype(float)

    for price_col in ["open", "high", "low", "close"]:
        work[price_col] = (
            pd.to_numeric(work[price_col], errors="coerce") / work["split_factor"]
        ).apply(truncate_to_2dp)
    work["volume"] = pd.to_numeric(work["volume"], errors="coerce") * work["split_factor"]

    # Drop any rows where adjusted close is missing or zero before calculating MAs
    # This prevents holiday/empty rows from breaking the rolling window calculation
    work = work.dropna(subset=["close"]).copy()
    work = work[work["close"] > 0].copy()

    close_series = pd.to_numeric(work["close"], errors="coerce")
    for window in [5, 10, 20, 50, 100, 200]:
        work[f"ma_{window}"] = close_series.rolling(window=window, min_periods=window).mean().round(4)

    # Use None instead of pd.NA for SQLite compatibility
    work["shares_outstanding"] = None
    work["market_cap_cr"] = None

    return work[
        [
            "symbol", "date", "open", "high", "low", "close", "volume", "split_factor", "share_factor",
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
        # Ensure any placeholders are converted to None for SQLite
        return work.where(pd.notnull(work), None)

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

    # Drop the placeholder columns before merging to avoid suffix collisions (_x, _y)
    cols_to_drop = [c for c in ["shares_outstanding", "market_cap_cr"] if c in work.columns]
    work = work.drop(columns=cols_to_drop)

    merged = pd.merge_asof(
        work,
        shares[["date", "shares_outstanding"]],
        on="date",
        direction="backward",
    )
    merged["shares_outstanding"] = merged["shares_outstanding"].fillna(
        shares["shares_outstanding"].iloc[0]
    )
    
    # Smart Share Adjustment: Detect if raw shares are already post-split on the split day
    # OR if the adjustment is delayed by a few days (common on Yahoo Finance)
    raw_shares_series = pd.to_numeric(merged["shares_outstanding"], errors="coerce")
    final_shares = []
    
    # Pre-calculate known delayed jumps
    delayed_jumps = {} # {split_date_obj: jump_detected_idx}
    for row in merged.itertuples():
        s_factor = float(row.share_factor)
        p_factor = float(row.split_factor)
        split_today = s_factor / p_factor if p_factor > 0 else 1.0
        if split_today > 1.1:
            lookahead = merged.loc[row.Index : row.Index + 7]
            for next_row in lookahead.itertuples():
                if next_row.Index == 0: continue
                prev_val = float(merged.loc[next_row.Index - 1, "shares_outstanding"])
                if prev_val > 0 and abs(float(next_row.shares_outstanding) / prev_val - split_today) < abs(float(next_row.shares_outstanding) / prev_val - 1.0):
                    delayed_jumps[row.date] = next_row.Index
                    log.info(f"SMART SHARE: Delayed split jump for {row.symbol} on {next_row.date} (Split Date: {row.date})")
                    break

    for row in merged.itertuples():
        current_raw_shares = float(row.shares_outstanding)
        
        # Logic: 
        # 1. Start with split_factor (adjusts all historical splits AFTER today to current basis)
        effective_factor = float(row.split_factor)
        
        # 2. For every split that has happened ON or BEFORE today:
        # We need to decide if we also need to adjust TODAY'S raw shares for that specific split.
        for split_date, jump_idx in delayed_jumps.items():
            if row.date >= split_date:
                if row.Index < jump_idx:
                    # This split has 'happened' but isn't in raw shares yet.
                    # SCALE UP.
                    match_row = merged[merged["date"] == split_date].iloc[0]
                    ratio = match_row.share_factor / match_row.split_factor
                    effective_factor *= ratio
                else:
                    # This split IS in raw shares. No extra factor needed.
                    pass
            else:
                # This split hasn't happened yet. Standard cumulative logic handles it.
                pass
        
        final_shares.append(current_raw_shares * effective_factor)

    merged["shares_outstanding"] = final_shares
    merged["market_cap_cr"] = (
        pd.to_numeric(merged["close"], errors="coerce") *
        pd.to_numeric(merged["shares_outstanding"], errors="coerce") / 1e7
    ).round(4)
    merged["date"] = merged["date"].dt.strftime("%Y-%m-%d")
    
    # Convert NAType/NaN to None for SQLite safety
    return merged.where(pd.notnull(merged), None)


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
            actions = load_corporate_actions(conn, symbol)
            adjusted = build_split_adjusted(raw, actions=actions)
            if preserve_market_cap:
                shares = load_share_history(conn, symbol)
                adjusted = attach_market_cap(adjusted, shares)
                existing_market_caps = load_adjusted_market_caps(conn, symbol)
                adjusted = preserve_existing_market_cap(adjusted, existing_market_caps)
            else:
                shares = load_share_history(conn, symbol)
                adjusted = attach_market_cap(adjusted, shares)
            replace_adjusted_prices(conn, symbol, adjusted)
            save_indicators(conn, adjusted)
            save_market_caps(conn, adjusted)
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
            actions = load_corporate_actions(conn, symbol)
            adjusted = build_split_adjusted(raw, actions=actions)
            shares = load_share_history(conn, symbol)
            adjusted = attach_market_cap(adjusted, shares)
            target_dates = {str(date_val) for date_val in changed_dates}
            subset = adjusted[adjusted["date"].isin(target_dates)].copy()
            upsert_adjusted_prices(conn, subset)
            save_indicators(conn, subset)
            save_market_caps(conn, subset)
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
