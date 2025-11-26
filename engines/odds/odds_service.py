# === PATCH START ===
# 📍 TARGET: engines/odds/odds_service.py
# 🔎 SEARCH: (file does not exist)
# ⛏️ ACTION: create file
from __future__ import annotations
    
from typing import Dict, Tuple
from collections import defaultdict, deque
from datetime import datetime, timezone
import time

from engines.decision_engine.decide_once.scope import build_and_maintain_scope, SCOPE_WINDOW, SCOPE_OVERRIDES
from engines.decision_engine.decide_once.helpers import status_once
from engines.utils.api_tools import fetch_live_odds as fetch_market_book

from engines.odds.features import slope_ppm, tick_velocity, ranks_by_ltp, market_breadth
from engines.odds.writers import upsert_odds_current, append_snapshot
from engines.blueprint.engine import update_for_market
from engines.decision_engine.decide_once.scope import read_scope_window, ordered_markets_for_tick
from engines.fallbackscope import _next5_from_bets_with_active
# in-memory ring buffers (seconds,ltp)
# BUFFERS[(mid,sid)] = deque[(ts, ltp)]
BUFFERS: Dict[Tuple[str,str], deque] = defaultdict(lambda: deque(maxlen=90))
# per-market rank history to compute fav_rank_30s/breadth
RANK_HISTORY: Dict[str, deque] = defaultdict(lambda: deque(maxlen=180))

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def _today() -> str:
    return _utcnow().strftime("%Y-%m-%d")

def _mto_minutes_for(mid: str) -> float:
    try:
        from engines.decision_engine.orchestrator import _compute_minutes_to_off
        mto, _ = _compute_minutes_to_off(mid, source="LIVE")
        return float(mto) if mto is not None else 9999.0
    except Exception:
        return 9999.0

def _window_tag(mto: float) -> str:
    if mto <= 0.0: return "IP"
    if mto <= 20.0: return "T20"
    return "PRE"

def _next5_from_odds_current(fresh_sec: int = 300, max_items: int = 5) -> list[str]:
    """
    Pull next markets from autoscalp_gui.db.odds_current, ordered by recency.
    Used as a middle fallback before falling back to bets.db.
    """
    from engines.decision_engine.decide_once.helpers import open_auto_db as _adb, q_retry as _q
    import sqlite3
    con = None
    try:
        con = _adb(ro=True); con.row_factory = sqlite3.Row
        rows = _q(con, """
            SELECT marketId, MAX(updated_ts) AS mx
              FROM odds_current
             WHERE datetime(updated_ts) >= datetime('now','utc', ?)
             GROUP BY marketId
             ORDER BY MAX(datetime(updated_ts)) DESC
             LIMIT ?
        """, (f"-{int(fresh_sec)} seconds", int(max_items))).fetchall() or []
        return [str(r["marketId"]) for r in rows]
    except Exception:
        return []
    finally:
        try:
            if con: con.close()
        except Exception:
            pass

# === PATCH START ===
# 📍 TARGET: engines/odds/odds_service.py
# 📆 PATCHED: 2025-10-11T18:05Z — fix unbound book + odds:write safety
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def tick_update_for_scope(inplay_window_min: int = 15) -> None:
    """
    Collect odds for current scope markets and write to odds_current.
    Scope gating is now canonical; all internal date/time filters removed.
    """
    try:
        try:
            raw_scope = read_scope_window(ahead_min=inplay_window_min + 25)
        except TypeError:
            raw_scope = read_scope_window(ahead_min=inplay_window_min + 25)

        # normalize scope shape
        snap = {"markets": []}
        if isinstance(raw_scope, dict):
            snap["markets"] = [
                m if isinstance(m, dict) else {"marketId": str(m)}
                for m in raw_scope.get("markets", [])
            ]
        elif isinstance(raw_scope, list):
            snap["markets"] = [{"marketId": str(x)} for x in raw_scope]
        elif isinstance(raw_scope, str):
            snap["markets"] = [{"marketId": raw_scope}]

        mids = [m["marketId"] for m in snap.get("markets", []) if m.get("marketId")]
        if not mids:
            # 1) try odds_current-based fallback
            mids = _next5_from_odds_current(fresh_sec=300, max_items=5)
            # 2) then bets-based fallback
            if not mids:
                mids = _next5_from_bets_with_active(max_items=5)
            if not mids:
                print("[odds:filter] warn: empty scope snapshot (after fallbacks)")
                return


        status_once("odds:write", True, f"scope_mids={len(mids)}")

        from engines.odds.writers import upsert_odds_current
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # hybrid fetch helper outside loop
        def _safe_fetch_market_book(mid: str):
            """Try DB fallback first; fallback to live API if no data."""
            try:
                from engines.odds.providers.db_fallback_provider import fetch_market_book as _fmb
                rows = _fmb(mid) or []
                if rows:
                    return {"runners": rows}
            except Exception:
                pass
            try:
                from engines.utils.api_tools import fetch_live_odds as _api
                odds = _api(None, mid, None)
                if isinstance(odds, list):
                    return {"runners": odds}
                elif isinstance(odds, dict):
                    return {"runners": [
                        {"selectionId": sid, "ltp": v.get("lay") or v.get("back")}
                        for sid, v in odds.items()
                    ]}
            except Exception as e:
                print(f"[ODDS_SERVICE] live API fallback failed mid={mid}: {e}")
            return {"runners": []}

        # --- main loop ---
        for mid in mids:
            book = {"runners": []}  # ✅ always defined
            try:
                book = _safe_fetch_market_book(mid)

                if not isinstance(book, dict) or not book.get("runners"):
                    continue

                for r in book["runners"]:
                    sid = str(r.get("selectionId"))
                    ex = r.get("ex", {}) or {}
                    back1 = (ex.get("availableToBack") or [{}])[0].get("price")
                    lay1  = (ex.get("availableToLay") or [{}])[0].get("price")
                    ltp   = float(r.get("lastPriceTraded") or r.get("ltp") or 0.0)
                    cur = {
                        "ltp": ltp,
                        "back1": back1,
                        "lay1": lay1,
                        "fav_rank_now": None,
                        "mto_minutes": None,
                        "slope_ppm": None,
                        "tick_vel_1s_up": None,
                        "tick_vel_3s_up": None,
                    }
                    upsert_odds_current(today, mid, sid, cur)

            except Exception as e:
                print(f"[odds:write] warn mid={mid}: {e}")

        print(f"[odds:write] persisted {len(mids)} markets to odds_current")

    except Exception as e:
        status_once("odds:write", False, str(e))
# === PATCH END ===


# --- CLI entrypoint for standalone debug --------------------------------------
if __name__ == "__main__":
    print("=== DEBUG tick_update_for_scope (standalone) ===")
    try:
        tick_update_for_scope(inplay_window_min=15)
        print("tick_update_for_scope completed OK")
    except Exception as e:
        import traceback
        print(f"[ERROR] tick_update_for_scope failed: {e}")
        traceback.print_exc()


