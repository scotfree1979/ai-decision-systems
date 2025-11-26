# engines/decision_engine/decide_once/helpers.py
from __future__ import annotations

import time as _time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ── time / health ────────────────────────────────────────────────────────────
def utc_now() -> datetime:
    try:
        return datetime.now(timezone.utc)
    except Exception:
        return datetime.utcnow().replace(tzinfo=timezone.utc)

def minute_bucket(ts: Optional[float] = None) -> int:
    try:
        return int((ts if ts is not None else _time.time()) // 60)
    except Exception:
        return 0

def status_once(key: str, ok: bool, detail: str = "") -> None:
    """Print once when state flips (OK/FAIL)."""
    try:
        _state = status_once.__dict__.setdefault("_S", {})  # type: ignore[attr-defined]
        cur = (bool(ok), str(detail or ""))
        if _state.get(key) != cur:
            print(f"[HEALTH] {key} => {'OK' if ok else 'FAIL'}{(' — ' + cur[1]) if cur[1] else ''}")
            _state[key] = cur
    except Exception:
        pass

# ── DB / config ──────────────────────────────────────────────────────────────
def tbl_exists(con, table: str) -> bool:
    try:
        r = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        return bool(r)
    except Exception:
        return False

def open_auto_db(ro: bool = True):
    """Best-effort connector; returns sqlite3.Connection or None."""
    try:
        from engines.config_paths import autoscalp_db  # type: ignore
        import sqlite3
        flags = {"check_same_thread": False}
        uri = f"file:{autoscalp_db()}?mode={'ro' if ro else 'rw'}&cache=shared"
        return sqlite3.connect(uri, uri=True, **flags)
    except Exception:
        return None

# Back-compat alias some code uses
auto_conn = open_auto_db

# ── schedule / scope adapters ────────────────────────────────────────────────
def is_today_in_scope(market_id: str) -> bool:
    try:
        from engines.decision_engine.scope_helpers import is_today_in_scope as _impl  # type: ignore
        return bool(_impl(market_id))
    except Exception:
        return True  # fail-open; resolver/health will reveal wiring

def build_scope(now_utc: Optional[datetime] = None, inplay_window_min: int = 15) -> Dict[str, List[Tuple[str, float]]]:
    try:
        from engines.decision_engine.scope_helpers import build_scope as _impl  # type: ignore
        return _impl(now_utc=now_utc or utc_now(), inplay_window_min=inplay_window_min) or {}
    except Exception:
        return {"pre_far": [], "pre_near": [], "in_play": []}

# Optional windows (kept for back-compat)
def in_pre_window(mto_minutes: float) -> bool:
    try:
        return float(mto_minutes) > 0.0
    except Exception:
        return False

def in_ip_window(mto_minutes: float) -> bool:
    try:
        return float(mto_minutes) <= 0.0
    except Exception:
        return False

# ── prices / odds ────────────────────────────────────────────────────────────
def latest_price(market_id: str, selection_id: str) -> Optional[float]:
    try:
        from engines.decision_engine.price import latest_price as _impl  # type: ignore
        return _impl(market_id, selection_id)
    except Exception:
        return None

def latest_prices_for_market(market_id: str) -> Dict[str, float]:
    try:
        from engines.decision_engine.price import latest_prices_for_market as _impl  # type: ignore
        return dict(_impl(market_id) or {})
    except Exception:
        return {}

def _runner_activity(market_id: str, selection_id: str) -> str:
    try:
        from engines.decision_engine.activity import runner_activity  # type: ignore
        return str(runner_activity(market_id, selection_id) or "")
    except Exception:
        return "active"  # fail-open

# Odds math
def tick_size(o: float) -> float:
    try:
        if o < 2.0: return 0.01
        if o < 3.0: return 0.02
        if o < 4.0: return 0.05
        if o < 6.0: return 0.1
        if o < 10.0: return 0.2
        if o < 20.0: return 0.5
        if o < 30.0: return 1.0
        if o < 50.0: return 2.0
        return 5.0
    except Exception:
        return 0.01

def odds_plus_ticks(o: float, ticks: int) -> float:
    try:
        px = float(o); step = tick_size(px)
        return round(px + (step * int(ticks)), 2)
    except Exception:
        return o

# ── rulebook / caps / tags ───────────────────────────────────────────────────
def rulebook_allow(letter: str, pass_n: int, ctx: dict, plan: dict) -> Tuple[bool, Optional[dict]]:
    try:
        from engines.decision_engine.rulebook import rulebook_allow as _impl  # type: ignore
        return _impl(letter, pass_n, ctx, plan)
    except Exception:
        return True, plan

def apply_rulebook(letter: str, tag: str, ctx: dict, plan: dict) -> Tuple[bool, dict]:
    try:
        from engines.decision_engine.rulebook import apply_rulebook as _impl  # type: ignore
        return _impl(letter, tag, ctx, plan)
    except Exception:
        return False, plan

def can_open_scalp(market_id: str, selection_id: str, *, max_per_runner: int, run_id: str, family_letter: str) -> Tuple[bool, str]:
    try:
        from engines.decision_engine.risk import can_open_scalp as _impl  # type: ignore
        return _impl(market_id, selection_id, max_per_runner=max_per_runner, run_id=run_id, family_letter=family_letter)
    except Exception:
        return True, "fallback"

def next_pair_tag(letter: str, market_id: str) -> str:
    return f"{letter}:{market_id}:{minute_bucket()}"

def pass_tag(letter: str, mto: float) -> str:
    return f"{letter}:{int(mto)}:{minute_bucket()}"

def already_open_pass(market_id: str, selection_id: str, tag: str, *, mode: str) -> bool:
    try:
        from engines.decision_engine.pass_tags import already_open_pass as _impl  # type: ignore
        return bool(_impl(market_id, selection_id, tag, mode=mode))
    except Exception:
        return False

# ── orders / placement adapters ──────────────────────────────────────────────
def queue_order(plan: dict, ctx: dict) -> Optional[int]:
    try:
        from engines.decision_engine.placement import queue_order as _impl  # type: ignore
        return _impl(plan, ctx)
    except Exception:
        return None

def place_decision(name: str, plan: dict, ctx: dict) -> Optional[int]:
    try:
        from engines.decision_engine.placement import place_decision as _impl  # type: ignore
        return _impl(name, plan, ctx)
    except Exception:
        return None

def place_companion_hedge(parent_id: int, plan: dict, ctx: dict) -> Optional[int]:
    try:
        from engines.decision_engine.placement import place_companion_hedge as _impl  # type: ignore
        return _impl(parent_id, plan, ctx)
    except Exception:
        return None

def set_order_status(order_id: int, status: str, reason: str = "") -> None:
    try:
        from engines.decision_engine.placement import set_order_status as _impl  # type: ignore
        return _impl(order_id, status, reason)
    except Exception:
        return None

def mark_last_child_placed(parent_id: int) -> None:
    try:
        from engines.decision_engine.placement import mark_last_child_placed as _impl  # type: ignore
        return _impl(parent_id)
    except Exception:
        return None

# ── candidates (public + internal, both supported) ───────────────────────────
def _is_active_pair(mid: str, sid: str, o: float) -> bool:
    try:
        if not (1.5 <= float(o) <= 8.0):
            return False
    except Exception:
        return False
    try:
        return _runner_activity(mid, sid) == "active"
    except Exception:
        return False

def _active_candidates_for_market(mid: str, max_runners: int = 8) -> List[Tuple[str, float]]:
    """Internal engine version (RR, robust fallbacks)."""
    odds = latest_prices_for_market(mid)
    if not odds:
        status_once("candidates:odds", False, "no odds map")
        return []

    candidates = [(sid, px) for sid, px in odds.items() if _is_active_pair(mid, sid, px)]
    if not candidates:
        return []

    try:
        rr_key = "_RR_CURSOR"; order_key = "_RR_ORDER"
        cursors = globals().setdefault(rr_key, {})
        orders  = globals().setdefault(order_key, {})

        cur_ids = [sid for (sid, _px) in sorted(candidates, key=lambda t: (float(t[1]), str(t[0])))]
        prev_order = list(orders.get(mid, []))
        if prev_order != cur_ids:
            orders[mid] = cur_ids
            cur = int(cursors.get(mid, 0)) if mid in cursors else 0
            cursors[mid] = (cur % max(1, len(cur_ids)))

        cur = int(cursors.get(mid, 0))
        rot_ids   = (orders[mid][cur:] + orders[mid][:cur]) if orders.get(mid) else cur_ids
        rot_pairs = [(sid, odds.get(sid)) for sid in rot_ids if sid in odds]

        if orders.get(mid):
            cursors[mid] = (cur + max(1, max_runners)) % len(orders[mid])

        out: List[Tuple[str, float]] = []
        seen: set[str] = set()
        for sid, px in rot_pairs:
            if sid in seen:
                continue
            seen.add(sid)
            try:
                o = float(px)
            except Exception:
                continue
            out.append((sid, o))
            if len(out) >= max_runners:
                break
        return out
    except Exception:
        sorted_pairs = sorted(candidates, key=lambda t: (float(t[1]), str(t[0])))
        return sorted_pairs[:max_runners]

def active_candidates_for_market(mid: str, max_runners: int = 8) -> List[Tuple[str, float]]:
    """Public API kept for back-compat: delegates to internal version."""
    return _active_candidates_for_market(mid, max_runners=max_runners)

# ── gate metrics (safe no-ops unless your telemetry wires them) ──────────────
def gate_bump(*_a, **_k):  # no-op by default
    return None

def gate_snapshot() -> dict:
    return {}

# ── misc legacy aliases so old code keeps working ────────────────────────────
_q_retry = lambda *a, **k: None
log_event_once = status_once
_utcnow = utc_now
_latest_price = latest_price
_open_adb = open_auto_db
_is_today_in_scope = is_today_in_scope
_build_scope = build_scope
_active_candidates_for_market_alias = _active_candidates_for_market
_apply_rulebook = apply_rulebook
_can_open_scalp = can_open_scalp
_next_pair_tag = next_pair_tag
_pass_tag = pass_tag
_already_open_pass = already_open_pass
_queue_order = queue_order
_place_decision = place_decision
_place_companion_hedge = place_companion_hedge
_set_order_status = set_order_status
_mark_last_child_placed = mark_last_child_placed
_tick_size = tick_size
_odds_plus_ticks = odds_plus_ticks
