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

import sync_share_counts as share_counts


class DownloadSharesTests(unittest.TestCase):
    def test_parse_args(self):
        options = share_counts.parse_args(
            ["--limit", "10", "--only-missing", "--start", "2025-01-01", "--sleep", "0.1", "--retry-sleep", "0.2", "--workers", "6"]
        )
        self.assertEqual(options["limit"], 10)
        self.assertTrue(options["only_missing"])
        self.assertEqual(options["start"], "2025-01-01")
        self.assertEqual(options["sleep_secs"], 0.1)
        self.assertEqual(options["retry_sleep_secs"], 0.2)
        self.assertEqual(options["workers"], 6)

    def test_run_share_download_retries_failed_symbols(self):
        symbols = pd.DataFrame(
            [
                {"symbol": "AAA", "yahoo_symbol": "AAA.NS"},
                {"symbol": "BBB", "yahoo_symbol": "BBB.NS"},
            ]
        )
        attempts = {}
        persisted = []

        def fake_fetcher(yahoo_symbol, start):
            attempts[yahoo_symbol] = attempts.get(yahoo_symbol, 0) + 1
            if yahoo_symbol == "BBB.NS" and attempts[yahoo_symbol] == 1:
                raise RuntimeError("temporary failure")
            return pd.DataFrame(
                [
                    {"date": "2025-01-01", "shares_outstanding": 1000},
                    {"date": "2025-01-02", "shares_outstanding": 1000},
                ]
            )

        def fake_persist(records):
            persisted.extend(records)

        summary = share_counts.run_share_download(
            symbols,
            workers=2,
            sleep_secs=0,
            retry_sleep_secs=0,
            fetcher=fake_fetcher,
            persist_func=fake_persist,
        )

        self.assertEqual(attempts["AAA.NS"], 1)
        self.assertEqual(attempts["BBB.NS"], 2)
        self.assertEqual(summary["success_symbols"], 2)
        self.assertEqual(len(summary["failed"]), 1)
        self.assertEqual(len([r for r in summary["retried"] if r["stage"] == "retry-success"]), 1)
        self.assertEqual(len(persisted), 4)

    def test_save_failure_report_writes_csv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "failures.csv"
            failed_rows = [
                {"symbol": "AAA", "yahoo_symbol": "AAA.NS", "stage": "retry-failed", "error": "boom"}
            ]
            with mock.patch.object(share_counts, "FAILED_SHARES_FILE", report_path):
                share_counts.save_failure_report(failed_rows)

            self.assertTrue(report_path.exists())
            content = report_path.read_text(encoding="utf-8")
            self.assertIn("AAA", content)
            self.assertIn("retry-failed", content)


if __name__ == "__main__":
    unittest.main()
