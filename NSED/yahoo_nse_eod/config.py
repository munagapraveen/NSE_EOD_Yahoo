"""Configuration for the standalone Yahoo/NSE EOD project."""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"

DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

DB_FILE = DATA_DIR / "yahoo_nse_eod.db"
LOG_FILE = LOG_DIR / "yahoo_nse_eod.log"
FAILED_SHARES_FILE = DATA_DIR / "share_download_failures_latest.csv"
FAILED_EOD_FILE = DATA_DIR / "eod_download_failures_latest.csv"

YAHOO_SUFFIX = ".NS"
DEFAULT_BATCH_SIZE = 75
DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_HISTORY_START = "2024-01-01"

NSE_REPORTS_URL = "https://www.nseindia.com/static/market-data/securities-available-for-trading"
NSE_SYMBOL_PAGE_URL = "https://www.nseindia.com/static/market-data/securities-available-for-trading"

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}
