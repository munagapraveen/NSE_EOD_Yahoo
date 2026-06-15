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
        if "split" in subject or "sub-division" in subject or "subdivision" in subject:
            # Try multiple patterns in order of specificity:
            # Pattern A: "from Rs 10/- to Re 1/-"  or  "from Rs.10 to Re.1"
            match = re.search(r"from\s+r[se]\.?\s*([\d.]+).*?to\s+r[se]\.?\s*([\d.]+)", subject)
            if not match:
                # Pattern B: "from fv 10 to fv 1"  or  "fv rs 10 to fv re 1"
                match = re.search(r"fv\s+(?:r[se]\.?\s*)?([\d.]+).*?to\s+(?:fv\s+)?(?:r[se]\.?\s*)?([\d.]+)", subject)
            if not match:
                # Pattern C: generic "10 to 1" — only if digits are clearly face values (integer-like)
                match = re.search(r"\b(\d+)\s*(?:/-\s*)?to\s+(\d+)\s*(?:/-)?", subject)

            if match:
                old_fv = float(match.group(1))
                new_fv = float(match.group(2))
                if new_fv > 0 and old_fv != new_fv:
                    split_value = round(old_fv / new_fv, 4)
                    if split_value < 1.0:
                        # Reverse split (consolidation) — old_fv < new_fv
                        log.warning(
                            f"Reverse split detected for {symbol} on {ex_date}: "
                            f"FV {old_fv} to {new_fv} (factor={split_value:.4f}). "
                            f"Subject: {orig_subject}"
                        )
                    try:
                        dt = datetime.strptime(ex_date, "%d-%b-%Y").strftime("%Y-%m-%d")
                        rows.append({
                            "symbol": symbol,
                            "ex_date": dt,
                            "action_type": "split",
                            "value": split_value,
                            "source": "nse",
                            "note": orig_subject
                        })
                    except Exception:
                        log.warning(f"Could not parse ex_date '{ex_date}' for {symbol} split: {orig_subject}")
            else:
                log.warning(f"Split subject not parsed for {symbol} on {ex_date}: {orig_subject}")

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
        # Use more flexible matching: allow for extra spaces or minor variations
        pattern = label_pattern.replace(" ", r"\s*")
        if re.search(pattern, cleaned, flags=re.I):
            return urljoin(NSE_SYMBOL_PAGE_URL, href)
    raise ValueError(f"Could not find NSE CSV link for pattern: {label_pattern}")


def _fetch_csv_from_page(page_url, label_pattern, max_retries=3, **kwargs):
    session = create_session()
    last_err = None
    
    for attempt in range(max_retries):
        try:
            page = session.get(page_url, timeout=30)
            page.raise_for_status()
            csv_url = _resolve_csv_link(page.text, label_pattern)
            log.info(f"Resolved NSE CSV: {csv_url}")
            data = session.get(csv_url, timeout=30)
            data.raise_for_status()
            return pd.read_csv(StringIO(data.text), **kwargs)
        except Exception as e:
            last_err = e
            log.warning(f"Attempt {attempt+1}/{max_retries} failed for {label_pattern}: {e}")
            if attempt < max_retries - 1:
                import time
                time.sleep(2)
    
    if last_err is not None:
        raise last_err
    raise RuntimeError("No retry attempts made")


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
        r"securities available for trading in etf",
    )
    cols = {str(c).strip().upper(): c for c in df.columns}
    rename = {}
    for src, target in [
        ("SYMBOL", "symbol"),
        ("SYMBOL ", "symbol"),  # Handle trailing space if any
        ("COMPANY NAME", "company_name"),
        ("NAME OF COMPANY", "company_name"),
        ("ISIN", "isin"),
        ("ISIN NUMBER", "isin"),
    ]:
        if src in cols:
            rename[cols[src]] = target
    df = df.rename(columns=rename)
    df["series"] = "EQ"  # ETFs are traded like EQ
    required = ["symbol", "company_name", "isin", "series"]
    
    # Fill missing with empty string instead of dropping
    for col in required:
        if col not in df.columns:
            df[col] = ""
            
    df = df[required].copy()
    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    df["company_name"] = df["company_name"].astype(str).str.strip()
    df["isin"] = df["isin"].fillna("").astype(str).str.strip().str.upper()
    return df


def fetch_indices_master():
    """Fetch all indices from the NSE API."""
    log.info("Fetching indices list from NSE API...")
    session = create_session()
    # Visit home page first to get cookies
    session.get("https://www.nseindia.com/", timeout=15)
    res = session.get("https://www.nseindia.com/api/allIndices", timeout=15)
    res.raise_for_status()
    data = res.json().get("data", [])
    
    # Extract 'indexSymbol' as the primary identifier
    records = []
    for item in data:
        symbol = item.get("indexSymbol")
        if symbol:
            records.append({"symbol": symbol})
            
    return pd.DataFrame(records)


def fetch_symbol_changes():
    """Fetch CSV of symbol changes (renames) from NSE."""
    df = _fetch_csv_from_page(
        NSE_SYMBOL_PAGE_URL,
        r"changes in symbols",
        header=None,
    )
    
    if df.empty:
        return df
        
    # Check if the first row contains column headers
    first_row = df.iloc[0].astype(str).str.upper().str.strip().tolist()
    has_header = any(
        re.search(r"OLD.SYMBOL", col, re.I) or "OLD SYMBOL" in col
        for col in first_row
    )
    
    if has_header:
        # Set first row as column header and drop it
        df.columns = df.iloc[0]
        df = df[1:].reset_index(drop=True)
        
        # Map NSE columns to internal names
        rename_map = {
            "OLD SYMBOL": "old_symbol",
            "NEW SYMBOL": "new_symbol",
            "APPLICABLE FROM": "effective_date"
        }
        cols = {str(c).strip().upper(): c for c in df.columns}
        final_rename = {cols[src]: target for src, target in rename_map.items() if src in cols}
        df = df.rename(columns=final_rename)
    else:
        # Standard NSE order: Company Name, Old, New, Date
        if len(df.columns) >= 4:
            df.columns = ["company_name", "old_symbol", "new_symbol", "effective_date"] + list(df.columns[4:])
    
    # Keep only necessary columns
    required = ["old_symbol", "new_symbol", "effective_date"]
    df = df[[c for c in required if c in df.columns]].copy()
    
    # Parse dates: 28-NOV-2016 -> 2016-11-28
    def parse_dt(val):
        try:
            return datetime.strptime(str(val).strip(), "%d-%b-%Y").strftime("%Y-%m-%d")
        except Exception:
            return None

    df["effective_date"] = df["effective_date"].apply(parse_dt)
    df["old_symbol"] = df["old_symbol"].astype(str).str.strip().str.upper()
    df["new_symbol"] = df["new_symbol"].astype(str).str.strip().str.upper()
    
    return df
