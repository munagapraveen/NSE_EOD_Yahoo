
import sqlite3
import pandas as pd
from config import DB_FILE

def check_db_state(symbols):
    conn = sqlite3.connect(DB_FILE)
    
    print(f"{'Symbol':<12} | {'Max Price Date':<15} | {'Share Hist Count'} | {'Max Share Date'}")
    print("-" * 65)
    
    for s in symbols:
        price_data = conn.execute(f"SELECT MAX(date) FROM raw_eod_prices WHERE symbol = '{s}'").fetchone()[0]
        share_count = conn.execute(f"SELECT COUNT(*) FROM share_history WHERE symbol = '{s}'").fetchone()[0]
        share_max_date = conn.execute(f"SELECT MAX(date) FROM share_history WHERE symbol = '{s}'").fetchone()[0]
        
        print(f"{s:<12} | {str(price_data):<15} | {share_count:<16} | {str(share_max_date)}")
        
    conn.close()

if __name__ == "__main__":
    check_db_state(['KANCHI', 'KPL', 'ACSTECH', 'RELIANCE'])
