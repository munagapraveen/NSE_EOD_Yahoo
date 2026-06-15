import unittest
import sqlite3
import db
import adjust_splits

class TestAdjustSplitsRebuild(unittest.TestCase):
    def test_rebuild_symbols_with_shared_connection(self):
        # Create an in-memory db
        conn = sqlite3.connect(":memory:")
        db.setup_schema(conn)
        
        # Insert test symbols
        conn.execute("INSERT INTO symbols (symbol, yahoo_symbol, isin, company_name, series, active, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     ("TESTSYM", "TESTSYM.NS", "INE123A01011", "Test Company", "EQ", 1, "active"))
        conn.commit()
        
        # Verify we can run rebuild_symbols using the shared connection
        try:
            adjust_splits.rebuild_symbols(["TESTSYM"], preserve_market_cap=False, conn=conn)
        except Exception as e:
            self.fail(f"rebuild_symbols raised an exception: {e}")
        finally:
            conn.close()

if __name__ == "__main__":
    unittest.main()
