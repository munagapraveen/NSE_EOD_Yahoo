"""Yahoo Finance download helpers."""

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd
import yfinance as yf

from config import DEFAULT_LOOKBACK_DAYS


PRICE_COLUMNS = ["open", "high", "low", "close", "adj_close"]


def truncate_to_2dp(value):
    """Round numeric values to 2 decimal places using ROUND_HALF_UP."""
    if pd.isna(value):
        return value
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def normalize_yahoo_history(history, tickers):
    if history.empty:
        return pd.DataFrame(
            columns=[
                "symbol", "date", "open", "high", "low", "close",
                "adj_close", "volume", "dividends", "stock_splits", "source",
            ]
        )

    frames = []
    if isinstance(history.columns, pd.MultiIndex):
        for ticker in tickers:
            if ticker not in history.columns.get_level_values(0):
                continue
            frame = history[ticker].copy()
            frame["yahoo_symbol"] = ticker
            frames.append(frame.reset_index())
    else:
        frame = history.copy().reset_index()
        frame["yahoo_symbol"] = tickers[0]
        frames.append(frame)

    result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if result.empty:
        return result

    rename = {
        "Date": "date",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Adj Close": "adj_close",
        "Volume": "volume",
        "Dividends": "dividends",
        "Stock Splits": "stock_splits",
    }
    result = result.rename(columns=rename)
    result["date"] = pd.to_datetime(result["date"]).dt.strftime("%Y-%m-%d")
    result["dividends"] = result["dividends"].fillna(0.0) if "dividends" in result.columns else 0.0
    result["stock_splits"] = result["stock_splits"].fillna(0.0) if "stock_splits" in result.columns else 0.0
    for col in PRICE_COLUMNS:
        if col in result.columns:
            result[col] = pd.to_numeric(result[col], errors="coerce").apply(truncate_to_2dp)
    result["source"] = "yahoo"
    return result


def download_history_batch(symbol_df, start=None, end=None):
    if symbol_df.empty:
        return pd.DataFrame()

    yahoo_symbols = symbol_df["yahoo_symbol"].tolist()
    if start is None:
        start = (datetime.today() - timedelta(days=DEFAULT_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    history = yf.download(
        tickers=yahoo_symbols,
        start=start,
        end=end,
        auto_adjust=False,
        actions=True,
        progress=False,
        group_by="ticker",
        threads=True,
    )
    result = normalize_yahoo_history(history, yahoo_symbols)
    if result.empty:
        return result

    symbol_map = dict(zip(symbol_df["yahoo_symbol"], symbol_df["symbol"]))
    result["symbol"] = result["yahoo_symbol"].map(symbol_map)
    return result[
        [
            "symbol", "date", "open", "high", "low", "close",
            "adj_close", "volume", "dividends", "stock_splits", "source",
        ]
    ].dropna(subset=["symbol", "close"])
