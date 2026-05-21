"""
logger.py — Centralised logging setup
======================================
Import get_logger() in every script instead of calling
logging.basicConfig() individually. Ensures consistent
format, level, and handlers across all scripts.
"""

import logging
import os
from pathlib import Path

_configured = False   # guard — only configure once per process


def get_logger(name: str, log_file: str = "zerodha.log") -> logging.Logger:
    """
    Returns a logger with file + console handlers.
    Safe to call multiple times — only configures once.

    Args:
        name     : usually __name__ from the calling module
        log_file : filename for the rotating log (default: zerodha.log)
    """
    global _configured

    if not _configured:
        log_dir  = Path(__file__).parent
        log_path = log_dir / log_file

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
            datefmt="%H:%M:%S",
            handlers=[
                logging.FileHandler(log_path, encoding="utf-8"),
                logging.StreamHandler(),
            ],
        )
        _configured = True

    return logging.getLogger(name)
