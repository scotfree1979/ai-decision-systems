import json
import os
import sqlite3
from datetime import datetime
from config_paths import DB_PATH  # ✅ Correct database reference

def save_snapshot(tag="unknown"):
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"snapshots/snapshot_{tag}_{timestamp}.json"
    os.makedirs("snapshots", exist_ok=True)

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM bets")
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()

        data = [dict(zip(columns, row)) for row in rows]

        with open(filename, "w") as f:
            json.dump(data, f, indent=2)

        conn.close()

    except Exception as e:
        print(f"❌ Snapshot error: {e}")
