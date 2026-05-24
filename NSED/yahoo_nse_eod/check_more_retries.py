
import yfinance as yf
import pandas as pd

symbols = [
    'AARNAV.NS', '3BBLACKBIO.NS', '3PLAND.NS', 'ABMKNO.NS', 'AKCAPIT.NS',
    'ABANSENT.NS', 'ACSTECH.NS', 'AMBALALSA.NS', 'ASHIKA.NS', 'AMIRCHAND.NS',
    'ARTEMISMED.NS', 'ASTAR.NS', 'AVAILFC.NS', 'BEEKAY.NS', 'BAJAJST.NS'
]

print(f"{'Symbol':<15} | {'Status':<12} | {'Rows'} | {'Latest Date'} | {'Last Close'}")
print("-" * 65)

for s in symbols:
    try:
        ticker = yf.Ticker(s)
        # Fetch small history to check availability
        hist = ticker.history(period='5d')
        if hist.empty:
            print(f"{s:<15} | EMPTY        | 0    | N/A        | N/A")
        else:
            last_date = hist.index[-1].strftime('%Y-%m-%d')
            last_close = hist['Close'].iloc[-1]
            print(f"{s:<15} | SUCCESS      | {len(hist):<4} | {last_date} | {last_close:>10.2f}")
    except Exception as e:
        err_msg = str(e)[:20]
        print(f"{s:<15} | ERROR        | 0    | {err_msg:<10} | N/A")
