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

# Major Indices Mapping (NSE Symbol -> Yahoo Ticker)
INDEX_MAP = {
    # Broad Market Indices
    "NIFTY 50": "^NSEI",
    "NIFTY NEXT 50": "^NIFTYJR",
    "NIFTY 100": "^CNX100",
    "NIFTY 200": "^CNX200",
    "NIFTY 500": "^CNX500",
    "NIFTY MIDCAP 50": "^NSEMDCP50",
    "NIFTY MIDCAP 100": "^NSMIDCP",
    "NIFTY SMALLCAP 100": "^CNXSC",
    "NIFTY TOTAL MARKET": "NIFTY_TOTAL_MKT.NS",
    "NIFTY MICROCAP 250": "NIFTY_MICROCAP250.NS",
    "NIFTY LARGEMIDCAP 250": "NIFTY_LARGEMID250.NS",
    "NIFTY MIDSMALLCAP 400": "NIFTYMIDSML400.NS",
    "NIFTY SMALLCAP 50": "NIFTYSMLCAP50.NS",
    "NIFTY SMALLCAP 250": "NIFTYSMLCAP250.NS",
    "NIFTY MIDCAP 150": "NIFTYMIDCAP150.NS",

    # Sectoral Indices
    "NIFTY BANK": "^NSEBANK",
    "NIFTY AUTO": "^CNXAUTO",
    "NIFTY FINANCIAL SERVICES": "^CNXFIN",
    "NIFTY FIN SERVICE": "^CNXFIN",  # Alias for NSE API consistency
    "NIFTY FMCG": "^CNXFMCG",
    "NIFTY IT": "^CNXIT",
    "NIFTY MEDIA": "^CNXMEDIA",
    "NIFTY METAL": "^CNXMETAL",
    "NIFTY PHARMA": "^CNXPHARMA",
    "NIFTY PSU BANK": "^CNXPSUBANK",
    "NIFTY REALTY": "^CNXREALTY",
    "NIFTY PRIVATE BANK": "NIFTY_PVT_BANK.NS",
    "NIFTY PVT BANK": "NIFTY_PVT_BANK.NS",  # Alias for NSE API consistency
    "NIFTY HEALTHCARE": "NIFTY_HEALTHCARE.NS",
    "NIFTY CONSUMER DURABLES": "NIFTY_CONSR_DURBL.NS",
    "NIFTY OIL & GAS": "NIFTY_OIL_AND_GAS.NS",
    
    # Thematic & Strategy
    "NIFTY COMMODITIES": "^CNXCMDT",
    "NIFTY CONSUMPTION": "^CNXCONSUM",
    "NIFTY CPSE": "^CNXCPSE",
    "NIFTY ENERGY": "^CNXENERGY",
    "NIFTY INFRA": "^CNXINFRA",
    "NIFTY INFRASTRUCTURE": "^CNXINFRA",  # Alias
    "NIFTY PSE": "^CNXPSE",
    "NIFTY SERVICES SECTOR": "^CNXSERVICE",
    "NIFTY DIVIDEND OPPORTUNITIES 50": "^CNXDIVOPP",
    "INDIA VIX": "^INDIAVIX",
    "NIFTY MIDCAP SELECT": "NIFTY_MID_SELECT.NS",
}

DEFAULT_BATCH_SIZE = 75
DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_HISTORY_START = "2024-01-01"

NSE_REPORTS_URL = "https://www.nseindia.com/static/market-data/securities-available-for-trading"
NSE_SYMBOL_PAGE_URL = "https://www.nseindia.com/static/market-data/securities-available-for-trading"
NSE_CORP_ACTIONS_URL = "https://www.nseindia.com/api/corporates-corporateactions?index=equities"

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
