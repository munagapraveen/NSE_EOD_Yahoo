import logging
import unittest
from logging.handlers import RotatingFileHandler
import logger

class LoggerTests(unittest.TestCase):
    def test_logger_configured(self):
        # Save old config state and handlers
        root_logger = logging.getLogger()
        old_handlers = list(root_logger.handlers)
        old_configured = logger._configured
        
        try:
            # Reset
            logger._configured = False
            root_logger.handlers = []
            
            # Trigger config
            logger.get_logger("test_dummy")
            
            handlers = root_logger.handlers
            rotating_handlers = [h for h in handlers if isinstance(h, RotatingFileHandler)]
            self.assertTrue(len(rotating_handlers) >= 1)
            
            # Verify properties
            for h in rotating_handlers:
                self.assertEqual(h.maxBytes, 5 * 1024 * 1024)
                self.assertEqual(h.backupCount, 3)
        finally:
            # Restore
            root_logger.handlers = old_handlers
            logger._configured = old_configured

if __name__ == "__main__":
    unittest.main()
