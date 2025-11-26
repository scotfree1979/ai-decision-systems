# === PATCH START ===
# 📍 TARGET: engines/odds/writers.py
# 🔎 SEARCH: (file does not exist)
# ⛏️ ACTION: create file
from __future__ import annotations
from typing import Dict, Any
from datetime import datetime, timezone
from engines.config_paths import auto_conn as open_auto_db, connect_db as open_bets_db, q_retry as _q
print(">>>> USING WRITERS FROM:", __file__)

import engines.config_paths as CP
print("[DEBUG] auto_conn pointer =", CP.auto_conn)
print("[DEBUG] open_auto_db pointer =", CP.open_auto_db)
print("[DEBUG] DAL_MODE =", CP.DAL_MODE)



def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

# === PATCH START ===
# 📍 TARGET: engines/odds/writers.py:upsert_odds_current
# 🔎 SEARCH: def upsert_odds_current
# 📆 PATCHED: 2025-11-21

from engines.config_paths import auto_conn as _auto_conn

def upsert_odds_current(day: str, marketId: str, selectionId: str, cur: dict) -> None:
    """
    Schema-verified writer for autoscalp_gui.db:odds_current

    Columns:
        day TEXT,
        marketId TEXT,
        selectionId TEXT,
        updated_ts TEXT,
        ltp REAL,
        back1 REAL,
        lay1 REAL,
        fav_rank_now INTEGER,
        mto_minutes REAL,
        slope_ppm REAL,
        tick_vel_1s_up INTEGER,
        tick_vel_3s_up INTEGER
    """
    from engines.config_paths import q_retry

    # DAL writer → CLOUD
    con = _auto_conn(rw=True)
    try:
        q_retry(con, """
            INSERT OR REPLACE INTO odds_current (
                day, marketId, selectionId, updated_ts,
                ltp, back1, lay1, fav_rank_now,
                mto_minutes, slope_ppm, tick_vel_1s_up, tick_vel_3s_up
            ) VALUES (
                ?, ?, ?, datetime('now','utc'),
                ?, ?, ?, ?, ?, ?, ?, ?
            )
        """, (
            day,
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
        print(f"[WRITERS] odds_current warn mid={marketId}: {e}")
    finally:
        try: con.close()
        except Exception: pass
# === PATCH END ===

def append_snapshot(marketId: str, selectionId: str, snap: Dict[str,Any]) -> None:
    con = open_auto_db(rw=True)
    if not con: return
    _q(con, """
      INSERT INTO odds_snapshots(ts,marketId,selectionId,ltp,back1,lay1,slope_ppm,tick_vel_1s_up,tick_vel_3s_up,fav_rank_now,fav_rank_30s,mto_minutes,source)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (_utcnow(), marketId, selectionId,
          snap.get("ltp"), snap.get("back1"), snap.get("lay1"),
          snap.get("slope_ppm"), snap.get("tick_vel_1s_up"), snap.get("tick_vel_3s_up"),
          snap.get("fav_rank_now"), snap.get("fav_rank_30s"), snap.get("mto_minutes"), snap.get("source")))
    con.commit()
    try: con.close()
    except Exception: pass

def record_blueprint_event(day: str, marketId: str, selectionId: str|None,
                           key: str, score: float, window_tag: str, context_json: str) -> None:
    b = open_bets_db(ro=False)
    if not b: return
    _q(b, """
      INSERT INTO blueprint_events(day,marketId,selectionId,blueprint_key,strength,detected_at,window_tag,context_json)
      VALUES (?,?,?,?,?,datetime('now','utc'),?,?)
    """, (day, marketId, selectionId, key, float(score), window_tag, context_json))
    b.commit()
    try: b.close()
    except Exception: pass

def upsert_blueprint_state(day: str, marketId: str, selectionId: str,
                           key: str|None, score: float|None, features_json: str) -> None:
    b = open_bets_db(ro=False)
    if not b: return
    _q(b, """
      INSERT INTO blueprint_state(day,marketId,selectionId,updated_at,blueprint_key,score,features_json)
      VALUES (?,?,?,?,?,?,?)
      ON CONFLICT(day,marketId,selectionId) DO UPDATE SET
        updated_at=excluded.updated_at,
        blueprint_key=excluded.blueprint_key,
        score=excluded.score,
        features_json=excluded.features_json
    """, (day, marketId, selectionId, _utcnow(), key, score, features_json))
    b.commit()
    try: b.close()
    except Exception: pass
# === PATCH END ===
