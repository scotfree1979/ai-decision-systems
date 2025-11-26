# === PATCH START ===
# 📍 TARGET: engines/mastery/bridge_feedback.py
# 📆 PATCHED: 2025-11-09Z — remove alias prefix and confirm local settle join
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import os, sqlite3

# === PATCH START ===
# 📍 TARGET: engines/mastery/bridge_feedback.py:summarise_bridge_feedback
# 📆 PATCHED: 2025-11-10Z — self-creating feedback_metrics table (bridge active)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def summarise_bridge_feedback(con):
    """
    Assimilate bridge feedback into mastery_training_metrics.
    Creates feedback_metrics on the fly if missing.
    """
    try:
        cur = con.cursor()

        # --- ensure feedback_metrics table exists -----------------------------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS feedback_metrics(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                marketId TEXT,
                selectionId TEXT,
                bucket TEXT,
                bias_delta REAL,
                exposure_gap REAL,
                hedge_eff REAL,
                pnl_now REAL,
                ts TEXT DEFAULT (datetime('now','utc'))
            );
        """)

        # --- ensure summary table exists -------------------------------------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bridge_feedback_summary(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bucket TEXT,
                avg_bias_delta REAL,
                avg_exposure_gap REAL,
                avg_hedge_eff REAL,
                avg_pnl_now REAL,
                created_at TEXT DEFAULT (datetime('now','utc'))
            );
        """)

        # --- aggregate feedback → summary ------------------------------------
        rows = cur.execute("""
            SELECT
                COALESCE(o.success, CASE WHEN o.realized_pnl>0 THEN 1 ELSE 0 END) AS success,
                f.bucket,
                AVG(f.bias_delta)  AS avg_bias_delta,
                AVG(f.exposure_gap) AS avg_exposure_gap,
                AVG(f.hedge_eff)    AS avg_hedge_eff,
                AVG(f.pnl_now)      AS avg_pnl_now
            FROM feedback_metrics f
            LEFT JOIN orders o USING(marketId, selectionId)
            GROUP BY f.bucket;
        """).fetchall()

        if not rows:
            print("[bridge_feedback] ℹ️ no feedback metrics found — skipping.")
            return

        cur.executemany("""
            INSERT INTO bridge_feedback_summary(
                bucket,avg_bias_delta,avg_exposure_gap,avg_hedge_eff,avg_pnl_now
            ) VALUES(:bucket,:avg_bias_delta,:avg_exposure_gap,:avg_hedge_eff,:avg_pnl_now);
        """, rows)
        con.commit()
        print(f"[bridge_feedback] ✅ assimilated {len(rows)} feedback buckets.")
    except Exception as e:
        print(f"[bridge_feedback] ❌ SQL failed: {e}")
# === PATCH END ===

