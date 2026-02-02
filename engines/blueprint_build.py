
"""
🧫 Enhanced Blueprint Engine (Trend-Following Scalping Profiler + Profit Match + Pattern Ranking)
"""
from __future__ import annotations
PARSE_FAILURES = set()

import sys
import os
import sqlite3
import json
from datetime import date, datetime, timedelta
from collections import defaultdict, Counter
import ast
import logging

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config_paths import autoscalp_db as _adb_path, DB_PATH  # we need both
# Export folder (we only create it here; we write at the end once we have data)
_OUTDIR = os.path.join(os.path.dirname(_adb_path()), "blueprints")
os.makedirs(_OUTDIR, exist_ok=True)




# ============================================================================
from price_math import calculate_tick_distance
from blueprint_csv_loader import get_matched_bet_ids_for_runner
from blueprint_loader import get_known_blueprint_patterns

# === PATCH 1: story series loader (snapshots -> oc_series.odd) ===============
# REMOVE this legacy import:
# from blueprint_engine_story_builder import get_story_chapters

def get_story_chapters(market_id: str, selection_id: int, max_points: int = 240):
    """
    Chronological [{'ts': iso, 'odds': float}] from:
      1) autoscalp_gui.odds_snapshots (ltp)
      2) oc_series.odd (same DB if present, else bets DB_PATH)
    """
    def _load(db_path: str):
        try:
            with sqlite3.connect(db_path, timeout=8) as con:
                con.row_factory = sqlite3.Row
                # 1) snapshots (preferred)
                try:
                 
                    rows = con.execute(
                        "SELECT ts, ltp FROM odds_snapshots "
                        "WHERE marketId=? AND selectionId=? "
                        "ORDER BY ts ASC LIMIT ?",
                        (market_id, selection_id, max_points)
                    ).fetchall()
                    out = [{"ts": str(r["ts"]), "odds": float(r["ltp"])} for r in rows if r["ltp"] is not None]
                    if out: return out
                except Exception:
                    pass
                # 2) oc_series (odd)
                rows = con.execute(
                    "SELECT snapshot_ts AS ts, odd FROM oc_series "
                    "WHERE marketId=? AND selectionId=? "
                    "ORDER BY snapshot_ts ASC LIMIT ?",
                    (market_id, selection_id, max_points)
                ).fetchall()
                return [{"ts": str(r["ts"]), "odds": float(r["odd"])} for r in rows if r["odd"] is not None]
        except Exception:
            return []
    # prefer autoscalp_gui.db, fallback to bets DB_PATH
    return _load(_adb_path()) or _load(DB_PATH)

# === PATCH START ===
# 📍 TARGET: engines/blueprint_build.py:_surface_distance_key
# 📆 PATCHED: 2025-10-18T23:59Z — final unified classifier (distance + surface + race type)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _surface_distance_key(market_name: str) -> str:
    """
    Unified classifier for race segmentation.
    Reads market_name directly (e.g. '2m Nov Hrd', '7f Hcap', '3m Hcap Chs')
    and returns '<surface>_<distance>_<race_type>' for blueprint grouping.
    """
    import re
    n = (market_name or "").lower().strip()

    # --- Surface ---
    surface = "jumps" if any(x in n for x in ("hurdle", "hrd", "chase", "chs", "nhf")) else "flat"

    # --- Distance ---
    distance = "unknown"
    m = re.search(r"(\d+m\d*f|\d+m|\d+f)", n)
    d = m.group(1) if m else ""
    if any(x in d for x in ("5f", "6f", "7f")):
        distance = "sprint"
    elif any(x in d for x in ("1m", "8f", "9f", "10f", "11f", "12f", "13f", "14f")):
        distance = "middle"
    elif any(x in d for x in ("1m6f", "2m", "2m1f", "2m2f")):
        distance = "stayer"
    elif any(x in d for x in ("3m", "4m")):
        distance = "long_stayer"

    # --- Race Type ---
    if "hcap" in n:
        race_type = "handicap"
    elif "nursery" in n:
        race_type = "nursery"
    elif "mdn" in n:
        race_type = "maiden"
    elif "nov" in n:
        race_type = "novice"
    elif "listed" in n:
        race_type = "listed"
    else:
        race_type = "other"

    return f"{surface}_{distance}_{race_type}"
# === PATCH END ===




# === PATCH START ===
# 📍 TARGET: engines/blueprint_build.py
# ⛏️ ACTION: add connection-aware fast paths and hardened chapter loader

def _fetch_oc_lanes_with_con(con_auto: sqlite3.Connection, market_id: str, selection_id: int) -> dict:
    out = {}
    try:
        con_auto.row_factory = sqlite3.Row
        row = con_auto.execute(
            "SELECT * FROM inbound_oc_cache WHERE marketId=? AND selectionId=? ORDER BY id DESC LIMIT 1",
            (market_id, selection_id)
        ).fetchone()
        if not row:
            return out
        keys = set(row.keys())
        for n in range(1, 21):
            v  = row[f"oc{n}"] if f"oc{n}" in keys else None
            bj = row[f"oc{n}_band_json"] if f"oc{n}_band_json" in keys else None
            band = None
            if bj:
                try:
                    parsed = json.loads(bj)
                    if isinstance(parsed, list) and parsed:
                        band = [float(x) for x in parsed if _is_float(x)]
                except Exception:
                    band = None
            out[f"OC{n}"] = {"value": float(v) if _is_float(v) else None, "band": band}
    except Exception:
        return out
    return out

def get_story_chapters_with_con(con_auto: sqlite3.Connection, con_bets: sqlite3.Connection,
                                market_id: str, selection_id: int, max_points: int = 240) -> list[dict]:
    """
    Chronological [{'ts': iso, 'odds': float}] from:
      1) autoscalp_gui.odds_snapshots (ltp)
      2) bets.oc_series (odd) [snapshot_ts asc]
    Returns a list (never None).
    """
    # 1) snapshots
    try:
        con_auto.row_factory = sqlite3.Row
        rows = con_auto.execute(
            "SELECT ts, ltp FROM odds_snapshots "
            "WHERE marketId=? AND selectionId=? "
            "ORDER BY ts ASC LIMIT ?",
            (market_id, selection_id, max_points)
        ).fetchall()
        out = [{"ts": str(r["ts"]), "odds": float(r["ltp"])} for r in rows if r["ltp"] is not None]
        if out:
            return out
    except Exception:
        pass
    # 2) oc_series from BETS
    try:
        con_bets.row_factory = sqlite3.Row
        rows = con_bets.execute(
            "SELECT snapshot_ts AS ts, odd FROM oc_series "
            "WHERE marketId=? AND selectionId=? "
            "ORDER BY snapshot_ts ASC LIMIT ?",
            (market_id, selection_id, max_points)
        ).fetchall()
        return [{"ts": str(r["ts"]), "odds": float(r["odd"])} for r in rows if r["odd"] is not None]
    except Exception:
        return []
# === PATCH END ===

# === PATCH 2: OC lanes + bands reader =======================================
def _fetch_oc_lanes(market_id: str, selection_id: int) -> dict:
    """
    Return {'OCn': {'value': float|None, 'band': [floats]|None}, ...} for n=1..20.
    Reads autoscalp_gui.inbound_oc_cache. Missing -> empty.
    """
    out = {}
    try:
        with sqlite3.connect(_adb_path(), timeout=8) as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT * FROM inbound_oc_cache WHERE marketId=? AND selectionId=? ORDER BY id DESC LIMIT 1",
                (market_id, selection_id)
            ).fetchone()
            if not row:
                return out
        
            # columns exist in your schema; if some builds miss higher OCn, guard per key
            keys = set(row.keys())
            for n in range(1, 21):
                v  = row[f"oc{n}"] if f"oc{n}" in keys else None
                bj = row[f"oc{n}_band_json"] if f"oc{n}_band_json" in keys else None
                band = None
                if bj:
                    try:
                        parsed = json.loads(bj)
                        if isinstance(parsed, list) and parsed:
                            band = [float(x) for x in parsed if _is_float(x)]
                    except Exception:
                        band = None
                out[f"OC{n}"] = {"value": float(v) if _is_float(v) else None, "band": band}


    except Exception:
        return out
    return out

def _is_float(x) -> bool:
    try:
        float(x)
        return True
    except Exception:
        return False
# ============================================================================
# === PATCH 3: chapter assembly from OC bands + tape ==========================
def _chapter_series(oc_dict: dict, story_series: list[dict]) -> dict[str, list[float]]:
    """
    Return {'OCn': [samples...]} for chapters we can build.
    Prefers ocN_band_json samples; else uses ocN value; else pulls from story_series window best-effort.
    """
    # map OCn -> list of floats
    out: dict[str, list[float]] = {}
    # 1) use band samples when they look like sample arrays (len >= 3)
    for n in range(1, 21):
        lab = f"OC{n}"
        info = oc_dict.get(lab) or {}
        band = info.get("band")
        val  = info.get("value")
        if isinstance(band, list) and len(band) >= 3:
            # band may be [lo, mid, hi] OR a growing sample list (your writer appends)
            # if it's a triple, we still use it (coarse), otherwise use as-is.
            out[lab] = [float(x) for x in band if _is_float(x)]
        elif _is_float(val):
            out[lab] = [float(val)]
        # else missing chapter → we might fill from story_series below

    if story_series:
        # simple fallback: if a chapter lacks samples, inject the nearest tape price
        last_px = None
        for point in story_series:
            px = float(point["odds"])
            last_px = px
        # if we have a final tape price but some chapters are empty, seed them
        if last_px is not None:
            for n in range(1, 21):
                lab = f"OC{n}"
                if lab not in out:
                    out[lab] = [last_px]

    return out
# ============================================================================

# === PATCH 0: run once per day + optional JSON ===============================
# place near top, after: from config_paths import DB_PATH

from datetime import date

def _already_ran_today() -> bool:
    key = f"blueprints_ran_{date.today().isoformat()}"
    try:
        with sqlite3.connect(_adb_path(), timeout=8) as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA busy_timeout=8000")
            con.execute("CREATE TABLE IF NOT EXISTS app_kv(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
            row = con.execute("SELECT value FROM app_kv WHERE key=?", (key,)).fetchone()
            return bool(row)
    except Exception:
        return False

def _mark_ran_today() -> None:
    key = f"blueprints_ran_{date.today().isoformat()}"
    try:
        with sqlite3.connect(_adb_path(), timeout=8) as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA busy_timeout=8000")
            con.execute("CREATE TABLE IF NOT EXISTS app_kv(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
            con.execute("INSERT OR REPLACE INTO app_kv(key,value,updated_at) VALUES(?, '1', datetime('now','utc'))", (key,))
            con.commit()
    except Exception:
        pass

_FORCE = any(a in ("--force","-f") for a in sys.argv)

# ============================================================================ 

# === PATCH 4: microstructure-based classification ===========================
def _classify_chapter(samples: list[float], anchor: float) -> str:
    """
    drift: sustained upward (price ↑) with net move ≥ T drift ticks
    steam: sustained downward (price ↓) with net move ≥ T steam ticks
    pingpong: high alternation & vol with small net move
    flat: otherwise
    """
    if not samples or len(samples) < 2:
        return "flat"

    # net movement vs anchor in ticks (positive => drift, negative => steam)
    try:
        net_ticks = calculate_tick_distance(anchor, samples[-1])
    except Exception:
        net_ticks = (samples[-1] - anchor)

    # slope of samples
    deltas = [samples[i+1] - samples[i] for i in range(len(samples)-1)]
    up_moves   = sum(1 for d in deltas if d > 0)
    down_moves = sum(1 for d in deltas if d < 0)
    alt_count  = sum(1 for i in range(len(deltas)-1) if deltas[i] == 0 or (deltas[i] > 0) != (deltas[i+1] > 0))
    alt_ratio  = (alt_count / max(1, len(deltas))) if deltas else 0.0

    # dispersion as a proxy for volatility
    try:
        from statistics import pstdev
        vol = float(pstdev(samples)) if len(samples) >= 3 else 0.0
    except Exception:
        vol = 0.0

    # thresholds (tunable)
    DRIFT_TICKS = 3
    STEAM_TICKS = -3
    PING_ALT    = 0.5
    PING_VOL    = 0.15  # absolute odds units; adjust later per bucket

    # classify
    if net_ticks >= DRIFT_TICKS and up_moves >= down_moves:
        return "drift"
    if net_ticks <= STEAM_TICKS and down_moves >= up_moves:
        return "steam"
    if abs(net_ticks) < 2 and alt_ratio >= PING_ALT and vol >= PING_VOL:
        return "pingpong"
    return "flat"
# ============================================================================


# Settings
STOP_THRESHOLD = 3
TICK_SIZES = [
    (1.01, 2.0, 0.01), (2.0, 3.0, 0.02), (3.0, 4.0, 0.05), (4.0, 6.0, 0.1),
    (6.0, 10.0, 0.2), (10.0, 20.0, 0.5), (20.0, 30.0, 1.0), (30.0, 50.0, 2.0),
    (50.0, 100.0, 5.0), (100.0, 1000.0, 10.0)
]

def round_to_nearest_tick(odds):
    for lower, upper, tick in TICK_SIZES:
        if lower <= odds < upper:
            return round(round((odds - lower) / tick) * tick + lower, 2)
    return round(odds, 2)


def map_oc_to_phase(oc_range):
    try:
        start = int(oc_range.split("-")[0].replace("OC", ""))
        if start == 0: return "Ki"
        elif start == 1: return "Shō"
        elif 2 <= start <= 3: return "Ten"
        else: return "Ketsu"
    except:
        return "unknown"

# === PATCH START ===
# 📍 TARGET: engines/blueprint_build.py
# 🔎 SEARCH: ^runner_trades = \[\][\s\S]*?# Also export to data/blueprints and mark as ran[\s\S]*?print\("✅ Blueprint build complete."\)
# ⛏️ ACTION: wrap the entire builder body into a function (no execution at import)

def _run_builder() -> None:
    # --- BEGIN moved body ---
    runner_trades = []

# === PATCH START ===
# 📍 TARGET: engines/blueprint_build.py
# 🔎 SEARCH: ^\s*KNOWN_BP\s*=\s*get_known_blueprint_patterns\(\)\s*$
# ⛏️ ACTION: normalize loader output to a set of keys so membership is always safe

    KNOWN_BP_RAW = get_known_blueprint_patterns()
    if isinstance(KNOWN_BP_RAW, dict):
        KNOWN_BP = set(KNOWN_BP_RAW.keys())
    elif isinstance(KNOWN_BP_RAW, (list, tuple, set)):
        # If list/tuple of dicts with pattern_key, extract; else cast to set
        try:
            keys = {it.get("pattern_key") for it in KNOWN_BP_RAW if isinstance(it, dict) and it.get("pattern_key")}
            KNOWN_BP = keys or set(KNOWN_BP_RAW)
        except Exception:
            KNOWN_BP = set()
    else:
        KNOWN_BP = set()
# === PATCH END ===


    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT marketId, selectionId, anchor_odd, horse_name, meta_json, placed_at
                FROM bets
                WHERE anchor_odd IS NOT NULL
            """)
            rows = cursor.fetchall()
    except Exception as e:
        print(f"❌ DB error: {e}")
        rows = []

    USE_NEW_OC_SNAPSHOT_LOGIC = True  # enable new path by default

    # --- OPEN two connections ONCE for the whole build ---
    con_auto = sqlite3.connect(_adb_path(), timeout=8)
    con_bets = sqlite3.connect(DB_PATH, timeout=8)
    try:
        for market_id, selection_id, anchor_odd, horse_name, meta_json, placed_at in rows:
            try:
                runner_name = horse_name or f"Runner_{selection_id}"

                # --- classify surface + distance for segmentation ---
                try:
                    bcon = sqlite3.connect(DB_PATH); bcon.row_factory = sqlite3.Row
                    info = bcon.execute(
                        "SELECT market_name FROM bets WHERE marketId=? LIMIT 1",
                        (market_id,)
                    ).fetchone()
                    market_name = info["market_name"] if info else ""
                    bcon.close()
                except Exception:
                    market_name = ""

                surface_key = _surface_distance_key(market_name)



                anchor = round_to_nearest_tick(float(anchor_odd)) if anchor_odd is not None else None
                if anchor is None:
                    continue

                if anchor is None:
                    continue  # need an anchor to score deltas

                # 1) lanes & bands (reuse AUTO connection)
                oc_map = _fetch_oc_lanes_with_con(con_auto, market_id, selection_id)

                # 2) tape (reuse AUTO & BETS connections)
                chapters = get_story_chapters_with_con(con_auto, con_bets, market_id, selection_id) or []
                if not isinstance(chapters, list):
                    chapters = []
                else:
                    chapters = [c for c in chapters if isinstance(c, dict) and "odds" in c and _is_float(c["odds"])]

                # 3) per-chapter samples
                chapter_samples = _chapter_series(oc_map, chapters)

                # 4) classify each chapter and assemble trades across OC ranges
                oc_labels = [f"OC{n}" for n in range(1, 21) if chapter_samples.get(f"OC{n}")]
                if len(oc_labels) < 1:
                    continue
                oc_labels.sort(key=lambda s: int(s.replace("OC", "")))

                # representative value per chapter
                rep_series = []
                for lab in oc_labels:
                    samples = chapter_samples.get(lab, [])
                    if samples:
                        try:
                            from statistics import median
                            rep = float(median(samples))
                        except Exception:
                            rep = float(samples[-1])
                        rep_series.append((lab, rep))
                if len(rep_series) < 2:
                    continue

                # generate trades across representative series
                for i in range(len(rep_series) - 1):
                    for j in range(i + 1, len(rep_series)):
                        (lab_i, px_i), (lab_j, px_j) = rep_series[i], rep_series[j]
                        seg_labels = f"{lab_i}-{lab_j}"
                        # union of samples across the segment
                        seg_samples = []
                        for k in range(int(lab_i.replace("OC", "")), int(lab_j.replace("OC", "")) + 1):
                            seg_samples += chapter_samples.get(f"OC{k}", [])
                        seg_samples = [float(x) for x in seg_samples if _is_float(x)]
                        seg_samples = seg_samples or [px_i, px_j]

                        label = _classify_chapter(seg_samples, anchor)
                        move_dir = "drift" if px_j > px_i else ("steam" if px_j < px_i else "flat")
                        trade_type = "lay-to-back" if move_dir == "drift" else ("back-to-lay" if move_dir == "steam" else "neutral")
                        try:
                            trend_ticks = calculate_tick_distance(px_i, px_j)
                        except Exception:
                            trend_ticks = px_j - px_i

                        pattern_key = f"{move_dir}→{seg_labels}→{label}→{trade_type}"

                        # --- Blueprint match logic: allow prefix matches (story mode) ---
                        chapters = pattern_key.split("→")
                        blueprint_match = None
                        if pattern_key in KNOWN_BP:
                            # exact full match
                            blueprint_match = {
                                "pattern": pattern_key,
                                "chapters_seen": len(chapters),
                                "expected_next": None
                            }
                        else:
                            # prefix / partial match logic
                            for bp in KNOWN_BP:
                                bp_chaps = bp.split("→")
                                if chapters[:2] == bp_chaps[:2]:
                                    expected_next = bp_chaps[2] if len(bp_chaps) > 2 else None
                                    blueprint_match = {
                                        "pattern": bp,
                                        "chapters_seen": 2,
                                        "expected_next": expected_next
                                    }
                                    break

                        runner_trades.append({
                            "surface_key": surface_key,
                            "runner": runner_name,
                            "market_id": market_id,
                            "selection_id": selection_id,
                            "entry": round_to_nearest_tick(px_i),
                            "exit":  round_to_nearest_tick(px_j),
                            "trend_ticks": trend_ticks,
                            "move": move_dir,
                            "trade_type": trade_type,
                            "pattern_key": pattern_key,
                            "tick_pattern": label,
                            "confidence": 0.65,
                            "blueprint_match": blueprint_match,
                        })


            except Exception as e:
                print(f"❌ Failed to process runner {selection_id}: {e}")
    finally:
        # --- CLOSE both connections ONCE, even if the loop raises ---
        try:
            con_auto.close()
        except Exception:
            pass
        try:
            con_bets.close()
        except Exception:
            pass

# === PATCH START ===
# 📍 TARGET: engines/blueprint_build.py:_run_builder()
# 📆 PATCHED: 2025-10-28Z — split blueprints by surface (flat / jumps)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # --- split and export by surface ----------------------------------
    surfaces = {"flat": {}, "jumps": {}}
    for t in runner_trades:
        surf = "jumps" if "jumps" in (t.get("surface_key") or "") else "flat"
        k = t.get("pattern_key")
        if not k:
            continue
        if k not in surfaces[surf]:
            surfaces[surf][k] = {"pattern_key": k, "trades": []}
        surfaces[surf][k]["trades"].append(t)

    # export both surfaces separately
    for surf, pdata in surfaces.items():
        fname = f"blueprint_signals_{surf}_{date.today().isoformat()}.json"
        out_fn = os.path.join(_OUTDIR, fname)
        try:
            with open(out_fn, "w") as f:
                json.dump(pdata, f, indent=2)
            print(f"✅ Exported {len(pdata)} {surf} blueprints → {out_fn}")
        except Exception as e:
            print(f"⚠️ Failed to export {surf} blueprints: {e}")
# === PATCH END ===


    # 🔢 Pattern Meta Summary
    pattern_summary = defaultdict(lambda: {"total": 0, "profitable": 0, "confidence_sum": 0.0})
    for trade in runner_trades:
        key = trade["pattern_key"]
        pattern_summary[key]["total"] += 1
        pattern_summary[key]["confidence_sum"] += trade["confidence"]

    # 📜 Export Known Blueprints
    print("\n📜 Known Blueprint Patterns")
    enriched_patterns = {}
    try:
        for key, summary in pattern_summary.items():
            start_oc = key.split("→")[1].split("-")[0]
            phase = map_oc_to_phase(key.split("→")[1])
            story_id = f"{start_oc}-{phase}"
            enriched_patterns[key] = {
                "sequence": key.split("→"),
                "meta": {
                    "total": summary["total"],
                    "profitable": 0,
                    "win_rate": 0.0,
                    "avg_confidence": round(summary["confidence_sum"] / summary["total"], 3) if summary["total"] > 0 else 0.0,
                    "story_phase": phase,
                    "oc_band": key.split("→")[1],
                    "story_id": story_id
                }
            }
        with open("known_blueprints.py", "w") as f:
            f.write("KNOWN_BLUEPRINT_PATTERNS = ")
            json.dump(enriched_patterns, f, indent=2)
    except Exception as e:
        print(f"⚠️ Failed to write blueprint patterns: {e}")

    # ⬇️ Export (NEW FORMAT)
    print(f"\n📦 Exporting {len(runner_trades)} trend trades to structured JSON format")

    pattern_dict_export = {}
    for trade in runner_trades:
        key = trade.get("pattern_key")
        if key:
            if key not in pattern_dict_export:
                pattern_dict_export[key] = {"pattern_key": key, "trades": []}
            pattern_dict_export[key]["trades"].append(trade)

    for pattern_key, meta in enriched_patterns.items():
        if pattern_key in pattern_dict_export:
            pattern_dict_export[pattern_key]["meta"] = meta.get("meta", {})
        else:
            pattern_dict_export[pattern_key] = {
                "pattern_key": pattern_key,
                "meta": meta.get("meta", {}),
                "trades": []
            }

    try:
        filename = f"blueprint_signals_{surface_key}_{date.today().isoformat()}.json"

        with open(filename, "w") as f:
            json.dump(pattern_dict_export, f, indent=2)
        print(f"✅ Exported {len(pattern_dict_export)} blueprint patterns to {filename}")
    except Exception as e:
        print(f"❌ Failed to write blueprint signals: {e}")

    try:
        with open("blueprint_meta_summary.json", "w") as f:
            json.dump({k: v.get("meta", {}) for k, v in enriched_patterns.items()}, f, indent=2)
    except Exception as e:
        print(f"⚠️ Failed to export meta summary: {e}")

    # Also export to data/blueprints and mark as ran
    try:
        out_fn = os.path.join(_OUTDIR, f"blueprint_signals_{surface_key}_{date.today().isoformat()}.json")

        with open(out_fn, "w") as f:
            json.dump(pattern_dict_export, f, indent=2)

        out_meta = os.path.join(_OUTDIR, "blueprint_meta_summary.json")
        with open(out_meta, "w") as f:
            json.dump({k: v.get("meta", {}) for k, v in enriched_patterns.items()}, f, indent=2)

        print(f"📦 Also exported {len(pattern_dict_export)} patterns to {out_fn}")
    except Exception as e:
        print(f"⚠️ Failed to write blueprint exports to data/blueprints: {e}")

        # === PATCH START ===
        # 📍 TARGET: engines/blueprint_build.py:_run_builder()
        # 📆 PATCHED: 2025-10-28Z — always update canonical latest blueprint file
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        try:
            # Create/update canonical alias for the current day
            canonical_path = os.path.join(_OUTDIR, f"blueprint_signals_{date.today().isoformat()}.json")
            import shutil
            shutil.copy2(out_fn, canonical_path)
            print(f"[blueprints] canonical copy updated → {os.path.basename(canonical_path)}")
        except Exception as e:
            print(f"[blueprints] warn: could not update canonical blueprint file: {e}")
        # === PATCH END ===



    try:
        _mark_ran_today()
        print("✅ Blueprint build complete.")
    except Exception:
        pass
    return {"runner_trades": runner_trades}

    # --- END moved body ---

def _infer_surface_from_bets(con, marketId: str) -> str:
    row = con.execute(
        """
        SELECT market_name
        FROM bets
        WHERE marketId = ?
        LIMIT 1
        """,
        (marketId,)
    ).fetchone()

    if not row or not row[0]:
        return "flat"  # fail-safe

    name = row[0].lower()
    if any(x in name for x in ("hurdle", "hrd", "chase", "chs", "nhf")):
        return "jumps"

    return "flat"

def update_for_market(*, marketId, selectionId, ctx, source="BUS"):
    from engines.config_paths import connect_db
    from engines.blueprint_cache import reload_cache

    con = connect_db(ro=True)
    try:
        surface = _infer_surface_from_bets(con, marketId)
    finally:
        con.close()

    blueprints = reload_cache(verbose=False)

    surface_patterns = blueprints.get(surface)
    if not surface_patterns:
        return  # nothing to attach

    # Attach ONLY what strategies need
    ctx["blueprint_surface"] = surface
    ctx["blueprint_patterns"] = surface_patterns



# === PATCH START ===
# 📍 TARGET: engines/blueprint_build.py: main()
# 📆 PATCHED: 2025-10-18T20:35Z — fixed runner_trades scope + added per-surface summary
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main(force: bool = False):
    print("\n🔁 Running Enhanced Blueprint Builder")
    if (not force) and _already_ran_today():
        print("↩️ Blueprints already ran today — skipping.")
        return

    # run builder and capture its trades
    try:
        print("[blueprints] starting builder …")
        results = _run_builder()
        if isinstance(results, dict) and "runner_trades" in results:
            runner_trades = results["runner_trades"]
        elif isinstance(results, list):
            runner_trades = results
        else:
            runner_trades = []
    except Exception as e:
        print(f"[blueprints] builder failed: {e}")
        runner_trades = []

    # mark as ran
    _mark_ran_today()

    # --- summary by surface key (safe against missing trades) ---
    from collections import Counter
    if runner_trades:
        surf_counts = Counter()
        for t in runner_trades:
            # pull the surface_key we generated earlier if available
            sk = t.get("surface_key")
            if not sk:
                # fallback: try infer from pattern or default
                pat = t.get("pattern_key", "flat_unknown")
                sk = "flat" if "flat" in pat else ("jumps" if "jump" in pat else "flat_unknown")
            surf_counts[sk] += 1

        print("\n=== Blueprint Summary by Surface Key ===")
        for sk, n in sorted(surf_counts.items()):
            print(f"  {sk:<25} {n:>8} patterns")
    else:
        print("\n[blueprints] no trades to summarise")

    print("✅ Blueprint build complete.")


if __name__ == "__main__":
    _FORCE = any(a in ("--force", "-f") for a in sys.argv)
    main(force=_FORCE)
# === PATCH END ===
