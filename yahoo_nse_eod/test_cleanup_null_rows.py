import unittest
import sys

class CleanupNullRowsImportTests(unittest.TestCase):
    def test_import_does_not_execute_main(self):
        from unittest.mock import patch
        import io

        # If cleanup_null_rows had already been imported somewhere,
        # we remove it from sys.modules to force a fresh import/execution check
        if "cleanup_null_rows" in sys.modules:
            del sys.modules["cleanup_null_rows"]

        f = io.StringIO()
        with patch('sys.stdout', new=f):
            import cleanup_null_rows
        
        # Verify stdout is empty during import (meaning main did not run)
        self.assertEqual(f.getvalue(), "")

if __name__ == "__main__":
    unittest.main()
