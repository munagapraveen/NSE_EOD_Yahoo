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

    # Cast volume to float to allow fractional intermediate math without LossySetitemError
    work["volume"] = work["volume"].astype(float)

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

    # Round volume for storage
    work["volume"] = work["volume"].round()

    return work[
        [
            "symbol", "date", "open", "high", "low", "close", "volume", "split_factor", "share_factor",
            "shares_outstanding", "market_cap_cr",
            "ma_5", "ma_10", "ma_20", "ma_50", "ma_100", "ma_200",
        ]
    ]


def attach_market_cap(adjusted_df, share_df):
    """
    Compute true historical market cap: actual_price × actual_shares_outstanding.

    Yahoo's 'Close' is retroactively split-adjusted (÷ split_factor). To recover
    the actual traded price we multiply back by split_factor:

        actual_price = raw_close × split_factor
                     = adj_close × split_factor²

    Yahoo's shares_outstanding (from get_shares_full) is point-in-time from
    quarterly filings, so it is NOT retroactively adjusted for splits.

    However, depending on when share data was downloaded, two situations arise:

      JUMP  (~80%): shares jumped by ~split_factor on/near ex-date.
                    Shares are actual pre-split counts before the event.
                    → price_scale = split_factor for pre-event dates.
                    → market_cap = adj_close × split_factor × price_scale × shares
                                 = adj_close × split_factor² × shares
                                 = actual_price × actual_shares  ✓

      NO JUMP (~20%): shares already at post-split level for all dates
                      (Yahoo retroactively updated them via later filings).
                    → price_scale = 1.0 for all dates.
                    → market_cap = adj_close × split_factor × 1.0 × shares
                                 = raw_close × shares
                                 = actual_price × actual_shares  ✓
                      (post-split shares cancel the ÷split_factor in raw_close)

    Detection: for each corporate action, inspect share_history in a ±5-day
    window around the ex-date. If shares jumped by ≈split_factor → JUMP.
    """
    if adjusted_df.empty:
        return adjusted_df

    work = adjusted_df.copy()
    work["date"] = pd.to_datetime(work["date"])

    if share_df.empty:
        work["date"] = work["date"].dt.strftime("%Y-%m-%d")
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

    # Drop placeholder columns before merging to avoid suffix collisions
    cols_to_drop = [c for c in ["shares_outstanding", "market_cap_cr"] if c in work.columns]
    work = work.drop(columns=cols_to_drop)

    # Align point-in-time shares to each price date (last-known-forward-fill)
    merged = pd.merge_asof(
        work,
        shares[["date", "shares_outstanding"]],
        on="date",
        direction="backward",
    )
    # H-1 fix: for dates before the first share history entry, fill forward
    # using the earliest known share value SCALED BACK by split_factor.
    # (earliest share count is typically post-split; dividing by split_factor
    #  recovers the pre-split share count for those earlier dates.)
    first_share_val = float(shares["shares_outstanding"].iloc[0])
    missing_mask = merged["shares_outstanding"].isna()
    if missing_mask.any():
        sf_at_missing = pd.to_numeric(
            merged.loc[missing_mask, "split_factor"], errors="coerce"
        ).fillna(1.0)
        # split_factor for earliest dates = cumulative factor STILL TO BE applied
        # If shares.iloc[0] is post-split, dividing by (sf_at_date / sf_at_first_share_date)
        # gives the actual share count at that early date.
        sf_at_first_share = pd.to_numeric(
            merged.loc[merged["date"] >= shares["date"].iloc[0], "split_factor"],
            errors="coerce"
        ).iloc[0] if (merged["date"] >= shares["date"].iloc[0]).any() else 1.0
        scale = (sf_at_missing / sf_at_first_share).clip(lower=1.0)
        merged.loc[missing_mask, "shares_outstanding"] = (first_share_val / scale).round(0)
    # Any remaining NaN (no share data at all) fall back to first known value
    merged["shares_outstanding"] = merged["shares_outstanding"].fillna(first_share_val)

    # ------------------------------------------------------------------ #
    # Step 1: Identify all corporate action ex-dates from the price data  #
    # stock_splits is not carried into adjusted_df; instead we derive it  #
    # as share_factor / split_factor (= split multiplier on that date).  #
    # ------------------------------------------------------------------ #
    symbol = merged["symbol"].iloc[0] if "symbol" in merged.columns else "?"
    sf_col   = pd.to_numeric(merged["split_factor"], errors="coerce").replace(0, 1.0)
    shf_col  = pd.to_numeric(merged["share_factor"], errors="coerce").replace(0, 1.0)
    merged["_split_on_date"] = (shf_col / sf_col).round(6)
    raw_action_rows = merged[merged["_split_on_date"] > 1.01][["date", "_split_on_date"]].rename(
        columns={"_split_on_date": "stock_splits"}
    )
    # C-5 fix: aggregate same-date actions by multiplying their split factors.
    # Without this, two actions on the same date produce two entries in action_rows;
    # the JUMP detection loop writes price_scale_map twice with the same key,
    # silently discarding the first detection result (last-write-wins bug).
    action_rows = (
        raw_action_rows.groupby("date", as_index=False)["stock_splits"]
        .prod()  # combined split factor for same-date actions
    )

    # ------------------------------------------------------------------ #
    # Step 2: Detect JUMP vs NO-JUMP for each corporate action            #
    # ------------------------------------------------------------------ #
    # price_scale[action_date] = split_factor if JUMP, else 1.0
    price_scale_map = {}  # {action_date (Timestamp): price_scale_factor}

    for _, arow in action_rows.iterrows():
        action_date = arow["date"]
        sf = float(arow["stock_splits"])

        # Find most recent share entry strictly BEFORE the window (-5 days)
        window_start = action_date - pd.Timedelta(days=5)
        window_end   = action_date + pd.Timedelta(days=5)

        pre_shares_df = shares[shares["date"] < window_start]
        post_shares_df = shares[shares["date"] <= window_end]

        if pre_shares_df.empty or post_shares_df.empty:
            # Not enough data to detect — default to NO JUMP (safer)
            price_scale_map[action_date] = 1.0
            log.info(
                f"MCap [{symbol}] {action_date.date()}: insufficient share data for jump "
                f"detection, defaulting to NO-JUMP (price_scale=1.0)"
            )
            continue

        shares_before = float(pre_shares_df["shares_outstanding"].iloc[-1])
        shares_after  = float(post_shares_df["shares_outstanding"].iloc[-1])

        if shares_before <= 0:
            price_scale_map[action_date] = 1.0
            continue

        ratio = shares_after / shares_before
        tolerance = sf * 0.15  # 15% tolerance around expected jump

        if abs(ratio - sf) <= tolerance:
            # Shares jumped by ~split_factor → point-in-time actual shares
            price_scale_map[action_date] = sf
            log.info(
                f"MCap [{symbol}] {action_date.date()}: JUMP detected "
                f"(shares {shares_before:,.0f} → {shares_after:,.0f}, ratio={ratio:.3f}, sf={sf}). "
                f"price_scale={sf} (adj×sf²×shares = true historical mcap)"
            )
        else:
            # No meaningful jump → shares already retroactively at post-split level
            price_scale_map[action_date] = 1.0
            log.info(
                f"MCap [{symbol}] {action_date.date()}: NO JUMP detected "
                f"(shares {shares_before:,.0f} → {shares_after:,.0f}, ratio={ratio:.3f}, sf={sf}). "
                f"price_scale=1.0 (raw_close×shares = true historical mcap)"
            )

    # ------------------------------------------------------------------ #
    # Step 3: Build per-row price_scale column                            #
    #                                                                     #
    # After build_split_adjusted(), adj_close x split_factor already     #
    # equals the actual traded price in ALL cases.                        #
    #                                                                     #
    # market_cap = actual_price x price_scale x hist_shares / 1e7        #
    #                                                                     #
    # JUMP (pre-split actual shares in share_history):                    #
    #   price_scale = 1.0 (default)                                       #
    #   = actual_price x pre_split_shares = true mcap  OK                #
    #                                                                     #
    # NO JUMP (shares already post-split = S x sf throughout):           #
    #   naive = actual_price x S x sf = true_mcap x sf  (sf x too high) #
    #   fix:  price_scale = 1 / sf  =>  actual_price / sf x S x sf = true mcap OK
    # ------------------------------------------------------------------ #
    merged["price_scale"] = 1.0

    for action_date, ps in price_scale_map.items():
        sf_series = action_rows[action_rows["date"] == action_date]["stock_splits"]
        if sf_series.empty:
            continue
        sf_val = float(sf_series.iloc[0])

        if ps == 1.0:
            # NO-JUMP: shares are already post-split for all dates.
            # actual_price x post_split_shares / 1e7 = true_mcap x sf (too high)
            # Fix: divide by sf for pre-event dates.
            if sf_val > 1.0:
                pre_mask = merged["date"] < action_date
                merged.loc[pre_mask, "price_scale"] = (
                    merged.loc[pre_mask, "price_scale"] / sf_val
                )
        else:
            # JUMP: shares are actual pre-split counts UNTIL the share jump.
            # Determine the pre-split share level (last entry before the action window).
            pre_window = action_date - pd.Timedelta(days=5)
            pre_shares_series = shares[shares["date"] < pre_window]["shares_outstanding"]
            if pre_shares_series.empty:
                continue
            pre_split_level = float(pre_shares_series.iloc[-1])
            pre_split_threshold = pre_split_level * 1.3

            on_or_after_mask = merged["date"] >= action_date
            pre_split_shares_mask = (
                pd.to_numeric(merged["shares_outstanding"], errors="coerce") < pre_split_threshold
            )
            fix_mask = on_or_after_mask & pre_split_shares_mask
            if fix_mask.any():
                merged.loc[fix_mask, "price_scale"] = (
                    merged.loc[fix_mask, "price_scale"] * sf_val
                )
            
            # D0 override: on the exact ex-date, Yahoo share counts may be mid-transition.
            # Always pin D0 shares to the confirmed pre-split level for JUMP stocks.
            d0_mask = merged["date"] == action_date
            if d0_mask.any():
                merged.loc[d0_mask, "shares_outstanding"] = pre_split_level

    # ------------------------------------------------------------------ #
    # Step 3b: D0 shares override for NO-JUMP stocks                     #
    # On the ex-date, Yahoo shares are already at post-split level.      #
    # Pin D0 to pre-split count (= post-split / sf) so mcap is correct. #
    # ------------------------------------------------------------------ #
    for action_date, ps in price_scale_map.items():
        if ps != 1.0:
            continue  # JUMP handled in Step 3 above
        sf_series = action_rows[action_rows["date"] == action_date]["stock_splits"]
        if sf_series.empty:
            continue
        sf_val = float(sf_series.iloc[0])
        if sf_val <= 1.0:
            continue
        # For NO-JUMP: D0 shares are at post-split level. Divide by sf to get pre-split.
        d0_mask = merged["date"] == action_date
        if d0_mask.any():
            d0_shares = pd.to_numeric(
                merged.loc[d0_mask, "shares_outstanding"], errors="coerce"
            ).iloc[0]
            if pd.notna(d0_shares) and d0_shares > 0:
                merged.loc[d0_mask, "shares_outstanding"] = round(d0_shares / sf_val, 0)

    # ------------------------------------------------------------------ #
    # Step 4: Compute market cap                                          #
    # actual_price  = adj_close x split_factor                           #
    # market_cap_cr = actual_price x price_scale x hist_shares / 1e7    #
    # ------------------------------------------------------------------ #
    merged["market_cap_cr"] = (
        pd.to_numeric(merged["close"], errors="coerce") *
        pd.to_numeric(merged["split_factor"], errors="coerce") *
        merged["price_scale"] *
        pd.to_numeric(merged["shares_outstanding"], errors="coerce") /
        1e7
    ).round(4)

    cols_to_drop_tmp = [c for c in ["price_scale", "_split_on_date"] if c in merged.columns]
    merged = merged.drop(columns=cols_to_drop_tmp)
    merged["date"] = merged["date"].dt.strftime("%Y-%m-%d")

    return merged.where(pd.notnull(merged), None)


def preserve_existing_market_cap(adjusted_df, existing_df):
    """
    Keep previously stored historical market cap values by date, and derive the
    corresponding shares_outstanding from the stored market_cap_cr and raw_close.

    This is used during corporate-action rebuilds so historical market cap
    remains stable even when adjusted close is rewritten for old dates.

    Back-derivation:
        true_mcap = actual_price × actual_shares
        actual_price = raw_close = adj_close × split_factor
        → actual_shares = true_mcap × 1e7 / raw_close
                        = market_cap_cr × 1e7 / (adj_close × split_factor)
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

    # Back-derive actual shares from stored market cap using raw_close
    # raw_close = adj_close × split_factor (undoes Yahoo's retroactive price adjustment)
    adj_close_series  = pd.to_numeric(work["close"], errors="coerce")
    split_factor_series = pd.to_numeric(work["split_factor"], errors="coerce")
    raw_close_series  = adj_close_series * split_factor_series
    market_cap_series = pd.to_numeric(work["market_cap_cr"], errors="coerce")

    valid = has_existing & raw_close_series.notna() & (raw_close_series != 0)
    work.loc[valid, "shares_outstanding"] = (
        market_cap_series[valid] * 1e7 / raw_close_series[valid]
    ).round(4)

    work.drop(columns=["market_cap_cr_existing"], inplace=True)
    return work


def rebuild_symbols(symbols, preserve_market_cap=False):
    if not symbols:
        log.warning("No symbols to adjust.")
        return

    for idx, symbol in enumerate(symbols, start=1):
        with get_connection() as conn:
            with conn:  # Transaction starts here
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
            
            # H-2 fix: preserve previously stored historical market caps.
            # attach_market_cap() freshly recomputes from share_history, which is
            # correct for new dates but would overwrite stored historical values
            # (which may reflect a different share count at the time of original
            # computation). Only new dates (not already in marketcap table) should
            # use the freshly computed value.
            existing_market_caps = load_adjusted_market_caps(conn, symbol)
            adjusted = preserve_existing_market_cap(adjusted, existing_market_caps)

            # Ensure adjusted['date'] is string and target_dates format matches
            adjusted["date"] = adjusted["date"].astype(str)
            target_dates = {
                pd.to_datetime(d).strftime("%Y-%m-%d") for d in changed_dates
            }
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
