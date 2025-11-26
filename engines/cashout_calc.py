# === PATCH START ===
# 📍 TARGET: engines/cashout_calc.py
# 🔎 SEARCH: ^# placeholder for new cashout module
# 📆 PATCHED: 2025-10-21Z — full cash-out computation helper (inbound_oc_cache → odds_current fallback)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#!/usr/bin/env python3
"""
engines/cashout_calc.py — Cash-Out Intelligence System helper

Computes live per-market cash-out PnL using inbound_oc_cache as primary source,
with fallback to odds_current.  Returns a dict {marketId: {...}} for GUI display,
and optionally writes OC-stage snapshots to cashout_series.
"""

import sqlite3, datetime

# === PATCH START ===
# 📍 TARGET: engines/cashout_calc.py:cashout_calc
# 📆 PATCHED: 2025-10-27Z — hybrid CIS fix (live snapshot + relaxed order filter)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def cashout_calc(conn: sqlite3.Connection, write_to_db: bool = False):
    """
    Hybrid CIS cash-out evaluator (LIVE-safe, WAL-aware).
    Keeps full logic but adds:
      • relaxed closed_at filter (include recent closed)
      • dynamic odds_current fallback (force latest snapshot)
      • light retry if WAL snapshot lag detected
    """
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 1️⃣ Include recently closed parents (≤10m ago) to avoid £0 gaps
    parents = cur.execute("""
        SELECT id,
               marketId,
               selectionId,
               UPPER(side) AS side,
               COALESCE(entry_matched_odds, entry_odds)   AS entry_odds,
               COALESCE(entry_matched_stake, entry_stake) AS stake
          FROM orders
         WHERE (role IS NULL OR role='PARENT')
           AND UPPER(COALESCE(entry_status,''))='MATCHED'
           AND (
                closed_at IS NULL
                OR closed_at=''
                OR datetime(closed_at) >= datetime('now','utc','-10 minute')
           )
    """).fetchall()


    # if no matched orders yet, fall back to odds_current snapshot
    if not parents:
        alt = {}
        for mkt, sid, back1, lay1 in cur.execute("""
            SELECT CAST(marketId AS TEXT), CAST(selectionId AS TEXT), back1, lay1
              FROM odds_current
             WHERE back1 IS NOT NULL OR lay1 IS NOT NULL
        """):
            l = float(lay1 or back1 or 0.0)
            alt[mkt] = {"total": 0.0, "liability": l, "children": []}
        return alt

  

# === PATCH START ===
# 📍 TARGET: engines/cashout_calc.py:cashout_calc()
# 📆 PATCHED: 2025-10-29Z — simplify odds fetch (always use current or latest oc_series)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 2️⃣ Always use the most recent odds snapshot for each (marketId, selectionId)
    px = {}
    # prefer current odds first
    for mkt, sid, back1, lay1 in cur.execute("""
        SELECT CAST(marketId AS TEXT), CAST(selectionId AS TEXT), back1, lay1
          FROM odds_current
         WHERE back1 IS NOT NULL OR lay1 IS NOT NULL
    """):
        # normalise and fall back to one side if needed
        if back1 is None and lay1 is not None:
            back1 = lay1
        elif lay1 is None and back1 is not None:
            lay1 = back1
        px[(str(mkt), str(sid))] = {"back1": float(back1 or 0.0), "lay1": float(lay1 or 0.0)}

    # fill any missing from oc_series immediately (no “missing” stage logic)
    for mkt, sid, ltp in cur.execute("""
        SELECT CAST(marketId AS TEXT), CAST(selectionId AS TEXT), odd
          FROM oc_series
         WHERE odd IS NOT NULL
      ORDER BY rowid DESC LIMIT 2000
    """):
        key = (str(mkt), str(sid))
        if key not in px:
            px[key] = {"back1": float(ltp or 0.0), "lay1": float(ltp or 0.0)}
# === PATCH END ===


# === PATCH START ===
# 📍 TARGET: engines/cashout_calc.py:cashout_calc  (replace liability aggregation)
# 📆 PATCHED: 2025-10-30Z — liability = true if-win loss per market (from orders only)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 4️⃣ Compute per-market aggregates using pure orders-based “if-win” logic
    results = {}

    # group all matched parent orders (today)
    orders = cur.execute("""
        SELECT marketId, selectionId, UPPER(side) AS side,
               COALESCE(entry_matched_odds, entry_odds) AS entry_odds,
               COALESCE(entry_matched_stake, entry_stake) AS entry_stake
          FROM orders
         WHERE (role IS NULL OR role='PARENT')
           AND LOWER(entry_status)='matched'
           AND marketId IS NOT NULL
           AND selectionId IS NOT NULL
           AND entry_odds IS NOT NULL
           AND entry_stake IS NOT NULL
    """).fetchall()


    from collections import defaultdict
    markets = defaultdict(list)
    for o in orders:
        entry_odds = float(o["entry_odds"] or 0.0)
        stake      = float(o["entry_stake"] or 0.0)
        markets[o["marketId"]].append(o)
  


    # calculate if-win P&L per runner in each market
# === PATCH START ===
# 📍 TARGET: engines/cashout_calc.py:cashout_calc
# 📆 PATCHED: 2025-11-02Z — preserve true liability, add correct cash-out potential
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    for mid, ords in markets.items():
        runner_pnls = {}
        for o in ords:
            sid   = o["selectionId"]
            side  = o["side"]
            entry = float(o["entry_odds"] or 0.0)
            stake = float(o["entry_stake"] or 0.0)
            back1 = px.get((mid, sid), {}).get("back1", entry)
            lay1  = px.get((mid, sid), {}).get("lay1",  entry)

            # mark-to-market PnL for this runner
            if side == "BACK":
                pnl_now = (lay1 - entry) * stake
            elif side == "LAY":
                pnl_now = (entry - back1) * stake
            else:
                pnl_now = 0.0
            runner_pnls[sid] = round(pnl_now, 2)

        if not runner_pnls:
            continue

        # existing liability (unchanged)
        worst_sid, worst_pnl = min(runner_pnls.items(), key=lambda kv: kv[1])
        liability_val = abs(worst_pnl)

        # new cash-out potential (best achievable close)
        potential_val = max(runner_pnls.values())

        results[mid] = {
            "total": potential_val,       # dashboard cash-out figure
            "liability": liability_val,   # keep existing liability logic
            "worst_runner": worst_sid,
            "runner_pnls": runner_pnls,
            "children": [
                {"selectionId": sid, "liability": abs(pnl)}
                for sid, pnl in runner_pnls.items()
            ],
        }
# === PATCH END ===


    # 5️⃣ If still empty, retry once after a WAL checkpoint (rare)
    if not results:
        try:
            cur.execute("PRAGMA wal_checkpoint(PASSIVE);")
            return cashout_calc(conn, write_to_db=write_to_db)
        except Exception:
            pass

    # 6️⃣ Preserve Mastery + live_state emit blocks (unchanged)
    try:
        from engines.mastery import event_sink
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        for mid, info in results.items():
            payload = {
                "ts": ts,
                "marketId": mid,
                "cashout_total": round(info.get("total", 0.0), 4),
                "liability_total": round(info.get("liability", 0.0), 4),
                "source": "LIVE"
            }
            event_sink.emit("cashout_tick", payload)
    except Exception as e:
        print(f"[cashout] event emit warn: {e}")

    try:
        from engines.mastery.live_state import record as record_live_state
        for mid, info in results.items():
            record_live_state(
                marketId=mid,
                pnl_now=float(info.get("total", 0.0)),
                liability=float(info.get("liability", 0.0)),
                status="OPEN"
            )
    except Exception as e:
        print(f"[cashout] live_state warn: {e}")

    return results
# === PATCH END ===


