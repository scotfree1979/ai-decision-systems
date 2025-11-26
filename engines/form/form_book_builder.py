#!/usr/bin/env python3
# === PATCH START ===
# 📍 TARGET: engines/form/form_book_builder.py
# 📆 PATCHED: 2025-10-30Z — combine official + inferred winners into runner_form
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import sqlite3, datetime

def build_runner_form():
    print("[form] Building unified runner_form …")
    con = sqlite3.connect("data/settlements.db")
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS runner_form (
            day TEXT, marketId TEXT, selectionId TEXT,
            runner_name TEXT, trainer_name TEXT, jockey_name TEXT,
            age INTEGER, weightCarried REAL, officialRating INTEGER,
            profit REAL, status TEXT, inferred_flag INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','utc'))
        );
    """)

    cur.execute("""
        INSERT OR REPLACE INTO runner_form
        (day, marketId, selectionId, runner_name, trainer_name, jockey_name,
         age, weightCarried, officialRating, profit, status, inferred_flag)
        SELECT
            date(co.settledDate,'utc'),
            co.marketId, co.selectionId,
            ri.runnerName, ri.trainerName, ri.jockeyName,
            ri.age, ri.weightCarried, ri.officialRating,
            SUM(co.profit),
            ri.status,
            CASE WHEN ri.status LIKE 'WINNER_INFERRED%' THEN 1 ELSE 0 END
          FROM bf_cleared_orders co
          LEFT JOIN bf_runner_info ri
            ON co.marketId=ri.marketId AND co.selectionId=ri.selectionId
         WHERE co.marketId IS NOT NULL
         GROUP BY co.marketId, co.selectionId;
    """)
    con.commit()
    rows = cur.execute("SELECT COUNT(*) AS n FROM runner_form").fetchone()["n"]
    con.close()
    print(f"[form] ✅ runner_form built with {rows:,} entries.")

if __name__ == "__main__":
    build_runner_form()
# === PATCH END ===
