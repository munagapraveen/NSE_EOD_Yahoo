"""Shared logging for the standalone Yahoo/NSE EOD project."""

import logging
from logging.handlers import RotatingFileHandler

from config import LOG_FILE, initialize_config

initialize_config()

_configured = False


def get_logger(name: str) -> logging.Logger:
    global _configured

    if not _configured:
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        formatter = logging.Formatter(
            fmt="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        
        file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.INFO)
        
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        stream_handler.setLevel(logging.INFO)
        
        # Prevent duplicating handlers
        if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
            root.addHandler(file_handler)
        if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler) for h in root.handlers):
            root.addHandler(stream_handler)
            
        _configured = True

    return logging.getLogger(name)
