import unittest
from unittest import mock
import sys
import query_prices

class QueryPricesErrorHandlingTests(unittest.TestCase):
    def test_invalid_limit_raises_system_exit(self):
        with mock.patch("sys.exit") as mock_exit:
            with mock.patch("sys.stdout") as mock_stdout:
                query_prices.parse_args(["--limit", "invalid_val"])
                # sys.exit(1) should have been called
                mock_exit.assert_called_once_with(1)

    def test_database_exception_handled_in_main(self):
        with mock.patch("query_prices.get_connection") as mock_conn:
            mock_conn.side_effect = RuntimeError("Mock DB Error")
            with mock.patch("sys.exit") as mock_exit:
                query_prices.main()
                mock_exit.assert_called_once_with(1)

    def test_latest_combined_with_date_filters_raises_system_exit(self):
        with mock.patch("sys.exit") as mock_exit:
            query_prices.parse_args(["--latest", "--from", "2025-01-01"])
            mock_exit.assert_called_once_with(1)

if __name__ == "__main__":
    unittest.main()
