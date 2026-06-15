import unittest
from unittest import mock
import sys
import sharpe_screener

class SharpeScreenerArgParserTests(unittest.TestCase):
    def test_arg_parser_parses_valid_arguments(self):
        with mock.patch("sharpe_screener.get_connection") as mock_conn:
            with mock.patch("sys.argv", ["sharpe_screener.py", "--date", "invalid-date"]):
                # Running main with invalid date format should print error and return
                sharpe_screener.main()
                
            # Running with valid arguments should trigger DB connect
            mock_conn.side_effect = RuntimeError("Success reaching DB connect")
            with mock.patch("sys.argv", ["sharpe_screener.py", "--mcap", "500", "--top", "20", "--date", "2025-01-01"]):
                with self.assertRaises(RuntimeError):
                    sharpe_screener.main()
if __name__ == "__main__":
    unittest.main()
