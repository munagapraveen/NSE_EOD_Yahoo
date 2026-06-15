import sqlite3
import sys
import types
import unittest
from unittest import mock

import pandas as pd

try:
    import requests
except ImportError:
    sys.modules["requests"] = types.SimpleNamespace()

import db
import sync_corporate_actions


class SyncCorporateActionsTests(unittest.TestCase):
    def test_run_corporate_sync_rebuilds_only_changed_symbols(self):
        conn = sqlite3.connect(":memory:")
        try:
            db.setup_schema(conn)
            db.upsert_corporate_actions(conn, [{
                "symbol": "AAA",
                "ex_date": "2025-01-01",
                "action_type": "split",
                "value": 2.0,
                "source": "nse",
                "note": "old note",
            }])

            fetched = pd.DataFrame([
                {
                    "symbol": "AAA",
                    "ex_date": "2025-01-01",
                    "action_type": "split",
                    "value": 2.0,
                    "source": "nse",
                    "note": "updated note",
                },
                {
                    "symbol": "BBB",
                    "ex_date": "2025-02-01",
                    "action_type": "bonus",
                    "value": 1.5,
                    "source": "nse",
                    "note": "new action",
                },
            ])
            rebuilt = []

            def fake_rebuild(symbols, preserve_market_cap=False):
                rebuilt.append((symbols, preserve_market_cap))

            with mock.patch.object(sync_corporate_actions, "fetch_nse_corporate_actions", return_value=fetched):
                with mock.patch.object(sync_corporate_actions, "get_connection") as fake_get_connection:
                    fake_get_connection.return_value.__enter__.return_value = conn
                    summary = sync_corporate_actions.run_corporate_sync(
                        rebuild=True,
                        rebuild_func=fake_rebuild,
                    )

            self.assertEqual(summary["synced"], 2)
            self.assertEqual(summary["rebuilt_symbols"], ["BBB"])
            self.assertEqual(rebuilt, [(["BBB"], True)])
        finally:
            conn.close()

    def test_run_corporate_sync_with_rebuild_false(self):
        conn = sqlite3.connect(":memory:")
        try:
            db.setup_schema(conn)
            fetched = pd.DataFrame([
                {
                    "symbol": "CCC",
                    "ex_date": "2025-01-01",
                    "action_type": "split",
                    "value": 2.0,
                    "source": "nse",
                    "note": "note",
                }
            ])
            rebuilt = []
            def fake_rebuild(symbols, preserve_market_cap=False):
                rebuilt.append(symbols)

            with mock.patch.object(sync_corporate_actions, "fetch_nse_corporate_actions", return_value=fetched):
                with mock.patch.object(sync_corporate_actions, "get_connection") as fake_get_connection:
                    fake_get_connection.return_value.__enter__.return_value = conn
                    summary = sync_corporate_actions.run_corporate_sync(
                        rebuild=False,
                        rebuild_func=fake_rebuild,
                    )
            self.assertEqual(summary["synced"], 1)
            self.assertEqual(summary["rebuilt_symbols"], [])
            self.assertEqual(rebuilt, [])
        finally:
            conn.close()

    def test_run_corporate_sync_value_change_triggers_rebuild(self):
        conn = sqlite3.connect(":memory:")
        try:
            db.setup_schema(conn)
            db.upsert_corporate_actions(conn, [{
                "symbol": "AAA",
                "ex_date": "2025-01-01",
                "action_type": "split",
                "value": 2.0,
                "source": "nse",
                "note": "note",
            }])
            # Value changes from 2.0 to 10.0
            fetched = pd.DataFrame([
                {
                    "symbol": "AAA",
                    "ex_date": "2025-01-01",
                    "action_type": "split",
                    "value": 10.0,
                    "source": "nse",
                    "note": "updated note",
                }
            ])
            rebuilt = []
            def fake_rebuild(symbols, preserve_market_cap=False):
                rebuilt.append(symbols)

            with mock.patch.object(sync_corporate_actions, "fetch_nse_corporate_actions", return_value=fetched):
                with mock.patch.object(sync_corporate_actions, "get_connection") as fake_get_connection:
                    fake_get_connection.return_value.__enter__.return_value = conn
                    summary = sync_corporate_actions.run_corporate_sync(
                        rebuild=True,
                        rebuild_func=fake_rebuild,
                    )
            self.assertEqual(summary["synced"], 1)
            self.assertEqual(summary["rebuilt_symbols"], ["AAA"])
            self.assertEqual(rebuilt, [["AAA"]])
        finally:
            conn.close()

    def test_run_corporate_sync_empty_fetch(self):
        conn = sqlite3.connect(":memory:")
        try:
            db.setup_schema(conn)
            rebuilt = []
            def fake_rebuild(symbols, preserve_market_cap=False):
                rebuilt.append(symbols)

            with mock.patch.object(sync_corporate_actions, "fetch_nse_corporate_actions", return_value=pd.DataFrame()):
                summary = sync_corporate_actions.run_corporate_sync(
                    rebuild=True,
                    rebuild_func=fake_rebuild,
                )
            self.assertEqual(summary["synced"], 0)
            self.assertEqual(summary["rebuilt_symbols"], [])
            self.assertEqual(rebuilt, [])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
