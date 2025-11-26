# engines/live/scalp_smoke_test.py
#!/usr/bin/env python3
"""
LIVE scalp smoke test (self-contained)
- No interactive input
- Uses upgrade_import_patch.get_app_key/get_session_token or ENV (BETFAIR_APP_KEY/BETFAIR_SESSION)
- Picks a live runner from DB, places a BACK/LAY, polls for match
- Optionally places a 1-tick hedge and polls

Usage:
  python -m engines.live.scalp_smoke_test --side LAY --stake 2.0
  python -m engines.live.scalp_smoke_test --side BACK --stake 2.0
"""

from __future__ import annotations
import os, sys, json, time, random, argparse, logging, sqlite3, requests
from datetime import datetime, timezone
from typing import Optional, Tuple
from engines.config_paths import connect_db, autoscalp_db, set_db_paths
import engines.daily_config as daily_config
import getpass
# ── repo paths ───────────────────────────────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))          # .../engines/live
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ── DB helpers ───────────────────────────────────────────────────────────────
from engines.config_paths import connect_db, autoscalp_db, set_db_paths

# ── tokens / app key (no prompts) ────────────────────────────────────────────
# 🔎 SEARCH (the whole _get_keys function) and REPLACE with:
# 🔎 SEARCH the whole _get_keys() function and REPLACE with:
def _get_keys(cli_app_key: str | None = None,
              cli_session: str | None = None,
              prompt_if_missing: bool = True) -> tuple[str, str]:
    """
    Credential sources (in order):
      1) CLI flags: --appkey / --token
      2) Step-1 daily_config (APP_KEY, get_session_token)
      3) upgrade_import_patch (optional)
      4) ENV (BETFAIR_APP_KEY / BETFAIR_SESSION etc.)
      5) Prompt (if enabled) → interactive fallback for standalone runs
    """
    # 1) CLI overrides
    app_key = (cli_app_key or "").strip() or None
    sess    = (cli_session or "").strip() or None

    # 2) daily_config (Step 1)
    if not app_key:
        try:
            app_key = getattr(daily_config, "APP_KEY", None)
        except Exception:
            pass
    if not sess:
        try:
            get_tok = getattr(daily_config, "get_session_token", None)
            if callable(get_tok):
                sess = get_tok()
        except Exception:
            pass

    # 3) upgrade_import_patch
    if not app_key or not sess:
        try:
            try:
                from upgrade_import_patch import get_app_key as u_get_app_key, get_session_token as u_get_session_token  # type: ignore
            except Exception:
                from engines.upgrade_import_patch import get_app_key as u_get_app_key, get_session_token as u_get_session_token  # type: ignore
            app_key = app_key or u_get_app_key()
            sess    = sess    or u_get_session_token()
        except Exception:
            pass

    # 4) ENV
    app_key = app_key or os.getenv("BETFAIR_APP_KEY") or os.getenv("APP_KEY")
    sess    = sess    or os.getenv("BETFAIR_SESSION") or os.getenv("SESSION_TOKEN") or os.getenv("BF_SESSION")

    # 5) Prompt (standalone fallback)
    if prompt_if_missing and (not app_key or not sess):
        if not app_key:
            # App key isn’t super-sensitive, but hide it anyway to avoid copy/paste in shell history
            app_key = getpass.getpass("Enter Betfair App Key: ").strip()
        if not sess:
            sess = getpass.getpass("Enter Betfair Session Token: ").strip()

    if not app_key or not sess:
        raise RuntimeError(
            "Missing credentials: no AppKey/Session found (daily_config, upgrade_import_patch, ENV) and prompt disabled."
        )
    return app_key, sess



# ── logging to data/bets.log ─────────────────────────────────────────────────
def _init_logging():
    data_dir = os.path.dirname(autoscalp_db())
    os.makedirs(data_dir, exist_ok=True)
    log_path = os.path.join(data_dir, "bets.log")
    try:
        if not os.path.exists(log_path):
            open(log_path, "a").close()
    except Exception:
        pass
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(message)s"
    )

# ── odds math ────────────────────────────────────────────────────────────────
def calculate_tick(odds: float) -> float:
    if odds < 2.0:   return 0.01
    if odds < 3.0:   return 0.02
    if odds < 4.0:   return 0.05
    if odds < 6.0:   return 0.10
    if odds < 10.0:  return 0.20
    if odds < 20.0:  return 0.50
    if odds < 30.0:  return 1.00
    if odds < 50.0:  return 2.00
    if odds < 100.0: return 5.00
    return 10.00

def round_to_valid_odds(odds: float) -> float:
    tick = calculate_tick(odds)
    rounded = round(round(odds / tick) * tick, 2)
    return float(f"{max(1.01, rounded):.2f}")

# ── JSON-RPC helpers ────────────────────────────────────────────────────────
API_URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"

def _rpc(app_key: str, sess: str, method: str, params: dict) -> dict:
    headers = {
        "X-Application": app_key,
        "X-Authentication": sess,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    payload = [{
        "jsonrpc": "2.0",
        "method": f"SportsAPING/v1.0/{method}",
        "params": params,
        "id": 1
    }]
    r = requests.post(API_URL, headers=headers, data=json.dumps(payload), timeout=10)
    r.raise_for_status()
    resp = r.json()
    if not isinstance(resp, list) or "result" not in resp[0] and "error" in resp[0]:
        raise RuntimeError(f"RPC error: {resp}")
    return resp[0]

def place_bet(app_key: str, sess: str, market_id: str, selection_id: str,
              stake: float, odds: float, side: str, customer_order_ref: Optional[str] = None) -> Optional[str]:
    odds = round_to_valid_odds(float(odds))
    side = side.upper().strip()
    ref = customer_order_ref or f"SMK-{int(time.time())}-{random.randint(100,999)}"
    params = {
        "marketId": market_id,
        "instructions": [{
            "selectionId": int(selection_id),
            "side": side,
            "orderType": "LIMIT",
            "limitOrder": {
                "size": float(stake),
                "price": odds,
                "persistenceType": "PERSIST"
            },
            "customerOrderRef": ref
        }]
    }
    logging.info(f"[place] {side} {selection_id} @{odds} £{stake} ref={ref}")
    resp = _rpc(app_key, sess, "placeOrders", params)
    res = resp.get("result", {})
    if res.get("status") == "SUCCESS":
        ir = (res.get("instructionReports") or [{}])[0]
        return ir.get("betId")
    logging.info(f"[place] fail resp={resp}")
    return None

def cancel_bet(app_key: str, sess: str, bet_id: str) -> None:
    params = {"betIds": [bet_id]}
    try:
        _ = _rpc(app_key, sess, "cancelOrders", params)
        logging.info(f"[cancel] betId={bet_id}")
    except Exception as e:
        logging.info(f"[cancel] error betId={bet_id} err={e}")

def check_bet_matched(app_key: str, sess: str, bet_id: str) -> bool:
    params = {"betIds": [bet_id]}
    try:
        resp = _rpc(app_key, sess, "listCurrentOrders", params)
        cur = (resp.get("result", {}) or {}).get("currentOrders") or []
        if not cur:
            # no current order → could be fully executed & dropped; treat as matched best-effort
            return True
        co = cur[0]
        matched = float(co.get("sizeMatched") or 0.0)
        status = str(co.get("orderStatus") or co.get("status") or "").upper()
        return matched > 0.0 or "EXECUTION_COMPLETE" in status
    except Exception as e:
        logging.info(f"[status] error betId={bet_id} err={e}")
        return False

# ── DB sampling: choose a live runner ────────────────────────────────────────
def _pick_random_runner() -> Tuple[str, str, float, str]:
    """
    Return (marketId, selectionId, oc1, off_at_utc) from current DBs.
    """
    bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
    adb = sqlite3.connect(autoscalp_db(), timeout=8); adb.row_factory = sqlite3.Row
    try:
        mkts = bdb.execute(
            "SELECT marketId, off_at_utc FROM markets_schedule "
            "WHERE datetime(off_at_utc) >= datetime('now','utc') "
            "ORDER BY datetime(off_at_utc) ASC LIMIT 12"
        ).fetchall()
        if not mkts:
            mkts = adb.execute(
                "SELECT DISTINCT marketId AS marketId, NULL AS off_at_utc "
                "FROM inbound_oc_cache ORDER BY id DESC LIMIT 12"
            ).fetchall()
        if not mkts:
            raise RuntimeError("No markets available in schedule/inbound cache.")
        pick = random.choice(mkts)
        mid = str(pick["marketId"])
        off = pick["off_at_utc"] or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")

        rows = adb.execute(
            "SELECT selectionId, oc1, oc1_band_json FROM inbound_oc_cache "
            "WHERE marketId=? ORDER BY id DESC", (mid,)
        ).fetchall()
        if not rows:
            raise RuntimeError(f"No inbound rows for marketId={mid}")

        latest = {}
        for r in rows:
            sid = str(r["selectionId"])
            if sid not in latest:
                latest[sid] = r
        sid, r = random.choice(list(latest.items()))
        oc1 = r["oc1"]
        if oc1 is None and r["oc1_band_json"]:
            try:
                band = json.loads(r["oc1_band_json"])
                oc1 = float(band[-1]) if band else 6.0
            except Exception:
                oc1 = 6.0
        return mid, sid, float(oc1 or 6.0), off
    finally:
        try: bdb.close()
        except Exception: pass
        try: adb.close()
        except Exception: pass

# ── main flow ────────────────────────────────────────────────────────────────
def _gen_ref(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{random.randint(100,999)}"

def _opposite(side: str) -> str:
    return "BACK" if side.upper() == "LAY" else "LAY"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--side", choices=["BACK","LAY"], default="LAY")
    ap.add_argument("--stake", type=float, default=2.0)
    ap.add_argument("--ticks", type=int, default=1, help="hedge ticks (1=~1 tick scalp)")
    ap.add_argument("--no-hedge", action="store_true", help="place only first leg")
    ap.add_argument("--appkey", type=str, default=None, help="Betfair App Key (overrides tool config)")
    ap.add_argument("--token",  type=str, default=None, help="Betfair Session Token (overrides tool config)")
    ap.add_argument("--no-prompt", action="store_true", help="Disable interactive prompt if creds missing")

    # Route DB for reading markets/odds; no user input anywhere
    # Parse CLI first
    args = ap.parse_args()

    # Route DB for reading markets/odds; no user input anywhere
    set_db_paths("learning")
    _init_logging()

    app_key, sess = _get_keys(cli_app_key=args.appkey,
                              cli_session=args.token,
                              prompt_if_missing=(not args.no_prompt))
    mid, sid, oc1, off = _pick_random_runner()

    entry_odds = round_to_valid_odds(oc1)
    side = args.side.upper()
    stake = float(args.stake)
    ref = _gen_ref(side)

    print(f"[LIVE-TEST] placing {side} {sid} @{entry_odds} £{stake} (mid={mid}) ref={ref}")
    bet_id = place_bet(app_key, sess, mid, sid, stake, entry_odds, side, ref)
    if not bet_id:
        print("[LIVE-TEST] placement failed")
        sys.exit(2)
    print(f"[LIVE-TEST] betId={bet_id}; polling for match…")

    deadline = time.time() + 90
    matched = False
    while time.time() < deadline:
        if check_bet_matched(app_key, sess, bet_id):
            matched = True
            break
        time.sleep(2)

    if not matched:
        print("[LIVE-TEST] not matched in window; cancelling…")
        cancel_bet(app_key, sess, bet_id)
        print("[LIVE-TEST] RESULT: UNMATCHED (first leg)")
        sys.exit(0)

    print("[LIVE-TEST] first leg MATCHED")

    if args.no_hedge:
        print("[LIVE-TEST] RESULT: MATCHED (single-leg)")
        sys.exit(0)

    # Opposite hedge by N ticks
    ts = calculate_tick(entry_odds)
    if side == "LAY":
        hedge_side = "BACK"
        hedge_odds = round_to_valid_odds(entry_odds - (args.ticks * ts))
    else:
        hedge_side = "LAY"
        hedge_odds = round_to_valid_odds(entry_odds + (args.ticks * ts))

    ref2 = _gen_ref(hedge_side)
    print(f"[LIVE-TEST] placing hedge {hedge_side} @{hedge_odds} £{stake} ref={ref2}")
    bet_id2 = place_bet(app_key, sess, mid, sid, stake, hedge_odds, hedge_side, ref2)
    if not bet_id2:
        print("[LIVE-TEST] hedge placement failed")
        sys.exit(0)

    deadline = time.time() + 90
    matched2 = False
    while time.time() < deadline:
        if check_bet_matched(app_key, sess, bet_id2):
            matched2 = True
            break
        time.sleep(2)

    if matched2:
        print("[LIVE-TEST] RESULT: HEDGE MATCHED")
    else:
        print("[LIVE-TEST] RESULT: HEDGE UNMATCHED (consider cancelling in UI)")

if __name__ == "__main__":
    main()
