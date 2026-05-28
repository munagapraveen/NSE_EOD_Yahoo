
import sqlite3
import pandas as pd
from config import DB_FILE

def check_acstech_mcap():
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql("SELECT * FROM marketcap WHERE symbol = 'ACSTECH' ORDER BY date DESC LIMIT 5", conn)
    print("--- Market Cap Table for ACSTECH ---")
    print(df)
    
    df_adj = pd.read_sql("SELECT symbol, date, close, shares_outstanding, market_cap_cr FROM adjusted_eod_prices WHERE symbol = 'ACSTECH' ORDER BY date DESC LIMIT 5", conn)
    print("\n--- Adjusted Prices (MCAP cols) for ACSTECH ---")
    print(df_adj)
    
    conn.close()

if __name__ == "__main__":
    check_acstech_mcap()
