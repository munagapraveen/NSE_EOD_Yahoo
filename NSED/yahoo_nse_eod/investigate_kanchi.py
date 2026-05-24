
import sqlite3
import pandas as pd
from config import DB_FILE

def check_symbol(symbol):
    conn = sqlite3.connect(DB_FILE)
    print(f"--- Symbol Table for {symbol} ---")
    df_sym = pd.read_sql(f"SELECT * FROM symbols WHERE symbol = '{symbol}'", conn)
    print(df_sym)
    
    print(f"\n--- Raw Price Stats for {symbol} ---")
    raw_stats = conn.execute(f"SELECT MIN(date), MAX(date), COUNT(*) FROM raw_eod_prices WHERE symbol = '{symbol}'").fetchone()
    print(f"Min: {raw_stats[0]}, Max: {raw_stats[1]}, Count: {raw_stats[2]}")
    
    print(f"\n--- Adjusted Price Stats for {symbol} ---")
    adj_stats = conn.execute(f"SELECT MIN(date), MAX(date), COUNT(*) FROM adjusted_eod_prices WHERE symbol = '{symbol}'").fetchone()
    print(f"Min: {adj_stats[0]}, Max: {adj_stats[1]}, Count: {adj_stats[2]}")
    
    print(f"\n--- Aliases for {symbol} ---")
    df_alias = pd.read_sql(f"SELECT * FROM symbol_aliases WHERE old_symbol = '{symbol}' OR new_symbol = '{symbol}'", conn)
    print(df_alias)
    
    conn.close()

if __name__ == "__main__":
    check_symbol("KANCHI")
