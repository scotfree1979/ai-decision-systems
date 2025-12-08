# === PATCH START ============================================================
# 📍 TARGET: engines/bus/test_market_sim.py
# 📆 PATCHED: 2026-02-13 — one-market simulator
# ============================================================================

#!/usr/bin/env python3
from engines.bus.bus import BUS
from engines.config_paths import auto_conn, q_retry as _q
import time, sqlite3, sys

def load_ticks(mid, sid, limit=500):
    con = auto_conn(rw=False); con.row_factory = sqlite3.Row
    rows = _q(con, """
        SELECT ltp, updated_ts
          FROM odds_current
         WHERE marketId=? AND selectionId=?
         ORDER BY updated_ts ASC
         LIMIT ?
    """, (mid, sid, limit)).fetchall()
    con.close()
    return [(float(r["ltp"]), str(r["updated_ts"])) for r in rows]

def run(mid, sid):
    ticks = load_ticks(mid, sid)
    for px, ts in ticks:
        BUS.tick()      # full bus execution
        print(f"[SIM] tick px={px} ts={ts}")
        time.sleep(0.1)

if __name__ == "__main__":
    mid, sid = sys.argv[1], sys.argv[2]
    run(mid, sid)

# === PATCH END ================================================================
