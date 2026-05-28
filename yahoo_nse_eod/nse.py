"""NSE helpers for symbol master and rename data."""

from io import StringIO
import re
from urllib.parse import urljoin

from datetime import datetime
import pandas as pd
import requests
from config import HTTP_HEADERS, NSE_SYMBOL_PAGE_URL, NSE_CORP_ACTIONS_URL
from logger import get_logger

log = get_logger(__name__)


def create_session():
    session = requests.Session()
    session.headers.update(HTTP_HEADERS)
    return session


def fetch_nse_corporate_actions(start_date="01-01-2024", end_date=None):
    """Fetch recent corporate actions from NSE and parse splits/bonuses."""
    log.info(f"Fetching corporate actions from NSE ({start_date} to {end_date or 'today'})...")
    session = create_session()
    
    # Target range
    from_dt = start_date
    to_dt = end_date or datetime.today().strftime("%d-%m-%Y")
    url = f"{NSE_CORP_ACTIONS_URL}&from_date={from_dt}&to_date={to_dt}"
    
    # NSE requires initial visit and specific referer
    session.headers.update({"Referer": "https://www.nseindia.com/market-data/corporate-actions"})
    
    try:
        session.get("https://www.nseindia.com/", timeout=15)
        res = session.get(url, timeout=15)
        res.raise_for_status()
        data = res.json()
        log.info(f"NSE API returned {len(data)} items.")
    except Exception as e:
        log.warning(f"Could not fetch NSE corporate actions: {e}")
        return pd.DataFrame()

    rows = []
    for item in data:
        symbol = item.get("symbol")
        ex_date = item.get("exDate")
        # Field is 'subject' in NSE API
        orig_subject = item.get("subject", "")
        subject = orig_subject.lower()
        
        if not symbol or not ex_date:
            continue
            
        # 1. Parse Splits
        if "split" in subject or "sub-division" in subject:
            # Handle "From Rs 10/- To Re 1/-" or similar
            match = re.search(r"from r[es]\s*(\d+).*?to r[es]\s*(\d+)", subject)
            if not match:
                # Fallback to simple digit-to-digit
                match = re.search(r"(\d+)\s*to\s*(\d+)", subject)
            
            if match:
                old_fv = float(match.group(1))
                new_fv = float(match.group(2))
                if new_fv > 0:
                    try:
                        dt = datetime.strptime(ex_date, "%d-%b-%Y").strftime("%Y-%m-%d")
                        rows.append({
                            "symbol": symbol,
                            "ex_date": dt,
                            "action_type": "split",
                            "value": round(old_fv / new_fv, 4),
                            "source": "nse",
                            "note": orig_subject
                        })
                    except Exception:
                        pass

        # 2. Parse Bonuses
        if "bonus" in subject:
            match = re.search(r"(\d+):(\d+)", subject)
            if match:
                bonus_qty = float(match.group(1))
                existing_qty = float(match.group(2))
                if existing_qty > 0:
                    try:
                        dt = datetime.strptime(ex_date, "%d-%b-%Y").strftime("%Y-%m-%d")
                        rows.append({
                            "symbol": symbol,
                            "ex_date": dt,
                            "action_type": "bonus",
                            "value": round((bonus_qty + existing_qty) / existing_qty, 4),
                            "source": "nse",
                            "note": orig_subject
                        })
                    except Exception:
                        pass
            
    return pd.DataFrame(rows)


def _resolve_csv_link(html, label_pattern):
    matches = re.findall(r'href="([^"]+)"[^>]*>(.*?)</a>', html, flags=re.I | re.S)
    for href, text in matches:
        cleaned = re.sub(r"\s+", " ", text).strip().lower()
        if re.search(label_pattern, cleaned, flags=re.I):
            return urljoin(NSE_SYMBOL_PAGE_URL, href)
    raise ValueError(f"Could not find NSE CSV link for pattern: {label_pattern}")


def _fetch_csv_from_page(page_url, label_pattern):
    session = create_session()
    page = session.get(page_url, timeout=30)
    page.raise_for_status()
    csv_url = _resolve_csv_link(page.text, label_pattern)
    log.info(f"Resolved NSE CSV: {csv_url}")
    data = session.get(csv_url, timeout=30)
    data.raise_for_status()
    return pd.read_csv(StringIO(data.text))


def fetch_securities_master():
    df = _fetch_csv_from_page(
        NSE_SYMBOL_PAGE_URL,
        r"securities available for equity segment",
    )
    cols = {str(c).strip().upper(): c for c in df.columns}
    rename = {}
    for src, target in [
        ("SYMBOL", "symbol"),
        ("NAME OF COMPANY", "company_name"),
        ("ISIN NUMBER", "isin"),
        ("SERIES", "series"),
    ]:
        if src in cols:
            rename[cols[src]] = target
    df = df.rename(columns=rename)
    required = ["symbol", "company_name", "isin", "series"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"NSE securities file missing columns: {missing}")
    df = df[required].copy()
    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    df["company_name"] = df["company_name"].astype(str).str.strip()
    df["isin"] = df["isin"].fillna("").astype(str).str.strip().str.upper()
    df["series"] = df["series"].fillna("").astype(str).str.strip().str.upper()
    
    return df


def fetch_etf_master():
    df = _fetch_csv_from_page(
        NSE_SYMBOL_PAGE_URL,
        r"list of etfs",
    )
    cols = {str(c).strip().upper(): c for c in df.columns}
    rename = {}
    for src, target in [
        ("SYMBOL", "symbol"),
        ("COMPANY NAME", "company_name"),
        ("ISIN", "isin"),
    ]:
        if src in cols:
            rename[cols[src]] = target
    df = df.rename(columns=rename)
    df["series"] = "EQ"  # ETFs are traded like EQ
    required = ["symbol", "company_name", "isin", "series"]
    df = df[[c for c in required if c in df.columns]].copy()
    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    return df
