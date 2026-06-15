# Yahoo NSE EOD

Standalone NSE end-of-day downloader built around Yahoo Finance for price history and NSE public files for symbol master maintenance.

This project is intentionally independent of the Zerodha/Kite codebase:

- no Zerodha credentials
- no Zerodha imports
- its own SQLite database
- its own logging
- its own symbol master pipeline

## Scope

First phase focuses on backend functionality:

- NSE symbol master sync
- Yahoo EOD history download
- Yahoo historical shares outstanding download
- split/dividend action capture
- split-adjusted OHLCV materialization
- moving averages: MA5, MA10, MA20, MA50, MA100, MA200
- symbol rename detection and application

UI can be added later.

## Project layout

- [config.py](D:/Praveen/Codex/NSED/yahoo_nse_eod/config.py)
- [db.py](D:/Praveen/Codex/NSED/yahoo_nse_eod/db.py)
- [nse.py](D:/Praveen/Codex/NSED/yahoo_nse_eod/nse.py)
- [yahoo_client.py](D:/Praveen/Codex/NSED/yahoo_nse_eod/yahoo_client.py)
- [sync_symbols.py](D:/Praveen/Codex/NSED/yahoo_nse_eod/sync_symbols.py)
- [download_eod.py](D:/Praveen/Codex/NSED/yahoo_nse_eod/download_eod.py)
- [sync_share_counts.py](D:/Praveen/Codex/NSED/yahoo_nse_eod/sync_share_counts.py)
- [corporate_actions.py](D:/Praveen/Codex/NSED/yahoo_nse_eod/corporate_actions.py)
- [adjust_splits.py](D:/Praveen/Codex/NSED/yahoo_nse_eod/adjust_splits.py)
- [sharpe_screener.py](D:/Praveen/Codex/NSED/yahoo_nse_eod/sharpe_screener.py)
- [query_prices.py](D:/Praveen/Codex/NSED/yahoo_nse_eod/query_prices.py)
- [symbol_change_handler.py](D:/Praveen/Codex/NSED/yahoo_nse_eod/symbol_change_handler.py)
- [gui_y.py](D:/Praveen/Codex/NSED/yahoo_nse_eod/gui_y.py)

## Database

The project stores:

- `symbols`: current NSE symbol master with ISIN and Yahoo ticker
- `symbol_aliases`: old symbol to new symbol mappings
- `share_history`: historical shares outstanding from Yahoo
  This is a source/staging table used to calculate market cap for new dates.
- `raw_eod_prices`: raw Yahoo OHLCV, `Adj Close`, dividends, and splits
- `adjusted_eod_prices`: split-adjusted OHLCV generated from raw history
- `marketcap`: split-adjusted `shares_outstanding` and `market_cap_cr`
- `indicators`: `ma_5`, `ma_10`, `ma_20`, `ma_50`, `ma_100`, `ma_200`
- `corporate_actions`: verified split and bonus events synced from NSE

## Suggested flow

1. Sync NSE symbols

```powershell
cd D:\Praveen\Codex\NSED\yahoo_nse_eod
python sync_symbols.py
```

2. Bootstrap Yahoo history

```powershell
python download_eod.py --bootstrap
python download_eod.py --bootstrap --batch-size 50 --retry-sleep 2
```

3. Download historical shares outstanding

```powershell
python sync_share_counts.py
python sync_share_counts.py --only-missing --workers 4
python sync_share_counts.py --recent-days 120
```

4. Build split-adjusted prices, market cap, and moving averages

```powershell
python adjust_splits.py
```

5. Detect symbol changes

```powershell
python symbol_change_handler.py
```

6. Apply symbol changes when you are comfortable

```powershell
python symbol_change_handler.py --apply
```

7. Daily incremental refresh

```powershell
python download_eod.py
python download_eod.py --batch-size 50 --retry-sleep 2
python download_eod.py --symbols RELIANCE,TCS,INFY
```

`download_eod.py` now automatically updates split-adjusted prices, market cap, and
moving averages for the newly downloaded dates in that run. `adjust_splits.py` is
still useful for a full rebuild or after schema/logic changes.

8. Review actions or rebuild only split-affected symbols

```powershell
python corporate_actions.py
python corporate_actions.py --type split --rebuild
```

9. Inspect one symbol's adjusted close, market cap, and moving averages

```powershell
python query_prices.py --symbol RELIANCE --limit 20
python query_prices.py --symbol TCS --from 2025-01-01 --to 2025-03-31
python query_prices.py --latest --limit 100 --csv latest_snapshot.csv
```

10. Run the Sharpe screener on the standalone DB

```powershell
python sharpe_screener.py
python sharpe_screener.py --top 100
python sharpe_screener.py --mcap 500 --rf 8 --turnover 2
python sharpe_screener.py --date 2025-12-31 --long-months 6 --short-months 3
```

11. Launch the PySide6 GUI

```powershell
python gui_y.py
```

## Notes

- Yahoo is convenient but unofficial, so occasional gaps or throttling can happen.
- NSE is used here for symbol maintenance, not price history.
- Market cap is calculated on the split-adjusted basis so historical market caps stay stable when old prices are back-adjusted after a split refresh.
- Corporate-action rebuilds preserve previously stored historical market-cap values by date, and adjust stored shares outstanding to match the refreshed adjusted close.
- In other words: `share_history` helps create market cap for new dates, but `marketcap.market_cap_cr` is the authoritative historical market-cap series after insertion.
- `sync_share_counts.py` now uses controlled parallelism, retries failed symbols sequentially at the end, and writes unresolved failures to `data/share_download_failures_latest.csv`.
- `download_eod.py` retries failed Yahoo batches with backoff, then falls back to one-symbol-at-a-time recovery for stubborn/rate-limited batches, and writes unresolved failures to `data/eod_download_failures_latest.csv`.
- Dividend-adjusted total-return series can be added later if needed.
