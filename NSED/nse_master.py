"""
nse_master.py -- NSE master sync for equities and ETFs
======================================================
Fetches the NSE equity-segment and ETF master CSV files, stores them in DB,
and reports what changed vs the previously stored master version.
"""

from datetime import datetime
from io import StringIO
import re
from urllib.parse import urljoin

import pandas as pd
import requests

from db import (
    get_connection,
    load_symbols_master,
    mark_missing_master_symbols_inactive,
    setup_schema,
    upsert_symbols_master,
)
from logger import get_logger

log = get_logger(__name__)

NSE_PAGE_URL = "https://www.nseindia.com/market-data/securities-available-for-trading"
NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


def create_session():
    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    return session


def _resolve_csv_link(html, patterns):
    matches = re.findall(r'href="([^"]+)"[^>]*>(.*?)</a>', html, flags=re.I | re.S)
    for href, text in matches:
        href_lower = href.strip().lower()
        if ".csv" not in href_lower:
            continue
        cleaned = re.sub(r"\s+", " ", text).strip().lower()
        for pattern in patterns:
            if re.search(pattern, cleaned, flags=re.I):
                return urljoin(NSE_PAGE_URL, href)
    raise ValueError(f"Could not find NSE CSV link for patterns: {patterns}")


def _fetch_csv(label_patterns):
    session = create_session()
    page = session.get(NSE_PAGE_URL, timeout=30)
    page.raise_for_status()
    csv_url = _resolve_csv_link(page.text, label_patterns)
    log.info(f"Resolved NSE CSV: {csv_url}")
    resp = session.get(csv_url, timeout=30)
    resp.raise_for_status()
    return pd.read_csv(StringIO(resp.text))


def fetch_equity_master():
    df = _fetch_csv([
        r"equity segment",
        r"equity \(.csv\)",
    ])
    cols = {str(c).strip().upper(): c for c in df.columns}
    rename = {}
    for src, dst in [
        ("SYMBOL", "symbol"),
        ("NAME OF COMPANY", "company_name"),
        ("ISIN NUMBER", "isin"),
        ("SERIES", "series"),
    ]:
        if src in cols:
            rename[cols[src]] = dst
    df = df.rename(columns=rename)
    required = ["symbol", "company_name", "isin", "series"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Equity master missing columns: {missing}")
    df = df[required].copy()
    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    df["company_name"] = df["company_name"].fillna("").astype(str).str.strip()
    df["isin"] = df["isin"].fillna("").astype(str).str.strip().str.upper()
    df["series"] = df["series"].fillna("").astype(str).str.strip().str.upper()
    df = df[df["symbol"] != ""]
    df = df[df["series"].isin({"EQ", "BE"})].copy()
    df["category"] = "equity"
    df["source"] = "nse-equity-master"
    return df.drop_duplicates(subset=["symbol"])


def fetch_etf_master():
    df = _fetch_csv([
        r"trading in etf",
        r"\betf\b.*\.csv",
        r"securities available.*etf",
    ])
    cols = {str(c).strip().upper(): c for c in df.columns}
    rename = {}
    for src, dst in [
        ("SYMBOL", "symbol"),
        ("NAME OF COMPANY", "company_name"),
        ("ISIN NUMBER", "isin"),
        ("SERIES", "series"),
    ]:
        if src in cols:
            rename[cols[src]] = dst
    df = df.rename(columns=rename)
    missing = [col for col in ["symbol"] if col not in df.columns]
    if missing:
        raise ValueError(f"ETF master missing columns: {missing}")
    for col in ["company_name", "isin", "series"]:
        if col not in df.columns:
            df[col] = ""
    df = df[["symbol", "company_name", "isin", "series"]].copy()
    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    df["company_name"] = df["company_name"].fillna("").astype(str).str.strip()
    df["isin"] = df["isin"].fillna("").astype(str).str.strip().str.upper()
    df["series"] = df["series"].fillna("").astype(str).str.strip().str.upper()
    df = df[df["symbol"] != ""]
    df["category"] = "etf"
    df["source"] = "nse-etf-master"
    return df.drop_duplicates(subset=["symbol"])


def build_master_universe():
    equity = fetch_equity_master()
    etf = fetch_etf_master()
    combined = pd.concat([equity, etf], ignore_index=True)

    def first_non_blank(series):
        for value in series:
            text = str(value).strip()
            if text:
                return text
        return ""

    master = (
        combined.groupby("symbol", as_index=False)
        .agg({
            "company_name": first_non_blank,
            "isin": first_non_blank,
            "series": first_non_blank,
            "category": lambda s: "+".join(sorted({str(v).strip().lower() for v in s if str(v).strip()})),
            "source": lambda s: "+".join(sorted({str(v).strip() for v in s if str(v).strip()})),
        })
    )
    return master


def summarize_changes(old_df, new_df):
    old = old_df.copy()
    new = new_df.copy()
    if old.empty:
        return {
            "added": len(new),
            "removed": 0,
            "company_changed": 0,
            "isin_changed": 0,
            "category_changed": 0,
        }

    old_idx = old.set_index("symbol")
    new_idx = new.set_index("symbol")
    old_symbols = set(old_idx.index)
    new_symbols = set(new_idx.index)
    common = old_symbols & new_symbols

    company_changed = 0
    isin_changed = 0
    category_changed = 0
    for symbol in common:
        old_row = old_idx.loc[symbol]
        new_row = new_idx.loc[symbol]
        if str(old_row.get("company_name", "")).strip() != str(new_row.get("company_name", "")).strip():
            company_changed += 1
        if str(old_row.get("isin", "")).strip().upper() != str(new_row.get("isin", "")).strip().upper():
            isin_changed += 1
        if str(old_row.get("category", "")).strip().lower() != str(new_row.get("category", "")).strip().lower():
            category_changed += 1

    return {
        "added": len(new_symbols - old_symbols),
        "removed": len(old_symbols - new_symbols),
        "company_changed": company_changed,
        "isin_changed": isin_changed,
        "category_changed": category_changed,
    }


def log_change_preview(old_df, new_df):
    old_idx = old_df.set_index("symbol") if not old_df.empty else pd.DataFrame().set_index(pd.Index([]))
    new_idx = new_df.set_index("symbol")
    old_symbols = set(old_idx.index)
    new_symbols = set(new_idx.index)

    added = sorted(new_symbols - old_symbols)[:20]
    removed = sorted(old_symbols - new_symbols)[:20]
    if added:
        log.info("  Added sample   : " + ", ".join(added))
    if removed:
        log.info("  Removed sample : " + ", ".join(removed))


def run_master_sync():
    with get_connection() as conn:
        setup_schema(conn)
        old_master = load_symbols_master(conn)

    try:
        master = build_master_universe()
    except Exception as exc:
        if not old_master.empty:
            log.warning(f"NSE master fetch failed ({exc}). Falling back to cached master list.")
            cached_master = old_master[old_master["active"] == 1].copy()
            summary = {
                "added": 0,
                "removed": 0,
                "company_changed": 0,
                "isin_changed": 0,
                "category_changed": 0,
            }
            log.info("")
            log.info("=" * 55)
            log.info("NSE MASTER SYNC")
            log.info("=" * 55)
            log.info("Using cached symbols_master data")
            log.info(f"Universe size     : {len(cached_master):,}")
            log.info("=" * 55)
            return cached_master, summary
        raise

    today = datetime.today().strftime("%Y-%m-%d")
    records = []
    for row in master.itertuples(index=False):
        records.append({
            "symbol": row.symbol,
            "company_name": row.company_name,
            "isin": row.isin,
            "category": row.category,
            "series": row.series,
            "active": 1,
            "source": row.source,
            "last_synced_on": today,
        })

    with get_connection() as conn:
        summary = summarize_changes(old_master, master)
        upsert_symbols_master(conn, records)
        mark_missing_master_symbols_inactive(conn, master["symbol"].tolist())

    log.info("")
    log.info("=" * 55)
    log.info("NSE MASTER SYNC")
    log.info("=" * 55)
    log.info(f"Universe size     : {len(master):,}")
    category_series = master["category"].fillna("").astype(str).str.lower()
    log.info(f"Equity symbols    : {category_series.str.contains('equity').sum():,}")
    log.info(f"ETF symbols       : {category_series.str.contains('etf').sum():,}")
    log.info(f"Added             : {summary['added']:,}")
    log.info(f"Removed           : {summary['removed']:,}")
    log.info(f"Company changed   : {summary['company_changed']:,}")
    log.info(f"ISIN changed      : {summary['isin_changed']:,}")
    log.info(f"Category changed  : {summary['category_changed']:,}")
    log_change_preview(old_master, master)
    log.info("=" * 55)

    return master, summary


def main():
    run_master_sync()


if __name__ == "__main__":
    main()
