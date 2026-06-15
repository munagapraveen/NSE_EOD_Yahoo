import unittest
from unittest import mock
import sys
import pandas as pd
import sqlite3

import symbol_change_handler
from db import setup_schema

class TestSymbolChangeHandler(unittest.TestCase):
    @mock.patch("symbol_change_handler.fetch_securities_master")
    @mock.patch("symbol_change_handler.fetch_symbol_changes")
    @mock.patch("symbol_change_handler.get_connection")
    @mock.patch("symbol_change_handler.apply_symbol_rename")
    def test_apply_flags_separation(self, mock_rename, mock_get_conn, mock_changes, mock_master):
        # Setup mock database
        conn = sqlite3.connect(":memory:")
        setup_schema(conn)
        # Populate active symbols
        conn.execute("INSERT INTO symbols (symbol, yahoo_symbol, isin, company_name, series, active, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     ("AAA", "AAA.NS", "INE123A01011", "Company A", "EQ", 1, "active"))
        
        mock_get_conn.return_value.__enter__.return_value = conn
        
        # Securities master: AAA changed ISIN or is rebranded to BBB
        mock_master.return_value = pd.DataFrame([
            {"symbol": "BBB", "company_name": "Company A", "isin": "INE123A01011", "series": "EQ"}
        ])
        
        # Direct renames from CSV file: CCC -> DDD
        mock_changes.return_value = pd.DataFrame([
            {"old_symbol": "CCC", "new_symbol": "DDD", "effective_date": "2025-01-01"}
        ])
        
        # Case 1: Run with only --apply (should only apply direct rename CCC -> DDD)
        with mock.patch.object(sys, "argv", ["symbol_change_handler.py", "--apply"]):
            symbol_change_handler.main()
            
        # Verify apply_symbol_rename calls
        called_pairs = [(call[0][1], call[0][2]) for call in mock_rename.call_args_list]
        self.assertIn(("CCC", "DDD"), called_pairs)
        self.assertNotIn(("AAA", "BBB"), called_pairs)
        
        # Clear mock calls
        mock_rename.reset_mock()
        
        # Case 2: Run with only --apply-isin (should only apply ISIN rename AAA -> BBB)
        with mock.patch.object(sys, "argv", ["symbol_change_handler.py", "--apply-isin"]):
            symbol_change_handler.main()
            
        called_pairs = [(call[0][1], call[0][2]) for call in mock_rename.call_args_list]
        self.assertIn(("AAA", "BBB"), called_pairs)
        self.assertNotIn(("CCC", "DDD"), called_pairs)

        # Clear mock calls
        mock_rename.reset_mock()

        # Case 3: Run with both (should apply both)
        with mock.patch.object(sys, "argv", ["symbol_change_handler.py", "--apply", "--apply-isin"]):
            symbol_change_handler.main()
            
        called_pairs = [(call[0][1], call[0][2]) for call in mock_rename.call_args_list]
        self.assertIn(("CCC", "DDD"), called_pairs)
        self.assertIn(("AAA", "BBB"), called_pairs)

if __name__ == "__main__":
    unittest.main()
