import unittest
from unittest import mock
import nse

class NseFetchTests(unittest.TestCase):
    def test_fetch_csv_from_page_with_zero_retries_raises_runtime_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            # Call fetch with 0 retries
            nse._fetch_csv_from_page("http://dummy", "pattern", max_retries=0)
        self.assertEqual(str(ctx.exception), "No retry attempts made")

    def test_fetch_symbol_changes_handles_malformed_dates(self):
        import pandas as pd
        mock_df = pd.DataFrame([
            ["OLD SYMBOL", "NEW SYMBOL", "APPLICABLE FROM"],
            ["AAA", "BBB", "invalid-date-format"],
            ["CCC", "DDD", "28-Nov-2016"]
        ])
        with mock.patch("nse._fetch_csv_from_page", return_value=mock_df):
            res = nse.fetch_symbol_changes()
            
        self.assertTrue(pd.isna(res.iloc[0]["effective_date"]))
        self.assertEqual(res.iloc[1]["effective_date"], "2016-11-28")

if __name__ == "__main__":
    unittest.main()
