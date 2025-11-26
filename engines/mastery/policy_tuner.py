# === PATCH START ===
# 📍 TARGET: engines/mastery/policy_tuner.py
# 📆 PATCHED: 2025-10-24T21:10Z — Stage 5 adaptive tuner
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import sqlite3, math, os
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "autoscalp_gui.db")

def compute_letter_weights(window_min: int = 30):
    """
    Read mastery_posteriors and recent cash-out performance,
    return new per-letter confidence weights and risk multipliers.
    """
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    q = f"""
        SELECT letter,
               AVG(pnl_delta)      AS pnl_avg,
               AVG(win_rate)       AS win_avg,
               COUNT(*)            AS n
          FROM mastery_posteriors
         WHERE ts >= datetime('now','-{window_min} minute','utc')
      GROUP BY letter
    """
    rows = con.execute(q).fetchall()
    con.close()
    if not rows:
        return {}

    weights = {}
    for r in rows:
        letter = r["letter"]
        pnl = float(r["pnl_avg"] or 0.0)
        win = float(r["win_avg"] or 0.0)
        # soft-normalize win % → 0-1
        w = 0.5 + 0.5 * math.tanh((win - 0.5) * 3.0)
        # scale by pnl sign and magnitude
        adj = 1.0 + (pnl / 50.0)
        weights[letter] = round(max(0.05, min(w * adj, 2.0)), 3)

    return weights


def update_mastery_policy(weights: dict):
    """
    Persist new weights into mastery_policy table and broadcast via event_sink.
    """
    if not weights:
        return 0

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("""
        CREATE TABLE IF NOT EXISTS mastery_policy (
            letter TEXT PRIMARY KEY,
            weight REAL,
            updated_at TEXT DEFAULT (datetime('now','utc'))
        )
    """)
    for letter, w in weights.items():
        con.execute("""
            INSERT INTO mastery_policy(letter, weight, updated_at)
            VALUES (?, ?, datetime('now','utc'))
            ON CONFLICT(letter) DO UPDATE
               SET weight=excluded.weight,
                   updated_at=datetime('now','utc')
        """, (letter, w))
    con.commit()
    con.close()

    # emit summary event
    try:
        from engines.mastery import event_sink
        event_sink.emit(
            "feedback_policy_update",
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "letters": weights,
                "source": "LIVE",
            },
        )
    except Exception as e:
        print(f"[policy_tuner] emit warn: {e}")

    return len(weights)


def run(window_min: int = 30):
    """One-shot convenience entry point used by scheduler."""
    weights = compute_letter_weights(window_min)
    return update_mastery_policy(weights)
# === PATCH END ===
