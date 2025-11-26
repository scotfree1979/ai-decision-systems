#!/usr/bin/env python3
"""
form_canonical_builder.py — Build canonical runner_form_canonical
Unifies BETS + SETTLEMENTS + RUNNER_INFO into one reference table
for both dashboard form and Mastery v7 context.
"""

import sqlite3

def build_canonical_runner_form():
    print("[form] Building canonical runner_form_canonical …")

    SETTLE_DB = "data/settlements.db"
    BETS_DB   = "data/bets.db"

    con = sqlite3.connect(SETTLE_DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # attach the bets database
    cur.execute(f"ATTACH DATABASE '{BETS_DB}' AS betsdb;")

    # create canonical table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS runner_form_canonical (
            runner_name TEXT,
            selectionId TEXT,
            marketId TEXT,
            day TEXT,
            trainer TEXT,
            jockey TEXT,
            age INTEGER,
            surface TEXT,
            distance_band TEXT,
            class_band TEXT,
            profit REAL,
            status TEXT,
            inferred_flag INTEGER DEFAULT 0,
            runs INTEGER,
            wins INTEGER,
            win_rate REAL,
            avg_pnl REAL,
            last_seen TEXT,
            created_at TEXT DEFAULT (datetime('now','utc')),
            PRIMARY KEY (marketId, selectionId)
        );
    """)

    cur.execute("DELETE FROM runner_form_canonical;")

    # canonical merge: Bets + Settlements + Runner Info
    cur.execute("""
        INSERT INTO runner_form_canonical
        (runner_name, selectionId, marketId, day, trainer, jockey, age,
         surface, distance_band, class_band, profit, status, inferred_flag)
        SELECT
            b.horse_name,
            b.selectionId,
            b.marketId,
            date(s.settledDate,'utc'),
            ri.trainerName,
            ri.jockeyName,
            ri.age,
            CASE
                WHEN lower(b.market_name) LIKE '%hurdle%' OR
                     lower(b.market_name) LIKE '%chase%' THEN 'jumps'
                WHEN lower(b.market_name) LIKE '%aw%' OR
                     lower(b.market_name) LIKE '%all-weather%' THEN 'all_weather'
                ELSE 'flat'
            END AS surface,
            CASE
                WHEN lower(b.market_name) LIKE '5f%' OR lower(b.market_name) LIKE '6f%' THEN 'sprint'
                WHEN lower(b.market_name) LIKE '7f%' OR lower(b.market_name) LIKE '1m%' THEN 'middle'
                WHEN lower(b.market_name) LIKE '1m4f%' OR lower(b.market_name) LIKE '1m6f%' THEN 'staying'
                ELSE 'other'
            END AS distance_band,
            CASE
                WHEN lower(b.market_name) LIKE '%class 1%' THEN 'class1'
                WHEN lower(b.market_name) LIKE '%class 2%' THEN 'class2'
                WHEN lower(b.market_name) LIKE '%class 3%' THEN 'class3'
                WHEN lower(b.market_name) LIKE '%class 4%' THEN 'class4'
                WHEN lower(b.market_name) LIKE '%class 5%' THEN 'class5'
                WHEN lower(b.market_name) LIKE '%class 6%' THEN 'class6'
                ELSE 'unknown'
            END AS class_band,
            SUM(COALESCE(s.profit,0.0)) AS profit,
            COALESCE(ri.status, 'UNKNOWN') AS status,
            CASE WHEN ri.status LIKE 'WINNER_INFERRED%' THEN 1 ELSE 0 END AS inferred_flag
        FROM bf_cleared_orders s
        LEFT JOIN bf_runner_info ri
               ON s.marketId=ri.marketId AND s.selectionId=ri.selectionId
        LEFT JOIN betsdb.bets b
               ON s.marketId=b.marketId AND s.selectionId=b.selectionId
        GROUP BY s.marketId, s.selectionId;
    """)

    # aggregated form stats
    cur.execute("""
        CREATE TEMP VIEW IF NOT EXISTS v_runner_form_stats AS
        SELECT
            runner_name,
            COUNT(*) AS runs,
            SUM(CASE WHEN status LIKE 'WINNER%' THEN 1 ELSE 0 END) AS wins,
            ROUND(100.0 * SUM(CASE WHEN status LIKE 'WINNER%' THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate,
            ROUND(AVG(profit), 2) AS avg_pnl,
            MAX(day) AS last_seen
        FROM runner_form_canonical
        WHERE runner_name IS NOT NULL
        GROUP BY runner_name;
    """)

    cur.execute("""
        UPDATE runner_form_canonical
           SET runs = (SELECT runs FROM v_runner_form_stats v WHERE v.runner_name = runner_form_canonical.runner_name),
               wins = (SELECT wins FROM v_runner_form_stats v WHERE v.runner_name = runner_form_canonical.runner_name),
               win_rate = (SELECT win_rate FROM v_runner_form_stats v WHERE v.runner_name = runner_form_canonical.runner_name),
               avg_pnl = (SELECT avg_pnl FROM v_runner_form_stats v WHERE v.runner_name = runner_form_canonical.runner_name),
               last_seen = (SELECT last_seen FROM v_runner_form_stats v WHERE v.runner_name = runner_form_canonical.runner_name);
    """)

    total = cur.execute("SELECT COUNT(*) FROM runner_form_canonical").fetchone()[0]
    con.commit()
    cur.execute("DETACH DATABASE betsdb;")
    con.close()
    print(f"[form] ✅ canonical runner_form_canonical built ({total:,} entries)")

if __name__ == "__main__":
    build_canonical_runner_form()
