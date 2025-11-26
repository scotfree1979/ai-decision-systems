# engines/decision_engine/decide_once/scope.py (V3)
from __future__ import annotations

from typing import Dict, List, Tuple
import time

from .helpers import (
    utc_now,
    build_scope as _project_build_scope,
    is_today_in_scope,
    status_once,
    open_bets_db,
    open_auto_db,
)

SCOPE_WINDOW_MAX: int = 5
SCOPE_WINDOW: List[str] = []
SCOPE_OVERRIDES: Dict[str, float] = {}
SCOPE_CURSOR: int = 0

# ---------------------------------------------------------------------------
# Fallback scope builders using your actual schemas (bets.db & autoscalp_gui.db)
# ---------------------------------------------------------------------------

SQL_MSCHED_BETS = """
SELECT marketId,
       CAST((julianday(off_at_utc) - julianday('now','utc')) * 1440.0 AS INTEGER) AS tto
FROM markets_schedule
WHERE date(off_at_utc) = date('now','utc')
ORDER BY tto ASC
LIMIT 500
"""

SQL_MSCHED_GUI = """
SELECT marketId,
       CAST((julianday(off_at_utc) - julianday('now','utc')) * 1440.0 AS INTEGER) AS tto
FROM markets_schedule
WHERE date(off_at_utc) = date('now','utc')
ORDER BY tto ASC
LIMIT 500
"""


def _fallback_scope_union() -> Dict[str, List[Tuple[str, float]]]:
    """Build a minimal scope from *either* DB if the main project scope is empty.
    Uses the real column names you provided: markets_schedule(marketId, off_at_utc).
    """
    out: Dict[str, List[Tuple[str, float]]] = {"pre_far": [], "pre_near": [], "in_play": []}

    # bets.db
    con_b = open_bets_db(ro=True)
    if con_b is not None:
        try:
            cur = con_b.execute(SQL_MSCHED_BETS)
            rows = cur.fetchall() or []
            for marketId, tto in rows:
                try:
                    t = float(tto)
                except Exception:
                    continue
                if t >= 0:
                    if t <= 20.0:
                        out["pre_near"].append((marketId, t))
                    else:
                        out["pre_far"].append((marketId, t))
        except Exception:
            pass
        finally:
            try: con_b.close()
            except Exception: pass

    # autoscalp_gui.db (optional extra supply)
    con_a = open_auto_db(ro=True)
    if con_a is not None:
        try:
            cur = con_a.execute(SQL_MSCHED_GUI)
            rows = cur.fetchall() or []
            seen = {m for m, _ in (out["pre_near"] + out["pre_far"]) }
            for marketId, tto in rows:
                if marketId in seen:
                    continue
                try:
                    t = float(tto)
                except Exception:
                    continue
                if t >= 0:
                    if t <= 20.0:
                        out["pre_near"].append((marketId, t))
                    else:
                        out["pre_far"].append((marketId, t))
        except Exception:
            pass
        finally:
            try: con_a.close()
            except Exception: pass

    # Order within each bucket by TTO ascending
    out["pre_near"].sort(key=lambda x: (x[1], str(x[0])))
    out["pre_far"].sort(key=lambda x: (x[1], str(x[0])))
    return out


# ---------------------------------------------------------------------------
# Public API (unchanged)
# ---------------------------------------------------------------------------

def scope_snapshot(inplay_window_min: int = 15) -> Dict[str, List[Tuple[str, float]]]:
    now = utc_now()

    # 1) Try project scope first
    sc: Dict[str, List[Tuple[str, float]]]
    try:
        sc = _project_build_scope(now_utc=now, inplay_window_min=inplay_window_min) or {}
    except Exception:
        sc = {}

    # 2) If empty, build minimal union from DBs using your real schema
    if not (sc.get("pre_far") or sc.get("pre_near") or sc.get("in_play")):
        sc = _fallback_scope_union()

    ok = bool(sc.get("pre_far") or sc.get("pre_near") or sc.get("in_play"))
    status_once("scope:build", ok, "empty" if not ok else "")

    return {
        "pre_far":  list(sc.get("pre_far",  [])),
        "pre_near": list(sc.get("pre_near", [])),
        "in_play":  list(sc.get("in_play",  [])),
    }


def seed_scope_window(scope: Dict[str, List[Tuple[str, float]]]) -> None:
    global SCOPE_WINDOW
    if SCOPE_WINDOW:
        return
    near = [m for (m, _t) in scope.get("pre_near", [])]
    far  = [m for (m, _t) in scope.get("pre_far",  [])]
    SCOPE_WINDOW = (near + far)[:SCOPE_WINDOW_MAX]


def prune_and_slide_window(scope: Dict[str, List[Tuple[str, float]]]) -> None:
    global SCOPE_WINDOW
    in_play = {m for (m, _e) in scope.get("in_play", [])}
    near    = [m for (m, _t) in scope.get("pre_near", [])]
    far     = [m for (m, _t) in scope.get("pre_far",  [])]
    sched   = near + far
    SCOPE_WINDOW = [m for m in SCOPE_WINDOW if (m in sched or m in in_play) and is_today_in_scope(m)]
    for m in sched:
        if len(SCOPE_WINDOW) >= SCOPE_WINDOW_MAX:
            break
        if m not in SCOPE_WINDOW and is_today_in_scope(m):
            SCOPE_WINDOW.append(m)


def advance_scope_cursor(step: int = 1, span: int | None = None) -> None:
    global SCOPE_CURSOR
    try:
        n = int(span or 0)
        if n <= 0:
            return
        SCOPE_CURSOR = (int(SCOPE_CURSOR) + int(step)) % n
    except Exception:
        SCOPE_CURSOR = 0


def promote_market_on_signal(market_id: str, ttl_s: int = 120) -> None:
    try:
        SCOPE_OVERRIDES[str(market_id)] = time.time() + max(30, int(ttl_s))
    except Exception:
        pass


def trim_expired_overrides() -> None:
    now = time.time()
    for m, exp in list(SCOPE_OVERRIDES.items()):
        if exp <= now:
            SCOPE_OVERRIDES.pop(m, None)


def ordered_markets_for_tick(scope: Dict[str, List[Tuple[str, float]]]) -> List[str]:
    tto_map = {m: t for (m, t) in (scope.get("pre_near", []) + scope.get("pre_far", []))}
    def _prio_key(m: str) -> tuple[int, float]:
        t = float(tto_map.get(m, 1e9))
        return (0 if t <= 20.0 else 1, t)
    window    = list(SCOPE_WINDOW)
    overrides = [m for m in SCOPE_OVERRIDES.keys() if m not in window]
    ip_list   = [m for (m, _e) in scope.get("in_play", [])]
    ordered = sorted(window, key=_prio_key) + [m for (m, t) in (scope.get("pre_near", []) or []) if float(t) <= 20.0 and m not in window] + overrides + ip_list
    if not ordered:
        return []
    n = len(ordered)
    start_idx = int(SCOPE_CURSOR) % n
    return ordered[start_idx:] + ordered[:start_idx]


def print_scope_dashboard(scope: Dict[str, List[Tuple[str, float]]], inplay_window_min: int = 15) -> None:
    try:
        now_s = int(time.time())
        tto_map = {m: t for (m, t) in (scope.get("pre_near", []) + scope.get("pre_far", []))}
        ip_map  = {m: e for (m, e) in scope.get("in_play", [])}
        win5 = list(SCOPE_WINDOW)
        extras20 = [m for (m, t) in (scope.get("pre_near", []) or []) if float(t) <= 20.0 and m not in win5]
        in_play = [m for (m, _) in scope.get("in_play", [])]
        signals = [m for (m, exp) in SCOPE_OVERRIDES.items() if exp > now_s]
        print(f"[SCOPES] WIN5={len(win5)}  ADD≤20={len(extras20)}  INPLAY={len(in_play)}  SIGNALS={len(signals)}")
        if win5:
            items = ", ".join(f"{m}({int(tto_map.get(m, 999))}m)" for m in win5[:12])
            tail  = "" if len(win5) <= 12 else f" …+{len(win5)-12}"
            print(f"  WIN5: {items}{tail}")
        if extras20:
            items = ", ".join(f"{m}({int(t)}m)" for m in extras20[:12])
            tail  = "" if len(extras20) <= 12 else f" …+{len(extras20)-12}"
            print(f"  ADD≤20: {items}{tail}")
        if in_play:
            items = ", ".join(f"{m}(+{int(ip_map.get(m, 0))}m)" for m in in_play[:12])
            tail  = "" if len(in_play) <= 12 else f" …+{len(in_play)-12}"
            print(f"  INPLAY: {items}{tail}")
        if signals:
            items = ", ".join(f"{m}(+{int(SCOPE_OVERRIDES[m]-now_s)}s)" for m in signals[:12])
            tail  = "" if len(signals) <= 12 else f" …+{len(signals)-12}"
            print(f"  SIGNALS: {items}{tail}")
    except Exception:
        pass


def build_and_maintain_scope(inplay_window_min: int = 15, *, show_dashboard: bool = True) -> Dict[str, List[Tuple[str, float]]]:
    sc = scope_snapshot(inplay_window_min=inplay_window_min)
    trim_expired_overrides()
    seed_scope_window(sc)
    prune_and_slide_window(sc)
    if show_dashboard:
        print_scope_dashboard(sc, inplay_window_min=inplay_window_min)
    return sc
