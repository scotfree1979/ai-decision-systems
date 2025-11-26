#!/usr/bin/env python3
# Minimal smoke test for YOUR fire_initial_scalp(signal, customer_order_ref)
# Usage:
#   python -m engines.live.router_test --module-hint <filename without .py> --side LAY --stake 2.0
# Example:
#   python -m engines.live.router_test --module-hint bet_router --side BACK --stake 2.0

from __future__ import annotations
import os, sys, time, random, argparse, importlib, types, collections, sqlite3
from datetime import datetime, timezone

# ----- paths -----
HERE = os.path.dirname(os.path.abspath(__file__))             # .../engines/live
ROOT = os.path.dirname(os.path.dirname(HERE))                 # repo root
SME_DIR = os.path.join(ROOT, "engines", "signal_memory_engine")

# Put SME dir first so its local imports (config_paths, etc.) resolve as intended
for p in (SME_DIR, ROOT):
    if p in sys.path:
        sys.path.remove(p)
for p in (SME_DIR, ROOT):
    sys.path.insert(0, p)

# DB helpers (we only read DBs to pick a runner)
from engines.config_paths import connect_db, autoscalp_db, set_db_paths

def _ensure_bets_log_exists():
    """Some SME modules open data/bets.log at import time; create it first."""
    try:
        data_dir = os.path.dirname(autoscalp_db())
        os.makedirs(data_dir, exist_ok=True)
        open(os.path.join(data_dir, "bets.log"), "a").close()
    except Exception:
        pass

def _utc_ref(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{random.randint(100,999)}"

def _direction_from_side(side: str) -> str:
    return "lay_to_back" if side.upper() == "LAY" else "back_to_lay"

def _pick_random_live_runner() -> tuple[str, str, float]:
    """Pick (marketId, selectionId, oc1) from current DBs."""
    bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
    adb = sqlite3.connect(autoscalp_db(), timeout=6); adb.row_factory = sqlite3.Row
    try:
        m = bdb.execute(
            "SELECT marketId FROM markets_schedule "
            "WHERE datetime(off_at_utc) >= datetime('now','utc') "
            "ORDER BY datetime(off_at_utc) ASC LIMIT 8"
        ).fetchall()
        if not m:
            m = adb.execute(
                "SELECT DISTINCT marketId AS marketId FROM inbound_oc_cache ORDER BY id DESC LIMIT 8"
            ).fetchall()
        if not m:
            raise RuntimeError("No markets available in schedule or inbound cache.")
        mid = str(random.choice(m)["marketId"])
        rows = adb.execute(
            "SELECT selectionId, oc1, oc1_band_json FROM inbound_oc_cache "
            "WHERE marketId=? ORDER BY id DESC", (mid,)
        ).fetchall()
        if not rows:
            raise RuntimeError(f"No inbound cache rows found for market {mid}")
        latest = {}
        for r in rows:
            sid = str(r["selectionId"])
            if sid not in latest:
                latest[sid] = r
        sid, r = random.choice(list(latest.items()))
        oc1 = r["oc1"]
        if oc1 is None:
            import json
            band = json.loads(r["oc1_band_json"]) if r["oc1_band_json"] else []
            oc1 = float(band[-1]) if band else 6.0
        return mid, sid, float(oc1)
    finally:
        try: bdb.close()
        except Exception: pass
        try: adb.close()
        except Exception: pass

def _try_status_fns():
    """Optional: if you expose a status fn in your SME (e.g., bet_placer.py), use it."""
    cands = [("bet_placer", "get_order_status_by_ref"),
             ("bet_placer", "get_order_status"),
             ("bet_placer", "list_current_orders_by_ref")]
    fns = []
    for mod, fn in cands:
        try:
            m = importlib.import_module(mod)
            if hasattr(m, fn):
                fns.append(getattr(m, fn))
        except Exception:
            continue
    return fns

def _poll_status(customer_ref: str, timeout_s=90, interval_s=2.0) -> str:
    fns = _try_status_fns()
    if not fns: return "unknown"
    import time
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for fn in fns:
            try:
                resp = fn(customer_ref)  # type: ignore
                # normalize common shapes
                if isinstance(resp, dict):
                    st = str(resp.get("status") or resp.get("orderStatus") or "").lower()
                    if "exec" in st or ("matched" in st and "unmatched" not in st):
                        return "matched"
                    fully = bool(resp.get("fully_matched") or resp.get("fullyMatched") or False)
                    matched_sz = float(resp.get("matched_size") or resp.get("sizeMatched") or 0.0)
                    if fully or matched_sz > 0:
                        return "matched"
                else:
                    st = str(resp).lower()
                    if "exec" in st or ("matched" in st and "unmatched" not in st):
                        return "matched"
            except Exception:
                continue
        time.sleep(interval_s)
    return "unmatched"

def main():
    ap = argparse.ArgumentParser(description="Call your fire_initial_scalp() exactly as-is")
    ap.add_argument("--module-hint", required=True, help="filename (without .py) under engines/signal_memory_engine/ where fire_initial_scalp lives")
    ap.add_argument("--side", choices=["BACK","LAY"], default="LAY")
    ap.add_argument("--stake", type=float, default=2.0)
    ap.add_argument("--odds", type=float, default=None)
    args = ap.parse_args()

    # Route DBs for reading odds/markets during test (doesn't affect placement)
    try: set_db_paths("learning")
    except Exception: pass

    # Pre-create bets.log if your SME module tries to open it at import-time
    _ensure_bets_log_exists()

    # Import your module EXACTLY (no guessing)
    modname = f"engines.signal_memory_engine.{args.module_hint}"
    try:
        mod = importlib.import_module(modname)
    except Exception as e:
        # as fallback, try bare name if you run from SME dir directly
        try: mod = importlib.import_module(args.module_hint)
        except Exception as e2:
            raise SystemExit(f"Could not import module '{args.module_hint}'. Tried {modname} and bare import.\nErrors: {e} | {e2}")

    if not hasattr(mod, "fire_initial_scalp"):
        raise SystemExit(f"Module '{args.module_hint}' imported, but fire_initial_scalp(signal, customer_order_ref) not found.")

    fire_initial_scalp = getattr(mod, "fire_initial_scalp")

    # Provide a dummy engine.counter if your module uses it
    if not hasattr(mod, "engine"):
        mod.engine = types.SimpleNamespace(counter=collections.defaultdict(int))  # type: ignore
    elif not hasattr(mod.engine, "counter"):  # type: ignore
        mod.engine.counter = collections.defaultdict(int)  # type: ignore

    # Pick a real runner
    mid, sid, oc1 = _pick_random_live_runner()
    odds = float(args.odds) if args.odds else oc1
    signal = {
        "marketId": mid,
        "selectionId": sid,
        "odds": odds,
        "stake": float(args.stake),
        "scalp_direction": _direction_from_side(args.side),
        "signal_type": "router_test",
        "confidence": 0.5,
    }
    ref = _utc_ref(f"TST-{args.side}")

    print(f"[ROUTER-TEST] fire_initial_scalp: side={args.side} odds={odds:.2f} stake={args.stake:.2f} mid={mid} sid={sid} ref={ref}")
    resp = fire_initial_scalp(signal, ref)  # << EXACT call
    print(f"[ROUTER-TEST] place response: {resp}")

    final = _poll_status(ref)
    print(f"[ROUTER-TEST] final status: {final}")

if __name__ == "__main__":
    main()
