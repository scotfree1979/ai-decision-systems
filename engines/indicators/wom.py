# === PATCH START ===
# 📍 TARGET: engines/indicators/wom.py:compute_wom_for_scope
# 📆 PATCHED: 2025-11-02Z — schema-verified odds_current (no volume data)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def compute_wom_for_scope(db_path: str = "data/autoscalp_gui.db"):
    """
    Schema-verified WOM placeholder.
    odds_current has only price columns (back1, lay1) — no volumes.
    Returns dict {(marketId, selectionId): None}.
    """
    # [SCHEMA VERIFIED] odds_current(day, marketId, selectionId, updated_ts,
    #                                ltp, back1, lay1, fav_rank_now,
    #                                mto_minutes, slope_ppm,
    #                                tick_vel_1s_up, tick_vel_3s_up)
    import sqlite3
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT marketId, selectionId, back1, lay1, updated_ts
          FROM odds_current
         WHERE date(updated_ts)=date('now','utc')
         GROUP BY marketId, selectionId
    """).fetchall()
    con.close()

    out = {}
    for r in rows:
        out[(r["marketId"], r["selectionId"])] = None  # no volume data
    return out


if __name__ == "__main__":
    wom_map = compute_wom_for_scope()
    print("=== WOM snapshot (schema-verified, no volumes) ===")
    for (mid, sid), wom in list(wom_map.items())[:20]:
        print(f"{mid}:{sid} → WOM={wom}")
# === PATCH END ===
