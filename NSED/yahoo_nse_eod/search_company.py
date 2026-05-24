
import sqlite3
import pandas as pd
from config import DB_FILE

def search_company():
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql("SELECT * FROM symbols WHERE company_name LIKE '%Kanchi Karpooram%'", conn)
    print(df)
    conn.close()

if __name__ == "__main__":
    search_company()
