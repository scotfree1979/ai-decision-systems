# === PATCH START ===
# 📍 TARGET: engines/odds/writers.py
# 🔎 ACTION: create/replace file (correct writer connections)
from __future__ import annotations
from typing import Dict, Any
from datetime import datetime, timezone
from engines.config_paths import connect_db as open_bets_db, q_retry as _q

print(">>>> USING WRITERS FROM:", __file__)

import engines.config_paths as CP
print("[DEBUG] auto_conn pointer =", CP.auto_conn)
print("[DEBUG] open_auto_db pointer =", CP.open_auto_db)
print("[DEBUG] DAL_MODE =", CP.DAL_MODE)

def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# CORRECT WRITER IMPORT (DO NOT USE auto_conn)
# ============================================================
# Use REAL WRITE CONNECTION, not DALReadProxy
from engines.config_paths import auto_conn_live as _auto_conn


# ============================================================
# WRITE odds_current (LIVE writer → LiveCache)
# ============================================================


# === PATCH START ============================================================
# 📍 TARGET: engines/odds/writers.py
# 🔎 SEARCH: def upsert_odds_current
# ⛏️ ACTION: full function replacement
# 📆 PATCHED: 2026-02-20 — LOCAL writer (restore legacy odds_current behaviour)
# ============================================================================

from engines.config_paths import open_auto_db   # <-- LOCAL database writer

def upsert_odds_current(day: str, marketId: str, selectionId: str, cur: dict) -> None:
    """
    LOCAL-ONLY odds_current writer.
    BUS, Scope, MarketMonitor, CTXv7 all read from autoscalp_gui.db.
    OddsService MUST therefore write into LOCAL autoscalp_gui.db to keep
    the decision pipeline alive.
    """
    con = None
    try:
        # LOCAL direct write — absolutely NOT auto_conn_live
        con = open_auto_db(rw=True)

        con.execute("""
            INSERT INTO odds_current(
                day,
                marketId,
                selectionId,
                updated_ts,
                ltp,
                back1,
                lay1,
                fav_rank_now,
                mto_minutes,
                slope_ppm,
                tick_vel_1s_up,
                tick_vel_3s_up
            )
            VALUES(
                date('now','utc'),
                ?, ?, datetime('now','utc'),
                ?, ?, ?, ?, ?, ?, ?, ?
            )
        """, (
            marketId,
            selectionId,
            cur.get("ltp"),
            cur.get("back1"),
            cur.get("lay1"),
            cur.get("fav_rank_now"),
            cur.get("mto_minutes"),
            cur.get("slope_ppm"),
            cur.get("tick_vel_1s_up"),
            cur.get("tick_vel_3s_up"),
        ))

        con.commit()

    except Exception as e:
        print(f"[odds:write][ERR] upsert_odds_current mid={marketId} sid={selectionId}: {e}")

    finally:
        try:
            if con:
                con.close()
        except Exception:
            pass

# === PATCH END ==============================================================


# === PATCH END ==============================================================



# ============================================================
# WRITE odds_snapshots
# ============================================================
# === PATCH START ============================================================
# 📍 TARGET: engines/odds/writers.py
# 🔎 SEARCH: def append_snapshot
# ⛏️ ACTION: replace writer connection
# 📆 PATCHED: 2026-02-20 — LOCAL snapshot writer
# ============================================================================

from engines.config_paths import open_auto_db

def append_snapshot(marketId: str, selectionId: str, snap: Dict[str,Any]) -> None:
    try:
        con = open_auto_db(rw=True)   # LOCAL writer
        con.execute("""
          INSERT INTO odds_snapshots(
            ts,marketId,selectionId,ltp,back1,lay1,slope_ppm,
            tick_vel_1s_up,tick_vel_3s_up,fav_rank_now,fav_rank_30s,
            mto_minutes,source
          )
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            marketId, selectionId,
            snap.get("ltp"), snap.get("back1"), snap.get("lay1"),
            snap.get("slope_ppm"), snap.get("tick_vel_1s_up"),
            snap.get("tick_vel_3s_up"), snap.get("fav_rank_now"),
            snap.get("fav_rank_30s"), snap.get("mto_minutes"),
            snap.get("source"),
        ))
        con.commit()

    except Exception as e:
        print(f"[odds:snapshot] warn: {e}")

    finally:
        try:
            con.close()
        except:
            pass

# === PATCH END ==============================================================



# ============================================================
# BETS DB WRITERS (unchanged)
# ============================================================
def record_blueprint_event(day: str, marketId: str, selectionId: str|None,
                           key: str, score: float, window_tag: str, context_json: str) -> None:
    try:
        b = open_bets_db(ro=False)
        _q(b, """
          INSERT INTO blueprint_events(day,marketId,selectionId,blueprint_key,strength,
                                       detected_at,window_tag,context_json)
          VALUES (?,?,?,?,?,datetime('now','utc'),?,?)
        """, (day, marketId, selectionId, key, float(score), window_tag, context_json))
        b.commit()
        b.close()
    except Exception as e:
        print(f"[blueprint:event] warn: {e}")


def upsert_blueprint_state(day: str, marketId: str, selectionId: str,
                           key: str|None, score: float|None, features_json: str) -> None:
    try:
        b = open_bets_db(ro=False)
        _q(b, """
          INSERT INTO blueprint_state(
            day,marketId,selectionId,updated_at,blueprint_key,score,features_json
          )
          VALUES (?,?,?,?,?,?,?)
          ON CONFLICT(day,marketId,selectionId) DO UPDATE SET
            updated_at=excluded.updated_at,
            blueprint_key=excluded.blueprint_key,
            score=excluded.score,
            features_json=excluded.features_json
        """, (day, marketId, selectionId, _utcnow(), key, score, features_json))
        b.commit()
        b.close()
    except Exception as e:
        print(f"[blueprint:state] warn: {e}")

# === PATCH END ===
