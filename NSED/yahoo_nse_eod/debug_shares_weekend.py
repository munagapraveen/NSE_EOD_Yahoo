
import yfinance as yf
import pandas as pd
from datetime import datetime

symbols = ['KANCHI.NS', 'KPL.NS', 'ACSTECH.NS', 'RELIANCE.NS']
start_date = '2024-01-01'

print(f"Checking share history for symbols starting from {start_date}...")
print("-" * 50)

for s in symbols:
    try:
        ticker = yf.Ticker(s)
        # Check price history first to see if symbol is valid
        hist = ticker.history(period='1mo')
        if hist.empty:
            print(f"{s}: No price history found.")
            continue
            
        shares = ticker.get_shares_full(start=start_date)
        if shares is None or len(shares) == 0:
            print(f"{s}: get_shares_full() returned NO data.")
        else:
            print(f"{s}: Success! Found {len(shares)} share records.")
            print(f"Latest record: {shares.index[-1]} -> {shares.iloc[-1]:,}")
            
    except Exception as e:
        print(f"{s}: Error -> {e}")

print("-" * 50)
print(f"Current Date/Time: {datetime.now()}")
