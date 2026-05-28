
import sqlite3
import pandas as pd
from config import DB_FILE

def query_adjusted_prices(symbol):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql(f"SELECT * FROM adjusted_eod_prices WHERE symbol = '{symbol}' ORDER BY date DESC LIMIT 20", conn)
    print(df)
    conn.close()

if __name__ == "__main__":
    query_adjusted_prices("ACSTECH")
