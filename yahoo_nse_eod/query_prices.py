"""Query adjusted prices, market cap, and moving averages from the standalone DB."""

import sys
from pathlib import Path

import pandas as pd

from db import get_connection, setup_schema


DEFAULT_COLUMNS = [
    "symbol",
    "date",
    "close",
    "market_cap_cr",
    "shares_outstanding",
    "ma_5",
    "ma_10",
    "ma_20",
    "ma_50",
    "ma_100",
    "ma_200",
]


def parse_args(args):
    options = {
        "symbol": None,
        "from_date": None,
        "to_date": None,
        "limit": 50,
        "csv": None,
        "excel": None,
        "columns": DEFAULT_COLUMNS,
        "latest_only": False,
    }

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--symbol" and i + 1 < len(args):
            options["symbol"] = args[i + 1].strip().upper()
            i += 2
            continue
        if arg == "--from" and i + 1 < len(args):
            options["from_date"] = args[i + 1].strip()
            i += 2
            continue
        if arg == "--to" and i + 1 < len(args):
            options["to_date"] = args[i + 1].strip()
            i += 2
            continue
        if arg == "--limit" and i + 1 < len(args):
            try:
                options["limit"] = max(1, int(args[i + 1]))
            except ValueError:
                print(f"Error: --limit requires an integer, got '{args[i+1]}'", flush=True)
                sys.exit(1)
                return
            i += 2
            continue
        if arg == "--csv" and i + 1 < len(args):
            options["csv"] = args[i + 1].strip()
            i += 2
            continue
        if arg == "--excel" and i + 1 < len(args):
            options["excel"] = args[i + 1].strip()
            i += 2
            continue
        if arg == "--columns" and i + 1 < len(args):
            options["columns"] = [
                col.strip() for col in args[i + 1].split(",") if col.strip()
            ]
            i += 2
            continue
        if arg == "--latest":
            options["latest_only"] = True
            i += 1
            continue
        i += 1

    if options["latest_only"] and (options["from_date"] or options["to_date"]):
        print("Error: --latest cannot be combined with date filters (--from / --to)", flush=True)
        sys.exit(1)

    return options


def build_query(options):
    if options["latest_only"]:
        query = """
            SELECT a.*, i.ma_5, i.ma_10, i.ma_20, i.ma_50, i.ma_100, i.ma_200,
                   m.market_cap_cr, m.shares_outstanding
            FROM adjusted_eod_prices a
            LEFT JOIN indicators i
                ON a.symbol = i.symbol AND a.date = i.date
            LEFT JOIN marketcap m
                ON a.symbol = m.symbol AND a.date = m.date
            INNER JOIN (
                SELECT symbol, MAX(date) AS max_date
                FROM adjusted_eod_prices
                GROUP BY symbol
            ) latest
                ON a.symbol = latest.symbol
               AND a.date = latest.max_date
        """
        conditions = []
        params = []
        table_alias = "a"
    else:
        query = """
            SELECT a.*, i.ma_5, i.ma_10, i.ma_20, i.ma_50, i.ma_100, i.ma_200,
                   m.market_cap_cr, m.shares_outstanding
            FROM adjusted_eod_prices a
            LEFT JOIN indicators i
                ON a.symbol = i.symbol AND a.date = i.date
            LEFT JOIN marketcap m
                ON a.symbol = m.symbol AND a.date = m.date
        """
        conditions = []
        params = []
        table_alias = "a"

    def qualify(column_name):
        return f"{table_alias}.{column_name}"

    if options["symbol"]:
        conditions.append(f"{qualify('symbol')} = ?")
        params.append(options["symbol"])
    if options["from_date"]:
        conditions.append(f"{qualify('date')} >= ?")
        params.append(options["from_date"])
    if options["to_date"]:
        conditions.append(f"{qualify('date')} <= ?")
        params.append(options["to_date"])

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += f" ORDER BY {qualify('symbol')}, {qualify('date')} DESC"
    query += f" LIMIT {int(options['limit'])}"
    return query, params


def main():
    options = parse_args(sys.argv[1:])
    query, params = build_query(options)

    try:
        with get_connection() as conn:
            setup_schema(conn)
            df = pd.read_sql(query, conn, params=params)
    except Exception as e:
        print(f"Error: {e}", flush=True)
        sys.exit(1)
        return

    if df.empty:
        print("No rows found.")
        return

    cols = [col for col in options["columns"] if col in df.columns]
    if cols:
        df = df[cols].copy()

    if options["csv"]:
        out_path = Path(options["csv"]).expanduser()
        df.to_csv(out_path, index=False)
        print(f"Saved {len(df):,} rows to {out_path}")
        return

    if options["excel"]:
        out_path = Path(options["excel"]).expanduser()
        df.to_excel(out_path, index=False)
        print(f"Saved {len(df):,} rows to {out_path}")
        return

    with pd.option_context("display.max_columns", None, "display.width", 220):
        print(df.to_string(index=False))


if __name__ == "__main__":
    main()
