
import sqlite3
import pandas as pd
from config import DB_FILE

def investigate_kanchi_data():
    conn = sqlite3.connect(DB_FILE)
    
    print("--- Earliest 10 Raw Prices for KANCHI ---")
    df_raw = pd.read_sql("SELECT * FROM raw_eod_prices WHERE symbol = 'KANCHI' ORDER BY date ASC LIMIT 10", conn)
    print(df_raw)
    
    print("\n--- Latest 5 Raw Prices for KANCHI ---")
    df_raw = pd.read_sql("SELECT * FROM raw_eod_prices WHERE symbol = 'KANCHI' ORDER BY date DESC LIMIT 5", conn)
    print(df_raw)
    
    print("\n--- Latest 5 Adjusted Prices for KANCHI ---")
    df_adj = pd.read_sql("SELECT * FROM adjusted_eod_prices WHERE symbol = 'KANCHI' ORDER BY date DESC LIMIT 5", conn)
    print(df_adj)
    
    print("\n--- Corporate Actions for KANCHI ---")
    df_actions = pd.read_sql("SELECT * FROM corporate_actions WHERE symbol = 'KANCHI'", conn)
    print(df_actions)
    
    conn.close()

if __name__ == "__main__":
    investigate_kanchi_data()
