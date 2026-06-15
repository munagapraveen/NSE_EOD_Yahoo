"""One-off script: delete pre-listing NULL rows from raw_eod_prices."""

import sqlite3
import os
from config import DB_FILE

def main():
    db_path = str(DB_FILE)
    if not os.path.exists(db_path):
        print(f"Database file not found at: {db_path}")
        return

    size_before = os.path.getsize(db_path) / (1024 * 1024)
    print(f"DB size before : {size_before:.1f} MB")

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")

        print("Counting NULL rows...")
        null_count = conn.execute(
            "SELECT COUNT(*) FROM raw_eod_prices WHERE close IS NULL OR close = 0"
        ).fetchone()[0]
        print(f"NULL rows found : {null_count:,}")

        if null_count == 0:
            print("Nothing to delete.")
        else:
            print("Deleting...")
            deleted = conn.execute(
                "DELETE FROM raw_eod_prices WHERE close IS NULL OR close = 0"
            ).rowcount
            conn.commit()
            print(f"Deleted : {deleted:,} rows")

            print("Running VACUUM to reclaim disk space...")
            conn.execute("VACUUM")
    finally:
        conn.close()

    size_after = os.path.getsize(db_path) / (1024 * 1024)
    print(f"DB size after  : {size_after:.1f} MB")
    print(f"Space reclaimed: {size_before - size_after:.1f} MB")
    print("Done.")

if __name__ == "__main__":
    main()
