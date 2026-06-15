import unittest
import pandas as pd
from yahoo_client import normalize_yahoo_history

class YahooClientNormalizationTests(unittest.TestCase):
    def test_normalize_yahoo_history_handles_missing_dividends_and_splits(self):
        # Create a dataframe missing "Dividends" and "Stock Splits" columns
        df = pd.DataFrame([
            {
                "Date": "2025-01-01",
                "Open": 100.0,
                "High": 105.0,
                "Low": 95.0,
                "Close": 102.0,
                "Volume": 1000,
            }
        ])
        
        # This should execute without throwing AttributeError
        res = normalize_yahoo_history(df, ["TEST.NS"])
        
        # Verify columns are created and set to 0.0
        self.assertIn("dividends", res.columns)
        self.assertIn("stock_splits", res.columns)
        self.assertEqual(res["dividends"].iloc[0], 0.0)
        self.assertEqual(res["stock_splits"].iloc[0], 0.0)

    def test_truncate_to_2dp_rounds_half_up(self):
        from yahoo_client import truncate_to_2dp
        self.assertEqual(truncate_to_2dp(1234.567), 1234.57)
        self.assertEqual(truncate_to_2dp(1234.562), 1234.56)
        self.assertEqual(truncate_to_2dp(1234.565), 1234.57)

if __name__ == "__main__":
    unittest.main()
