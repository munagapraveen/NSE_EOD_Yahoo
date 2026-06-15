import tempfile
import unittest
from pathlib import Path
import sys
from unittest import mock
import types

import pandas as pd

TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

sys.modules.setdefault("yfinance", types.SimpleNamespace())
try:
    import requests
except ImportError:
    sys.modules["requests"] = types.SimpleNamespace()

import download_eod


class DownloadEODTests(unittest.TestCase):
    def test_parse_args(self):
        options = download_eod.parse_args(
            ["--bootstrap", "--limit", "20", "--batch-size", "15", "--retry-sleep", "1.5", "--single-retry-sleep", "0.3", "--symbols", "AAA,BBB"]
        )
        self.assertTrue(options["bootstrap"])
        self.assertEqual(options["limit"], 20)
        self.assertEqual(options["batch_size"], 15)
        self.assertEqual(options["retry_sleep_secs"], 1.5)
        self.assertEqual(options["single_retry_sleep_secs"], 0.3)
        self.assertEqual(options["symbols"], ["AAA", "BBB"])


    def test_download_with_retries_falls_back_to_single_symbol(self):
        batch = pd.DataFrame([
            {"symbol": "AAA", "yahoo_symbol": "AAA.NS"},
            {"symbol": "BBB", "yahoo_symbol": "BBB.NS"},
        ])
        attempts = {"batch": 0, "single": {}}

        def fake_downloader(symbol_df, start=None, end=None):
            if len(symbol_df) > 1:
                attempts["batch"] += 1
                raise RuntimeError("Too Many Requests")
            yahoo_symbol = symbol_df.iloc[0]["yahoo_symbol"]
            attempts["single"][yahoo_symbol] = attempts["single"].get(yahoo_symbol, 0) + 1
            return pd.DataFrame([
                {
                    "symbol": symbol_df.iloc[0]["symbol"],
                    "date": "2025-01-01",
                    "open": 10,
                    "high": 12,
                    "low": 9,
                    "close": 11,
                    "adj_close": 11,
                    "volume": 1000,
                    "dividends": 0.0,
                    "stock_splits": 0.0,
                    "source": "yahoo",
                }
            ])

        history, failures = download_eod.download_with_retries(
            batch,
            "2025-01-01",
            downloader=fake_downloader,
            retry_sleep_secs=0,
        )
        self.assertEqual(attempts["batch"], download_eod.MAX_BATCH_RETRIES)
        self.assertEqual(len(history), 2)
        self.assertEqual(failures, [])

    def test_download_with_retries_recovers_missing_symbols_from_partial_batch(self):
        batch = pd.DataFrame([
            {"symbol": "AAA", "yahoo_symbol": "AAA.NS"},
            {"symbol": "BBB", "yahoo_symbol": "BBB.NS"},
        ])
        calls = {"batch": 0, "single": 0}

        def fake_downloader(symbol_df, start=None, end=None):
            if len(symbol_df) > 1:
                calls["batch"] += 1
                return pd.DataFrame([
                    {
                        "symbol": "AAA",
                        "date": "2025-01-01",
                        "open": 10,
                        "high": 12,
                        "low": 9,
                        "close": 11,
                        "adj_close": 11,
                        "volume": 1000,
                        "dividends": 0.0,
                        "stock_splits": 0.0,
                        "source": "yahoo",
                    }
                ])
            calls["single"] += 1
            return pd.DataFrame([
                {
                    "symbol": "BBB",
                    "date": "2025-01-01",
                    "open": 20,
                    "high": 21,
                    "low": 19,
                    "close": 20.5,
                    "adj_close": 20.5,
                    "volume": 2000,
                    "dividends": 0.0,
                    "stock_splits": 0.0,
                    "source": "yahoo",
                }
            ])

        history, failures = download_eod.download_with_retries(
            batch,
            "2025-01-01",
            downloader=fake_downloader,
            retry_sleep_secs=0,
        )

        self.assertEqual(calls["batch"], 1)
        self.assertEqual(calls["single"], 1)
        self.assertEqual(sorted(history["symbol"].unique().tolist()), ["AAA", "BBB"])
        self.assertEqual(failures, [])

    def test_run_eod_download_tracks_failures(self):
        symbols = pd.DataFrame([
            {"symbol": "AAA", "yahoo_symbol": "AAA.NS"},
        ])
        last_dates = {}
        persisted_calls = []

        def fake_downloader(symbol_df, start=None, end=None):
            raise RuntimeError("Too Many Requests")

        def fake_persist(history, bootstrap):
            persisted_calls.append((history, bootstrap))
            return {"rows": 0, "actions": 0, "touched_symbols": []}

        with mock.patch.object(download_eod, "persist_history", side_effect=fake_persist):
            with mock.patch.object(download_eod, "check_market_availability", return_value=(True, None)):
                summary = download_eod.run_eod_download(
                    symbols,
                    last_dates,
                    bootstrap=False,
                    batch_size=10,
                    downloader=fake_downloader,
                    retry_sleep_secs=0,
                    single_retry_sleep_secs=0,
                )

        self.assertEqual(summary["total_rows"], 0)
        self.assertEqual(len(summary["failures"]), 1)
        self.assertEqual(persisted_calls, [])

    def test_check_market_availability_new_data_available(self):
        last_dates = {
            "RELIANCE": "2025-01-01",
            "TCS": "2025-01-01",
            "INFY": "2025-01-01",
            "HDFCBANK": "2025-01-01",
            "ICICIBANK": "2025-01-01",
            "A": "2025-01-01",
            "B": "2025-01-01",
            "C": "2025-01-01",
            "D": "2025-01-01",
            "E": "2025-01-01",
            "F": "2025-01-01",
        }
        
        mock_ticker = mock.MagicMock()
        mock_index = pd.DatetimeIndex(["2025-01-02"])
        mock_df = pd.DataFrame([{"Close": 100.0}], index=mock_index)
        mock_ticker.history.return_value = mock_df
        
        with mock.patch("yfinance.Ticker", return_value=mock_ticker, create=True):
            has_new, latest = download_eod.check_market_availability(last_dates)
            
        self.assertTrue(has_new)
        self.assertEqual(latest, "2025-01-02")

    def test_check_market_availability_no_new_data(self):
        last_dates = {
            "RELIANCE": "2025-01-02",
            "TCS": "2025-01-02",
            "INFY": "2025-01-02",
            "HDFCBANK": "2025-01-02",
            "ICICIBANK": "2025-01-02",
            "A": "2025-01-02",
            "B": "2025-01-02",
            "C": "2025-01-02",
            "D": "2025-01-02",
            "E": "2025-01-02",
            "F": "2025-01-02",
        }
        
        mock_ticker = mock.MagicMock()
        mock_index = pd.DatetimeIndex(["2025-01-02"])
        mock_df = pd.DataFrame([{"Close": 100.0}], index=mock_index)
        mock_ticker.history.return_value = mock_df
        
        with mock.patch("yfinance.Ticker", return_value=mock_ticker, create=True):
            has_new, latest = download_eod.check_market_availability(last_dates)
            
        self.assertFalse(has_new)
        self.assertEqual(latest, "2025-01-02")

    def test_save_failure_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "eod_failures.csv"
            failures = [
                {"symbol": "AAA", "yahoo_symbol": "AAA.NS", "stage": "single-fallback", "error": "Too Many Requests"}
            ]
            with mock.patch.object(download_eod, "FAILED_EOD_FILE", report_path):
                download_eod.save_failure_report(failures)
            self.assertTrue(report_path.exists())
            content = report_path.read_text(encoding="utf-8")
            self.assertIn("AAA", content)
            self.assertIn("Too Many Requests", content)


if __name__ == "__main__":
    unittest.main()
