
import yfinance as yf
import pandas as pd

symbols = ['ADANIENSOL.NS', 'ABCOTS.NS', 'ADFFOODS.NS', 'ADROITINFO.NS', 'AFFORDABLE.NS']

print("--- Manual Ticker Check ---")
for s in symbols:
    try:
        ticker = yf.Ticker(s)
        hist = ticker.history(period='5d')
        if hist.empty:
            print(f"{s}: Returned EMPTY dataframe")
        else:
            print(f"{s}: Success - {len(hist)} rows found. Latest Close: {hist['Close'].iloc[-1]:.2f}")
    except Exception as e:
        print(f"{s}: Failed with Error - {e}")
