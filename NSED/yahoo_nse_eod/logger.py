"""Shared logging for the standalone Yahoo/NSE EOD project."""

import logging

from config import LOG_FILE

_configured = False


def get_logger(name: str) -> logging.Logger:
    global _configured

    if not _configured:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
            datefmt="%H:%M:%S",
            handlers=[
                logging.FileHandler(LOG_FILE, encoding="utf-8"),
                logging.StreamHandler(),
            ],
        )
        _configured = True

    return logging.getLogger(name)
