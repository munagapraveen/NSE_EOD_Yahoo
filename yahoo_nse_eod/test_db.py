import sqlite3
import unittest

import pandas as pd

import db


class ApplySymbolRenameTests(unittest.TestCase):
    def test_apply_symbol_rename_moves_marketcap_and_indicators(self):
        conn = sqlite3.connect(":memory:")
        try:
            db.setup_schema(conn)

            price_row = pd.DataFrame([
                {
                    "symbol": "OLDSYM",
                    "date": "2025-01-01",
                    "open": 100.0,
                    "high": 105.0,
                    "low": 99.0,
                    "close": 103.0,
                    "volume": 12345.0,
                    "split_factor": 1.0,
                }
            ])
            indicator_row = pd.DataFrame([
                {
                    "symbol": "OLDSYM",
                    "date": "2025-01-01",
                    "ma_5": 101.0,
                    "ma_10": 100.5,
                    "ma_20": None,
                    "ma_50": None,
                    "ma_100": None,
                    "ma_200": None,
                }
            ])
            marketcap_row = pd.DataFrame([
                {
                    "symbol": "OLDSYM",
                    "date": "2025-01-01",
                    "market_cap_cr": 999.9,
                    "shares_outstanding": 9707766.99,
                }
            ])

            db.upsert_symbols(conn, [{
                "symbol": "OLDSYM",
                "yahoo_symbol": "OLDSYM.NS",
                "company_name": "Old Co",
                "isin": "INE123",
                "series": "EQ",
                "instrument_type": "STOCK",
                "active": 1,
                "status": "active",
                "last_seen_date": "2025-01-01",
                "source": "test",
                "last_synced_at": "2025-01-01",
            }, {
                "symbol": "NEWSYM",
                "yahoo_symbol": "NEWSYM.NS",
                "company_name": "New Co",
                "isin": "INE123",
                "series": "EQ",
                "instrument_type": "STOCK",
                "active": 1,
                "status": "active",
                "last_seen_date": "2025-01-01",
                "source": "test",
                "last_synced_at": "2025-01-01",
            }])
            raw_prices = pd.DataFrame([
                {
                    "symbol": "OLDSYM",
                    "date": "2025-01-01",
                    "open": 100.0,
                    "high": 105.0,
                    "low": 99.0,
                    "close": 103.0,
                    "adj_close": 103.0,
                    "volume": 12345.0,
                    "dividends": 0.0,
                    "stock_splits": 1.0,
                    "source": "test",
                }
            ])
            db.insert_raw_prices(conn, raw_prices)
            db.upsert_adjusted_prices(conn, price_row)
            db.save_indicators(conn, indicator_row)
            db.save_market_caps(conn, marketcap_row)
            db.upsert_share_history(conn, [{
                "symbol": "OLDSYM",
                "date": "2025-01-01",
                "shares_outstanding": 1000000.0,
                "source": "test",
            }])

            db.apply_symbol_rename(conn, "OLDSYM", "NEWSYM", source="test")

            # Assert new values exist
            new_indicator = conn.execute(
                "SELECT ma_5 FROM indicators WHERE symbol = ? AND date = ?",
                ("NEWSYM", "2025-01-01"),
            ).fetchone()
            new_marketcap = conn.execute(
                "SELECT market_cap_cr FROM marketcap WHERE symbol = ? AND date = ?",
                ("NEWSYM", "2025-01-01"),
            ).fetchone()
            new_raw = conn.execute(
                "SELECT close FROM raw_eod_prices WHERE symbol = ? AND date = ?",
                ("NEWSYM", "2025-01-01"),
            ).fetchone()
            new_adj = conn.execute(
                "SELECT close FROM adjusted_eod_prices WHERE symbol = ? AND date = ?",
                ("NEWSYM", "2025-01-01"),
            ).fetchone()
            new_shares = conn.execute(
                "SELECT shares_outstanding FROM share_history WHERE symbol = ? AND date = ?",
                ("NEWSYM", "2025-01-01"),
            ).fetchone()

            # Assert old values were cleaned up
            old_count = conn.execute(
                """
                SELECT (SELECT COUNT(*) FROM indicators WHERE symbol = 'OLDSYM') +
                       (SELECT COUNT(*) FROM marketcap WHERE symbol = 'OLDSYM') +
                       (SELECT COUNT(*) FROM raw_eod_prices WHERE symbol = 'OLDSYM') +
                       (SELECT COUNT(*) FROM adjusted_eod_prices WHERE symbol = 'OLDSYM') +
                       (SELECT COUNT(*) FROM share_history WHERE symbol = 'OLDSYM')
                """
            ).fetchone()[0]

            # Assert alias registered
            alias = conn.execute(
                "SELECT COUNT(*) FROM symbol_aliases WHERE old_symbol = ? AND new_symbol = ?",
                ("OLDSYM", "NEWSYM")
            ).fetchone()[0]

            # Assert old symbol status renamed and active=0
            old_sym_info = conn.execute(
                "SELECT active, status FROM symbols WHERE symbol = ?",
                ("OLDSYM",)
            ).fetchone()

            self.assertEqual(new_indicator[0], 101.0)
            self.assertEqual(new_marketcap[0], 999.9)
            self.assertEqual(new_raw[0], 103.0)
            self.assertEqual(new_adj[0], 103.0)
            self.assertEqual(new_shares[0], 1000000.0)
            self.assertEqual(old_count, 0)
            self.assertEqual(alias, 1)
            self.assertEqual(old_sym_info[0], 0)
            self.assertEqual(old_sym_info[1], "renamed")
        finally:
            conn.close()

    def test_apply_symbol_rename_uses_effective_date_for_overlap_resolution(self):
        conn = sqlite3.connect(":memory:")
        try:
            db.setup_schema(conn)
            db.upsert_symbols(conn, [{
                "symbol": "OLDSYM",
                "yahoo_symbol": "OLDSYM.NS",
                "company_name": "Old Co",
                "isin": "INE123",
                "series": "EQ",
                "instrument_type": "STOCK",
                "active": 1,
                "status": "active",
                "last_seen_date": "2025-01-01",
                "source": "test",
                "last_synced_at": "2025-01-01",
            }, {
                "symbol": "NEWSYM",
                "yahoo_symbol": "NEWSYM.NS",
                "company_name": "New Co",
                "isin": "INE123",
                "series": "EQ",
                "instrument_type": "STOCK",
                "active": 1,
                "status": "active",
                "last_seen_date": "2025-01-01",
                "source": "test",
                "last_synced_at": "2025-01-01",
            }])
            old_rows = pd.DataFrame([
                {
                    "symbol": "OLDSYM",
                    "date": "2025-01-01",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.5,
                    "volume": 1000.0,
                    "split_factor": 1.0,
                },
                {
                    "symbol": "OLDSYM",
                    "date": "2025-01-03",
                    "open": 103.0,
                    "high": 104.0,
                    "low": 102.0,
                    "close": 103.5,
                    "volume": 1200.0,
                    "split_factor": 1.0,
                },
            ])
            new_rows = pd.DataFrame([
                {
                    "symbol": "NEWSYM",
                    "date": "2025-01-01",
                    "open": 200.0,
                    "high": 201.0,
                    "low": 199.0,
                    "close": 200.5,
                    "volume": 2000.0,
                    "split_factor": 1.0,
                },
                {
                    "symbol": "NEWSYM",
                    "date": "2025-01-03",
                    "open": 300.0,
                    "high": 301.0,
                    "low": 299.0,
                    "close": 300.5,
                    "volume": 3000.0,
                    "split_factor": 1.0,
                },
            ])
            db.upsert_adjusted_prices(conn, old_rows)
            db.upsert_adjusted_prices(conn, new_rows)

            db.apply_symbol_rename(
                conn,
                "OLDSYM",
                "NEWSYM",
                effective_date="2025-01-02",
                source="test",
            )

            before_cutoff = conn.execute(
                "SELECT close FROM adjusted_eod_prices WHERE symbol = ? AND date = ?",
                ("NEWSYM", "2025-01-01"),
            ).fetchone()[0]
            after_cutoff = conn.execute(
                "SELECT close FROM adjusted_eod_prices WHERE symbol = ? AND date = ?",
                ("NEWSYM", "2025-01-03"),
            ).fetchone()[0]
            old_remaining = conn.execute(
                "SELECT COUNT(*) FROM adjusted_eod_prices WHERE symbol = ?",
                ("OLDSYM",),
            ).fetchone()[0]

            self.assertEqual(before_cutoff, 100.5)
            self.assertEqual(after_cutoff, 300.5)
            self.assertEqual(old_remaining, 0)
        finally:
            conn.close()


class GetConnectionTransactionTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        import os
        self.db_fd, self.db_path = tempfile.mkstemp()
        # Initialize basic test table
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE test_table (val TEXT)")
        conn.commit()
        conn.close()

    def tearDown(self):
        import os
        os.close(self.db_fd)
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def test_get_connection_commits_on_success(self):
        with db.get_connection(self.db_path) as conn:
            conn.execute("INSERT INTO test_table (val) VALUES ('success')")
        
        # Verify persistence
        conn = sqlite3.connect(self.db_path)
        val = conn.execute("SELECT val FROM test_table").fetchone()[0]
        conn.close()
        self.assertEqual(val, 'success')

    def test_get_connection_rolls_back_on_exception(self):
        with self.assertRaises(ValueError):
            with db.get_connection(self.db_path) as conn:
                conn.execute("INSERT INTO test_table (val) VALUES ('failure')")
                raise ValueError("Forced failure")
        
        # Verify rollback
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT val FROM test_table").fetchone()
        conn.close()
        self.assertIsNone(row)


class IndicatorsSqlInjectionTests(unittest.TestCase):
    def test_save_indicators_ignores_malicious_columns(self):
        conn = sqlite3.connect(":memory:")
        try:
            db.setup_schema(conn)
            # Create a dataframe with a malicious column name
            df = pd.DataFrame([
                {
                    "symbol": "TEST",
                    "date": "2025-01-01",
                    "ma_5": 10.0,
                    "ma_5; DROP TABLE indicators; --": 20.0
                }
            ])
            # Save indicators: this should ignore the malicious column and not crash
            db.save_indicators(conn, df)
            
            # Verify the indicators table still exists (i.e. DROP TABLE was NOT executed)
            row = conn.execute("SELECT COUNT(*) FROM indicators").fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

    def test_load_indicators_ignores_malicious_columns(self):
        conn = sqlite3.connect(":memory:")
        try:
            db.setup_schema(conn)
            # Attempt to load indicators with a malicious indicator name
            df = db.load_indicators(
                conn, 
                "TEST", 
                indicator_names=["ma_5", "ma_5; SELECT * FROM indicators;"]
            )
            # The returned DataFrame columns should only contain 'date' and 'ma_5'
            self.assertEqual(list(df.columns), ["date", "ma_5"])
        finally:
            conn.close()


class MarkMissingSymbolsInactiveIdempotencyTests(unittest.TestCase):
    def test_mark_missing_symbols_inactive_is_idempotent(self):
        conn = sqlite3.connect(":memory:")
        try:
            db.setup_schema(conn)
            # Call once
            db.mark_missing_symbols_inactive(conn, ["SYM1", "SYM2"])
            # Call twice on same connection
            db.mark_missing_symbols_inactive(conn, ["SYM1"])
        finally:
            conn.close()

    def test_mark_missing_symbols_inactive_cleanup_on_exception(self):
        conn = sqlite3.connect(":memory:")
        try:
            db.setup_schema(conn)
            # Cause an error by passing invalid query execution data types to trigger exception
            with self.assertRaises(Exception):
                # Passing tuple elements with incorrect column count will raise an exception
                db.mark_missing_symbols_inactive(conn, [("too", "many", "columns")])
            
            # The next call should still succeed because the temp table was dropped in finally
            db.mark_missing_symbols_inactive(conn, ["SYM1"])
        finally:
            conn.close()


class SetupSchemaMigrationTests(unittest.TestCase):
    def test_setup_schema_migration_commits(self):
        conn = sqlite3.connect(":memory:")
        try:
            # Manually create the table in the old format
            conn.execute("""
                CREATE TABLE indicators (
                    symbol TEXT,
                    date TEXT,
                    indicator TEXT,
                    value REAL
                )
            """)
            conn.commit()

            # Run setup_schema which should trigger migration and commit it
            db.setup_schema(conn)

            # Verify indicators table has new wide columns and is queryable
            cols = [row[1] for row in conn.execute("PRAGMA table_info(indicators)").fetchall()]
            self.assertIn("ma_5", cols)
            self.assertNotIn("indicator", cols)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
