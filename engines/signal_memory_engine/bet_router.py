from __future__ import annotations
try:
    # When imported as a package: python -m engines.signal_memory_engine.bet_router
    from .bet_placer import place_scalp_trade
except Exception:
    try:
        # When run from this folder: python bet_router.py
        from bet_placer import place_scalp_trade
    except Exception:
        # Absolute fallback
        from engines.signal_memory_engine.bet_placer import place_scalp_trade
import sqlite3
from datetime import datetime
from config_paths import DB_PATH


def fire_initial_scalp(signal, customer_order_ref):
    """
    Thin adapter: your bet_placer.place_scalp_trade(signal) expects
    signal['customerOrderRef'], 'stake', 'scalp_direction', etc.
    """
    # Ensure we don't mutate caller's dict
    sig = dict(signal)
    # Required by bet_placer.place_scalp_trade
    sig["customerOrderRef"] = customer_order_ref
    sig.setdefault("scalp_direction", "lay_to_back")
    sig.setdefault("stake", float(sig.get("stake", 10.0)))
    # Odds should already be a valid float; rounding happens inside bet_placer
    resp = place_scalp_trade(sig)
    if signal.get("signal_type") == "exploratory":
        engine.counter["exploratory_fired"] += 1
    if signal.get("signal_type") == "blueprint_match":
        engine.counter["blueprint_fired"] += 1

    engine.counter["scalps_fired"] += 1


    # ✅ Write to DB immediately for tracking
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO bets (
                customerOrderRef, marketId, selectionId, odds, stake, side, status, placed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            customer_order_ref,
            signal["marketId"],
            signal["selectionId"],
            signal["odds"],
            signal.get("stake", 10.0),
            "LAY" if signal.get("scalp_direction", "lay_to_back") == "lay_to_back" else "BACK",
            "unmatched",
            datetime.utcnow().isoformat()
        ))
        conn.commit()
        conn.close()
        print(f"📝 Logged scalp to DB: {customer_order_ref}")
    except Exception as e:
        print(f"⚠️ DB logging failed: {e}")

    return response

if __name__ == "__main__":
    # quick manual smoke test
    from engines.config_paths import set_db_paths, connect_db, autoscalp_db
    import sqlite3, random, json, os, time
    set_db_paths("learning")

    # ensure bets.log exists if your module expects it
    try:
        data_dir = os.path.dirname(autoscalp_db())
        os.makedirs(data_dir, exist_ok=True)
        open(os.path.join(data_dir, "bets.log"), "a").close()
    except Exception:
        pass

    # pick a real runner
    bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
    adb = sqlite3.connect(autoscalp_db(), timeout=6); adb.row_factory = sqlite3.Row
    m = bdb.execute("SELECT marketId FROM markets_schedule WHERE datetime(off_at_utc)>=datetime('now','utc') ORDER BY datetime(off_at_utc) ASC LIMIT 8").fetchall()
    if not m: m = adb.execute("SELECT DISTINCT marketId AS marketId FROM inbound_oc_cache ORDER BY id DESC LIMIT 8").fetchall()
    mid = str(random.choice(m)["marketId"])
    rows = adb.execute("SELECT selectionId, oc1, oc1_band_json FROM inbound_oc_cache WHERE marketId=? ORDER BY id DESC", (mid,)).fetchall()
    latest = {}
    for r in rows:
        sid = str(r["selectionId"])
        if sid not in latest: latest[sid]=r
    sid, r = random.choice(list(latest.items()))
    oc1 = r["oc1"]
    if oc1 is None:
        band = json.loads(r["oc1_band_json"]) if r["oc1_band_json"] else []
        oc1 = float(band[-1]) if band else 6.0

    signal = {
        "marketId": mid,
        "selectionId": sid,
        "odds": float(oc1),
        "stake": 2.0,
        "scalp_direction": "lay_to_back",   # change to "back_to_lay" to test BACK
        "signal_type": "router_test",
        "confidence": 0.5,
    }
    ref = f"TST-{int(time.time())}-{random.randint(100,999)}"
    print(f"[SMOKE] firing: ref={ref} mid={mid} sid={sid} odds={oc1:.2f}")
    print(fire_initial_scalp(signal, ref))

