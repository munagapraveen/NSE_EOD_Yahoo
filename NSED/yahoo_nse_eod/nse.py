"""NSE helpers for symbol master and rename data."""

from io import StringIO
import re
from urllib.parse import urljoin

import pandas as pd
import requests

from config import HTTP_HEADERS, NSE_SYMBOL_PAGE_URL
from logger import get_logger

log = get_logger(__name__)


def create_session():
    session = requests.Session()
    session.headers.update(HTTP_HEADERS)
    return session


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
    df = df[df["symbol"] != ""].drop_duplicates(subset=["symbol"])
    return df


def fetch_symbol_changes():
    try:
        df = _fetch_csv_from_page(
            NSE_SYMBOL_PAGE_URL,
            r"changes in symbols",
        )
    except Exception as exc:
        log.warning(f"Could not fetch NSE symbol changes file: {exc}")
        return pd.DataFrame(columns=["old_symbol", "new_symbol", "effective_date"])

    upper = {str(c).strip().upper(): c for c in df.columns}
    rename = {}
    candidates = [
        ("OLD SYMBOL", "old_symbol"),
        ("NEW SYMBOL", "new_symbol"),
        ("EFFECTIVE DATE", "effective_date"),
    ]
    for src, target in candidates:
        if src in upper:
            rename[upper[src]] = target
    df = df.rename(columns=rename)
    needed = ["old_symbol", "new_symbol"]
    if any(col not in df.columns for col in needed):
        log.warning("NSE symbol changes CSV format changed; skipping direct rename file.")
        return pd.DataFrame(columns=["old_symbol", "new_symbol", "effective_date"])

    if "effective_date" not in df.columns:
        df["effective_date"] = None

    for col in ["old_symbol", "new_symbol"]:
        df[col] = df[col].fillna("").astype(str).str.strip().str.upper()
    df["effective_date"] = df["effective_date"].fillna("").astype(str).str.strip()
    df = df[(df["old_symbol"] != "") & (df["new_symbol"] != "")]
    return df[["old_symbol", "new_symbol", "effective_date"]].drop_duplicates()
