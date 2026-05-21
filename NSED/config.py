# =============================================================================
# config.py — Central configuration for all Zerodha NSE scripts
# =============================================================================
# Edit ONLY this file to update your credentials and settings.
# All other scripts import from here automatically.
#
# HOW TO UPDATE ACCESS TOKEN DAILY:
#   1. Run: python zerodha_nse_eod_sqlite.py  (with GENERATE_TOKEN = True)
#   2. Follow the browser login steps
#   3. Copy the printed access token
#   4. Paste it into ACCESS_TOKEN below
#   5. Set GENERATE_TOKEN = False
#   6. Save this file and run any script normally
# =============================================================================

# ---------------------------------------------------------------------------
# Zerodha API credentials — permanent, never change
# ---------------------------------------------------------------------------
API_KEY    = "8inpa3iigq3n8ox8"
API_SECRET = "6pw13rfp2g8vtgv5jjbsp9kjkl2xubi0"

# ---------------------------------------------------------------------------
# Access token — refresh every trading day (expires at midnight)
# ---------------------------------------------------------------------------
ACCESS_TOKEN = "GY6ReiXXBf502oShXEL1cUvgB7JTf3h6"

# ---------------------------------------------------------------------------
# Token generation flag
# Set True once each morning to generate a new access token.
# Set back to False before running any download script.
# ---------------------------------------------------------------------------
GENERATE_TOKEN = False

# ---------------------------------------------------------------------------
# Database -- absolute path so all scripts find it regardless of where
# they are launched from (terminal, GUI, Task Scheduler, etc.)
# ---------------------------------------------------------------------------
import os as _os
DB_FILE = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "nse_eod.db")

# ---------------------------------------------------------------------------
# Kite API settings
# ---------------------------------------------------------------------------
CHUNK_DAYS    = 2000   # max days per API request for 'day' interval
REQUEST_DELAY = 0.4    # seconds between requests — stays under 3 req/s limit
YEARS_BACK    = 5      # how many years of history to download

# ---------------------------------------------------------------------------
# Instrument filter
# EQ = normal equity, BE = T2T/ASM stocks
# BE stocks are stored in the DB under their base symbol (no -BE suffix)
# ---------------------------------------------------------------------------
ALLOWED_TYPES = {"EQ", "BE"}

# Corporate actions — default lookback on first ever run
DEFAULT_DAYS_FIRST_RUN = 30
