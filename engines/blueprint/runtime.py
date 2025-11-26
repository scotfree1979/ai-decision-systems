from __future__ import annotations
import os, json, sqlite3, math, functools, time
from statistics import median
from typing import Dict, Any, Optional, Tuple, List
from engines.config_paths import autoscalp_db

from collections import Counter

# global in-memory index
_blueprint_index: dict[str, list] = {}
_observed_chapters: dict[tuple[str,str], list] = {}

def _ensure_blueprint_index() -> dict[str, Any]:
    """
    Ensure _blueprint_index is populated from today's cached JSON.
    """
    global _blueprint_index
    if _blueprint_index:
        return _blueprint_index
    obj = load_blueprints()
    patt = obj.get("pattern_dict_export") or {}
    # normalise patterns into list-of-chapters form
    _blueprint_index = {k: v.get("chapters", []) for k, v in patt.items()} if patt else {}
    if _blueprint_index:
        k0 = next(iter(_blueprint_index.keys()), "").lower()
        if   "drift" in k0: fp = "drift"
        elif "steam" in k0: fp = "steam"
        elif "flat"  in k0: fp = "flat"
        else:               fp = "bp"
        print(f"[blueprints] index ready {len(_blueprint_index)} patterns key={fp}")
    return _blueprint_index


# ------------ Loading ---------------------------------------------------------
@functools.lru_cache(maxsize=2)
def _bp_path_today() -> Optional[str]:
    """Return full path to today's blueprint JSON if present; else None."""
    base = os.path.join(os.path.dirname(autoscalp_db()), "blueprints")
    day = time.strftime("%Y-%m-%d", time.gmtime())
    p = os.path.join(base, f"blueprint_signals_{day}.json")
    return p if os.path.exists(p) else None

_BP_CACHE: Dict[str, Any] = {}

# === PATCH START ===
# 📍 TARGET: engines/blueprint/runtime.py:load_blueprints
# 📆 PATCHED: 2025-10-28Z — hybrid loader (canonical or latest per-surface file)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import glob

def load_blueprints() -> dict:
    """
    Load today's blueprint JSON (once, cached in _BP_CACHE).
    Prefers canonical blueprint_signals_{today}.json;
    falls back to the latest matching blueprint_signals_*_{today}.json.
    """
    global _BP_CACHE
    base = os.path.join(os.path.dirname(autoscalp_db()), "blueprints")
    day = time.strftime("%Y-%m-%d", time.gmtime())

    # canonical path first
    canonical = os.path.join(base, f"blueprint_signals_{day}.json")
    if os.path.exists(canonical):
        latest = canonical
    else:
        # fallback: pick latest matching file for the same day
        pattern = os.path.join(base, f"blueprint_signals_*_{day}.json")
        matches = sorted(glob.glob(pattern))
        latest = max(matches, key=os.path.getmtime) if matches else None

    if not latest:
        print(f"[blueprints] none found for {day} in {base}")
        _BP_CACHE = {"_day": day, "pattern_dict_export": {}, "prefix_index": {}}
        return _BP_CACHE

    try:
        with open(latest, "r", encoding="utf-8") as f:
            obj = json.load(f) or {}
        obj["_day"] = day
        obj["_path"] = latest
        if "pattern_dict_export" not in obj:
            p = obj.get("catalogue") or obj.get("patterns") or {}
            obj["pattern_dict_export"] = p if isinstance(p, dict) else {}
        _BP_CACHE = obj
        print(f"[blueprints] loaded {len(obj.get('pattern_dict_export', {}))} patterns from {os.path.basename(latest)}")
        return _BP_CACHE
    except Exception as e:
        print(f"[blueprints] load warn: {e}")
        _BP_CACHE = {"_day": day, "pattern_dict_export": {}, "prefix_index": {}}
        return _BP_CACHE
# === PATCH END ===


    # Return existing cache if it's already for today's file
    if isinstance(_BP_CACHE, dict) and _BP_CACHE.get("_path") == path:
        return _BP_CACHE

    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f) or {}
        obj["_path"] = path

        # Normalize keys
        # Ensure pattern_dict_export exists
        if "pattern_dict_export" not in obj:
            # Some builds export pure 'catalogue'; fall back if present
            p = obj.get("catalogue") or {}
            obj["pattern_dict_export"] = p if isinstance(p, dict) else {}

        # Ensure prefix_index exists
        if "prefix_index" not in obj:
            patterns = obj.get("pattern_dict_export") or {}
            obj["prefix_index"] = _build_naive_prefix_index(patterns)

        _BP_CACHE = obj
        return obj

    except Exception as e:
        print(f"[blueprints] cache warn: {e}")
        # Keep cache minimal to avoid repeated disk attempts this run
        _BP_CACHE = {"_path": path, "pattern_dict_export": {}, "prefix_index": {}}
        return {}
# === PATCH END ===


# ------------ Minimal naive index (fallback) ----------------------------------
def _build_naive_prefix_index(patterns: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a tiny map for transitions when enriched prefix_index is absent.
    We aggregate direction at start oc -> next oc (t+1) using segment keys 'OCi-OCj'.
    """
    out: Dict[str, Any] = {}
    for pkey, meta in patterns.items():
        # pkey = "drift|steam|flat → OCi-OCj → label → trade_type"
        try:
            parts = pkey.split("→")
            move = parts[0].strip()  # 'drift' / 'steam' / 'flat'
            ocseg = parts[1].strip() # 'OC2-OC3'
            start = int(ocseg.split("-")[0].replace("OC",""))
            end   = int(ocseg.split("-")[1].replace("OC",""))
            if end != start + 1:
                # we only use t -> t+1 for transitions
                continue
            # meta['trades'] is a list with entry/exit; use its len as support
            support = len(meta.get("trades") or [])
            if support <= 0:
                continue
            key = f"PREFIX@OC{start}"  # dummy prefix key by oc number
            slot = out.setdefault(key, {"next": {f"OC{end}": {"D":0,"S":0,"F":0}}, "quality": {"support":0}})
            bucket = slot["next"][f"OC{end}"]
            if move.lower().startswith("drift"):
                bucket["D"] += support
            elif move.lower().startswith("steam"):
                bucket["S"] += support
            else:
                bucket["F"] += support
            slot["quality"]["support"] += support
        except Exception:
            continue
    return out

# === Liability + rank helpers (favourite awareness) ==========================
# === PATCH START ===
# 📍 TARGET: engines/blueprint/runtime.py:_adb_ro
# 🔎 SEARCH: def _adb_ro(
# 📆 PATCHED: 2025-11-21 — DAL RO for autoscalp_gui.db

from engines.config_paths import auto_conn as _auto_conn

def _adb_ro(timeout: float = 6.0) -> sqlite3.Connection:
    """
    DAL-safe read-only connection to autoscalp_gui.db.
    Replaces raw sqlite3.connect(_adb_path()).
    """
    con = _auto_conn(rw=False)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=8000")
        con.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    return con
# === PATCH END ===


def latest_oc1_snapshot(market_id: str) -> dict[str, float]:
    """
    {selectionId: oc1} from inbound_oc_cache (latest row per sid).
    Fallback: empty dict. Safe & fast.
    """
    out: dict[str, float] = {}
    try:
        con = _adb_ro()
        rows = con.execute(
            "SELECT selectionId, oc1 FROM inbound_oc_cache "
            "WHERE marketId=? ORDER BY id DESC", (str(market_id),)
        ).fetchall()
        seen = set()
        for r in rows:
            sid = str(r["selectionId"])
            if sid in seen: 
                continue
            seen.add(sid)
            v = r["oc1"]
            if v is not None:
                out[sid] = float(v)
        con.close()
    except Exception:
        pass
    return out

def current_ranks(market_id: str) -> dict[str, int]:
    """
    Rank by lowest oc1 odds (1 = favourite). Ties break arbitrarily.
    """
    snap = latest_oc1_snapshot(market_id)
    if not snap:
        return {}
    order = sorted(snap.items(), key=lambda kv: kv[1])  # by odds ascending
    return {sid: (i + 1) for i, (sid, _v) in enumerate(order)}

def liability_snapshot(market_id: str) -> dict[str, dict]:
    """
    Return per-runner open exposure from AUTO_DB.orders (LIVE parents still open).
    Schema-agnostic: we look for children via hedge_of/parent_id if present; if not, we
    still sum by side for open parents. Output:
      { sid: { 'lay_liab': float, 'back_at_risk': float, 'open_parents': int } }
    """
    out: dict[str, dict] = {}
    try:
        con = _adb_ro()
        cols = [r["name"] for r in con.execute("PRAGMA table_info(orders)")]
        link = "hedge_of" if "hedge_of" in cols else ("parent_id" if "parent_id" in cols else None)

        # Open LIVE parents (no exit yet)
        rows = con.execute(
            "SELECT selectionId, side, entry_odds, entry_stake "
            "FROM orders "
            "WHERE mode='LIVE' "
            "AND (closed_at IS NULL OR closed_at='') "
            + ("" if link is None else f"AND ({link} IS NULL OR {link}='')")
            + " AND marketId=?",
            (str(market_id),)
        ).fetchall()
        for r in rows:
            sid = str(r["selectionId"])
            side = str(r["side"] or "").upper()
            odds = float(r["entry_odds"] or 0.0)
            stake = float(r["entry_stake"] or 0.0)
            rec = out.setdefault(sid, {"lay_liab": 0.0, "back_at_risk": 0.0, "open_parents": 0})
            rec["open_parents"] += 1
            if side == "LAY":
                rec["lay_liab"] += max(0.0, (odds - 1.0) * stake)
            elif side == "BACK":
                # for back, stake is at risk (approx; scalper logic settles on exit)
                rec["back_at_risk"] += max(0.0, stake)
        con.close()
    except Exception:
        pass
    return out

def adverse_ticks(entry_odds: float, current_odds: float, side: str) -> int:
    """
    Positive number of ticks against our entry:
      - For LAY, steam (down) is adverse → entry - current
      - For BACK, drift (up) is adverse → current - entry
    """
    try:
        # use your tick math if available; else 0.01 grid fallbacks
        from engines.price_math import tick_diff
        diff = int(tick_diff(current_odds, entry_odds)) if side.upper() == "LAY" else int(tick_diff(entry_odds, current_odds))
        return max(0, diff)
    except Exception:
        step = 0.01
        if side.upper() == "LAY":
            return max(0, int(round((entry_odds - current_odds) / step)))
        return max(0, int(round((current_odds - entry_odds) / step)))

def hedge_plan_for_liability(side: str,
                             current_odds: float,
                             liab_pounds: float,
                             tgt_ticks: int = 1) -> dict:
    """
    Build a simple reduction plan:
      - If we hold LAY liability → place BACK parent (BACK->LAY 1 tick)
      - If we hold BACK stake risk → place LAY parent (LAY->BACK 1 tick)
    Size decision is done by strategy’s dynamic sizing layer; here we just shape direction.
    """
    side = (side or "").upper()
    if side == "LAY":     # reduce LAY exposure with BACK entry
        return {"enter": True, "direction": "BACK->LAY", "target_ticks": max(1, int(tgt_ticks)), "why": f"liab_reduction lay={liab_pounds:.2f}"}
    # else BACK risk → LAY entry
    return {"enter": True, "direction": "LAY->BACK", "target_ticks": max(1, int(tgt_ticks)), "why": f"liab_reduction back={liab_pounds:.2f}"}
# ============================================================================


# ------------ OC / Bands read -------------------------------------------------
# === PATCH START ===
# 📍 TARGET: engines/blueprint/runtime.py:_read_inbound_row
# 🔎 SEARCH: def _read_inbound_row(
# 📆 PATCHED: 2025-11-21 — DAL RO reader for inbound_oc_cache

from engines.config_paths import auto_conn as _auto_conn

def _read_inbound_row(mid: str, sid: str) -> Optional[sqlite3.Row]:
    """
    Read the latest inbound_oc_cache row for (marketId, selectionId)
    using a DAL-safe read-only AUTO DB connection.
    """
    con = _auto_conn(rw=False)
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT * FROM inbound_oc_cache "
            "WHERE marketId=? AND selectionId=? "
            "ORDER BY id DESC LIMIT 1",
            (str(mid), str(sid))
        ).fetchone()
    finally:
        try:
            con.close()
        except Exception:
            pass
# === PATCH END ===


def _parse_band(bj: Optional[str]) -> List[float]:
    if not bj:
        return []
    try:
        obj = json.loads(bj)
        if not isinstance(obj, list):
            return []
        out: List[float] = []
        for x in obj:
            try:
                out.append(float(x))
            except Exception:
                pass
        return out
    except Exception:
        return []

def oc_presence(mid: str, sid: str) -> int:
    """Return highest OC number present (value not null) for this runner; 0 if none."""
    r = _read_inbound_row(mid, sid)
    if not r:
        return 0
    keys = set(r.keys())
    for n in range(20, 0, -1):
        if f"oc{n}" in keys and r[f"oc{n}"] is not None:
            return n
    return 0

def median_series_from_bands(mid: str, sid: str) -> Tuple[Optional[float], Dict[str, float]]:
    """
    Returns (anchor, medians_by_OCn). Anchor from inbound row 'anchor_odd' if present.
    For each OCn, take median of band samples when available; else use ocn value.
    """
    r = _read_inbound_row(mid, sid)
    if not r:
        return (None, {})
    anchor = None
    try:
        if "anchor_odd" in r.keys() and r["anchor_odd"] is not None:
            anchor = float(r["anchor_odd"])
    except Exception:
        anchor = None
    out: Dict[str, float] = {}
    keys = set(r.keys())
    for n in range(1, 21):
        v = float(r[f"oc{n}"]) if f"oc{n}" in keys and r[f"oc{n}"] is not None else None
        band = _parse_band(r[f"oc{n}_band_json"]) if f"oc{n}_band_json" in keys else []
        if band:
            try:
                out[f"OC{n}"] = float(median(band))
            except Exception:
                if v is not None:
                    out[f"OC{n}"] = v
        elif v is not None:
            out[f"OC{n}"] = v
    return (anchor, out)

# ------------ Prefix build & match -------------------------------------------
def _tick_diff(a: float, b: float) -> int:
    # linearized tick map (coarse); can be upgraded to your exact price_math
    step = 0.01 if a < 2 else 0.02 if a < 3 else 0.05 if a < 4 else 0.10 if a < 6 else \
           0.20 if a < 10 else 0.50 if a < 20 else 1.00 if a < 30 else 2.00 if a < 50 else 5.00
    try:
        return int(round((b - a) / step))
    except Exception:
        return 0

def _bucket_symbol(anchor: float, px: float) -> str:
    d = _tick_diff(anchor, px)
    if d >= 3:  return "D"  # drift (odds ↑)
    if d <= -3: return "S"  # steam (odds ↓)
    return "F"

def build_prefix(anchor: float, medians: Dict[str, float], upto_n: int) -> str:
    """
    Build a compact prefix like 'OC1:D,OC2:S,OC3:F' up to OCn.
    """
    labs = []
    for n in range(1, upto_n + 1):
        lab = f"OC{n}"
        if lab not in medians:
            break
        sym = _bucket_symbol(anchor, medians[lab])
        labs.append(f"{lab}:{sym}")
    return ",".join(labs)

def match_transition(mid: str, sid: str, t: int) -> Optional[Dict[str, Any]]:
    """
    Match observed OC chapters against known blueprints.
    Returns posterior distribution over next chapter (drift/steam/flat).
    """
    try:
        # ensure index from cached JSON
        idx = _ensure_blueprint_index()
        if not idx:
            return None

        # observed sequence so far
        prefix = _observed_chapters.get((mid, sid), [])
        if len(prefix) < 2:
            return None

        # candidate blueprints from cached index
        candidates = []
        for bp_key, chs in idx.items():
            if chs[:len(prefix)] == prefix:
                candidates.append(chs)

        if not candidates:
            return None

        # count next transitions
        from collections import Counter
        next_moves = []
        for chs in candidates:
            if len(chs) > len(prefix):
                next_moves.append(chs[len(prefix)])
        dist = Counter(next_moves)

        if not dist:
            return None

        total = sum(dist.values())
        probs = {
            "D": dist.get("drift", 0) / total,
            "S": dist.get("steam", 0) / total,
            "F": dist.get("flat",  0) / total,
        }
        return {"probs": probs, "candidates": candidates[:5]}

    except Exception:
        return None

# ------------ Plan from transition -------------------------------------------
def plan_from_transition(tr: Dict[str, Any], anchor: float, price_now: float, size_cap: float) -> Optional[Dict[str, Any]]:
    """
    Convert a transition posterior into a scalp plan dict the orchestrator understands.
    """
    if not tr: return None
    probs = tr.get("probs") or {}
    pD = float(probs.get("D", 0.0))  # drift => LAY->BACK
    pS = float(probs.get("S", 0.0))  # steam => BACK->LAY
    # direction by higher posterior; require minimum confidence gap
    if max(pD, pS) < 0.55:
        return None
    direction = "LAY->BACK" if pD >= pS else "BACK->LAY"
    # target ticks by confidence (1..4)
    conf = max(pD, pS)
    tgt = 1 if conf < 0.62 else 2 if conf < 0.70 else 3 if conf < 0.78 else 4
    size = max(2.0, min(float(size_cap or 2.0), 5.0)) * (0.75 + 0.5*(conf-0.55))
    why = f"BP {tr.get('next_label','OC?')} probs={probs}"
    return {"enter": True, "direction": direction, "target_ticks": int(tgt), "size": float(size), "why": why}
