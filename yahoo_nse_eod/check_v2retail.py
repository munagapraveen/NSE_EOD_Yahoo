import sqlite3
conn = sqlite3.connect("data/yahoo_nse_eod.db")

print("=== SYMBOLS ===")
for r in conn.execute("SELECT symbol, yahoo_symbol, company_name, isin, active, status, instrument_type FROM symbols WHERE symbol = 'V2RETAIL'").fetchall():
    print(r)

print("\n=== ALIASES ===")
for r in conn.execute("SELECT * FROM symbol_aliases WHERE old_symbol = 'V2RETAIL' OR new_symbol = 'V2RETAIL'").fetchall():
    print(r)

print("\n=== CORPORATE ACTIONS ===")
for r in conn.execute("SELECT symbol, ex_date, action_type, value, source, note FROM corporate_actions WHERE symbol = 'V2RETAIL' ORDER BY ex_date").fetchall():
    print(r)

print("\n=== RAW EOD SUMMARY ===")
for r in conn.execute("SELECT count(1), min(date), max(date), min(close), max(close), min(adj_close), max(adj_close) FROM raw_eod_prices WHERE symbol = 'V2RETAIL'").fetchall():
    print(f"  rows={r[0]}, dates={r[1]} to {r[2]}")
    print(f"  close:     {r[3]:.2f} to {r[4]:.2f}")
    print(f"  adj_close: {r[5]:.2f} to {r[6]:.2f}")

print("\n=== ADJUSTED EOD SUMMARY ===")
for r in conn.execute("SELECT count(1), min(date), max(date), min(close), max(close), min(split_factor), max(split_factor) FROM adjusted_eod_prices WHERE symbol = 'V2RETAIL'").fetchall():
    print(f"  rows={r[0]}, dates={r[1]} to {r[2]}")
    print(f"  adj_close:    {r[3]:.2f} to {r[4]:.2f}")
    print(f"  split_factor: {r[5]} to {r[6]}")

print("\n=== MARKETCAP SUMMARY ===")
for r in conn.execute("SELECT count(1), min(date), max(date), min(market_cap_cr), max(market_cap_cr), min(shares_outstanding), max(shares_outstanding) FROM marketcap WHERE symbol = 'V2RETAIL'").fetchall():
    print(f"  rows={r[0]}, dates={r[1]} to {r[2]}")
    print(f"  mcap:   {r[3]:.2f} to {r[4]:.2f} Cr")
    print(f"  shares: {r[5]:,.0f} to {r[6]:,.0f}")

print("\n=== SHARE HISTORY SUMMARY ===")
for r in conn.execute("SELECT count(1), min(date), max(date), min(shares_outstanding), max(shares_outstanding) FROM share_history WHERE symbol = 'V2RETAIL'").fetchall():
    print(f"  rows={r[0]}, dates={r[1]} to {r[2]}")
    print(f"  shares: {r[3]:,.0f} to {r[4]:,.0f}")

print("\n=== RAW SPLIT EVENTS (stock_splits != 0) ===")
for r in conn.execute("SELECT date, close, adj_close, stock_splits, dividends FROM raw_eod_prices WHERE symbol = 'V2RETAIL' AND stock_splits != 0 ORDER BY date").fetchall():
    print(f"  {r[0]}  close={r[1]}  adj={r[2]}  splits={r[3]}  div={r[4]}")

print("\n=== ALL CORPORATE ACTIONS (any type near V2RETAIL) ===")
for r in conn.execute("SELECT symbol, ex_date, action_type, value, source, note FROM corporate_actions WHERE symbol LIKE '%V2%' OR symbol LIKE '%RETAIL%' ORDER BY ex_date").fetchall():
    print(r)

conn.close()
print("\nDone.")
