# engines/fallbackscope.py
from __future__ import annotations
import sqlite3, os
from datetime import datetime, timezone
from typing import List, Dict

# --- DB paths (reuse project helpers if present) ------------------------------
try:
    from engines.config_paths import bets_db, autoscalp_db
except Exception:
    def bets_db() -> str:
        return os.path.join("data", "bets.db")
    def autoscalp_db() -> str:
        return os.path.join("data", "autoscalp_gui.db")


# --- Helpers -----------------------------------------------------------------
def _connect_ro(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=3.0, isolation_level=None)
    con.row_factory = sqlite3.Row
    return con

def next5_from_betsdb(limit: int = 5, odds_min: float = 1.5, odds_max: float = 8.0) -> List[Dict]:
    """
    Return the next 5 markets from bets.db (ordered by marketStartTime),
    each with active SIDs resolved via odds_current → inbound_oc_cache → bets.anchor_odd.
    """
    out: List[Dict] = []
    try:
        # Step 1: get next 5 markets by start time from bets.db
        con_b = _connect_ro(bets_db())
        rows = con_b.execute("""
            SELECT marketId, marketStartTime
              FROM bets
             WHERE datetime(marketStartTime) >= datetime('now','utc')
             GROUP BY marketId
             ORDER BY datetime(marketStartTime) ASC
             LIMIT ?
        """, (limit,)).fetchall() or []
        con_b.close()

        mids = [str(r["marketId"]) for r in rows]

        # Step 2: resolve SIDs for each market
        con_a = _connect_ro(autoscalp_db())
        for r in rows:
            mid = str(r["marketId"])
            off_ts = str(r["marketStartTime"])
            sids: List[str] = []

            # 2a: latest odds_current
            sid_rows = con_a.execute("""
                WITH latest AS (
                  SELECT selectionId, MAX(updated_ts) AS mx
                    FROM odds_current
                   WHERE marketId=?
                   GROUP BY selectionId
                )
                SELECT o.selectionId, o.ltp
                  FROM latest l
                  JOIN odds_current o
                    ON o.selectionId=l.selectionId
                   AND o.marketId=?
                   AND o.updated_ts=l.mx
            """, (mid, mid)).fetchall() or []

            for s in sid_rows:
                try:
                    px = float(s["ltp"])
                    if odds_min <= px <= odds_max:
                        sids.append(str(s["selectionId"]))
                except Exception:
                    pass

            # 2b: fallback → inbound_oc_cache
            if not sids:
                cache_rows = con_a.execute("""
                    SELECT selectionId, oc1, anchor_odd, oc1_band_json
                      FROM inbound_oc_cache
                     WHERE marketId=?
                     ORDER BY id DESC
                     LIMIT 24
                """, (mid,)).fetchall() or []
                for c in cache_rows:
                    ltp = c["oc1"] or c["anchor_odd"]
                    if not ltp and c["oc1_band_json"]:
                        try:
                            arr = json.loads(c["oc1_band_json"]) or []
                            if isinstance(arr, list) and arr:
                                ltp = float(arr[-1])
                        except Exception:
                            pass
                    try:
                        if ltp and odds_min <= float(ltp) <= odds_max:
                            sids.append(str(c["selectionId"]))
                    except Exception:
                        pass

            # 2c: fallback → bets.anchor_odd
            if not sids:
                try:
                    con_b2 = _connect_ro(bets_db())
                    b_rows = con_b2.execute("""
                        SELECT selectionId, anchor_odd
                          FROM bets
                         WHERE marketId=? AND anchor_odd IS NOT NULL
                    """, (mid,)).fetchall() or []
                    con_b2.close()
                    for b in b_rows:
                        try:
                            if odds_min <= float(b["anchor_odd"]) <= odds_max:
                                sids.append(str(b["selectionId"]))
                        except Exception:
                            pass
                except Exception:
                    pass

            out.append({
                "marketId": mid,
                "off_ts": off_ts,
                "active_sids": list(dict.fromkeys(sids)),  # dedup
            })
        con_a.close()
    except Exception as e:
        print(f"[fallbackscope] error: {e}")
    return out

# === PATCH START ===
# 📍 TARGET: engines/fallbackscope.py
# 🔎 SEARCH: def _next5_from_bets_with_active
# 📆 PATCHED: 2025-10-08T01:10Z — date-safe fallback (today only)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _next5_from_bets_with_active(max_items: int = 5) -> list[str]:
    """
    Return up to N next markets with active runners from bets.db,
    restricted to today's date to avoid picking tomorrow's races.
    """
    import sqlite3
    from engines.config_paths import bets_db
    con = None
    try:
        con = sqlite3.connect(bets_db()); con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT DISTINCT marketId
              FROM bets
             WHERE date(marketStartTime)=date('now','utc')
               AND datetime(marketStartTime) >= datetime('now','utc','-15 minutes')
             ORDER BY datetime(marketStartTime) ASC
             LIMIT ?
        """, (int(max_items),)).fetchall()
        con.close()
        return [str(r["marketId"]) for r in rows]
    except Exception:
        return []
    finally:
        try:
            if con: con.close()
        except Exception:
            pass
# === PATCH END ===


# --- CLI ---------------------------------------------------------------------
if __name__ == "__main__":
    print("=== NEXT5 from bets.db (with active SIDs 1.5–8.0) ===")
    mkts = next5_from_betsdb()
    for m in mkts:
        print(f"{m['marketId']}  off_ts={m['off_ts']} active_sids={len(m['active_sids'])}")
        if m["active_sids"]:
            print("  sids:", m["active_sids"])
    print(f"\nmarkets_with_active={sum(1 for m in mkts if m['active_sids'])} / {len(mkts)}")
