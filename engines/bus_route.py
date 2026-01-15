# ✅ api_tools.py – Shared API Functions (Updated with check_market_off_flag)

import json
import requests
import logging
import os
import time
from datetime import datetime, timedelta, timezone
datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
from typing import List, Dict, Any
from typing import List, Tuple
from collections import defaultdict
from engines.decision_engine.decide_once.scope import build_and_maintain_scope
from engines.market_monitor.monitor import get_market_state
from engines.legacy_snapshot_helper import get_legacy_parent_odds_snapshot


# --- at module level (top of file) ---
import requests
_session = requests.Session()
_adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=100, max_retries=2)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)

# ============================================================
# BUS ROUTE CONFIG (LOCKED)
# ============================================================

PLANS_PER_TICK = 30
CYCLE_SIZE = 300          # parents per full cycle
TICKS_PER_CYCLE = 10

# Per-tick allocation
ROUTE_SPLIT = {
    "LEGACY": 2,          # 2 runners → 16 plans (8 letters)
    "RISK": 7,            # defensive parents
    "EXPLORATORY": 5,     # always-on accumulator
    "INPLAY": 2,          # priority but narrow
}

def _build_runner_pool():
    """
    Authoritative runner pool:
    - scope markets
    - active / passive runners only
    - no filtering by engine
    """
    scope = build_and_maintain_scope()
    markets = scope.get("markets", []) or []

    pool = []

    for m in markets:
        mid = m["marketId"] if isinstance(m, dict) else str(m)
        st = get_market_state(mid) or {}
        runners = st.get("runners") or {}

        for sid, r in runners.items():
            if r.get("band") in ("ACTIVE", "PASSIVE"):
                pool.append((mid, str(sid)))

    return pool

class RunnerRotation:
    def __init__(self):
        self._idx = defaultdict(int)

    def next(self, engine: str, pool: List[Tuple[str, str]], n: int):
        if not pool:
            return []

        out = []
        i = self._idx[engine]

        for _ in range(n):
            out.append(pool[i % len(pool)])
            i += 1

        self._idx[engine] = i
        return out

def build_bus_route_tick(rotation: RunnerRotation):
    """
    Returns exactly 30 parent-plan *requests*.
    No CTX building. No execution.
    """
    pool = _build_runner_pool()
    if not pool:
        return []

    plans = []

    # LEGACY — 2 runners → 16 plans downstream
    for mid, sid in rotation.next("LEGACY", pool, ROUTE_SPLIT["LEGACY"]):
        plans.append(("LEGACY", mid, sid))

    # RISK — only if legacy parents exist
    legacy_parents = get_legacy_parent_odds_snapshot()

    legacy_runner_set = {
        (p["marketId"], p["selectionId"])
        for p in legacy_parents
    }

    risk_pool = [r for r in pool if r in legacy_runner_set]

    for mid, sid in rotation.next("RISK", risk_pool, ROUTE_SPLIT["RISK"]):
        plans.append(("RISK", mid, sid))

    return plans[:PLANS_PER_TICK]

def build_full_cycle():
    rotation = RunnerRotation()
    out = []

    for _ in range(TICKS_PER_CYCLE):
        out.extend(build_bus_route_tick(rotation))

    return out[:CYCLE_SIZE]



# -----------------------------
# ✅ LIVE ODDS FETCH (Refactored)
# -----------------------------

def fetch_live_odds(session_token, marketId, selectionId):
    """
    Return {'back': float|None, 'lay': float|None} for a runner,
    or {} if unavailable. Works in LIVE/LEARNING only.
    """
    import json, os, time, logging, requests
    from datetime import datetime, date

    global _session, _adapter

    # --- TEST mode short-circuit -------------------------------------------------
    try:
        from engines.upgrade_import_patch import get_mode  # type: ignore
        if (get_mode() or "learning").lower() == "test":
            return {}
    except Exception:
        pass

    # --- Resolve session token ---------------------------------------------------
    tok = (session_token or "").strip()
    if not tok:
        try:
            from engines.session_token import get_session_token as _central
            tok = (_central() or "").strip()
        except Exception:
            tok = os.getenv("BETFAIR_SESSION_TOKEN", "").strip()
    if not tok:
        logging.warning("⚠️ fetch_live_odds: no session_token; returning {}")
        return {}

    # --- Resolve app key ---------------------------------------------------------
    try:
        from engines.session_token import get_app_key as _ak
        app_key = _ak() or os.getenv("BETFAIR_APP_KEY") or "CZHojduNWa3kxWIn"
    except Exception:
        app_key = os.getenv("BETFAIR_APP_KEY") or "CZHojduNWa3kxWIn"

    # --- Prepare request ---------------------------------------------------------
    url = "https://api.betfair.com/exchange/betting/json-rpc/v1"
    headers = {
        "X-Application": app_key,
        "X-Authentication": tok,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = json.dumps([{
        "jsonrpc": "2.0",
        "method": "SportsAPING/v1.0/listMarketBook",
        "params": {
            "marketIds": [str(marketId)],
            "priceProjection": {"priceData": ["EX_BEST_OFFERS"], "virtualise": True}
        },
        "id": 1
    }])

    # --- Perform network call with retry ----------------------------------------
    j = None
    for attempt in range(3):
        try:
            resp = _session.post(url, headers=headers, data=payload, timeout=8)
            resp.raise_for_status()
            j = resp.json()
            break
        except (requests.exceptions.ConnectionError, BrokenPipeError):
            _session.close()
            time.sleep(0.3)
            _session.mount("https://", _adapter)
        except Exception as e:
            logging.warning(f"⚠️ fetch_live_odds attempt {attempt+1}/3 failed: {e}")
            time.sleep(0.3)
    else:
        logging.error("❌ fetch_live_odds: all retries failed")
        return {}

    # --- Parse JSON safely -------------------------------------------------------
    try:
        runners = j[0]["result"][0]["runners"]
    except Exception:
        logging.debug(f"fetch_live_odds: empty JSON for market {marketId}")
        return {}

    for r in runners:
        if str(r.get("selectionId")) == str(selectionId):
            ex = r.get("ex", {}) or {}
            back = (ex.get("availableToBack") or [{}])[0].get("price")
            lay  = (ex.get("availableToLay") or [{}])[0].get("price")
            return {"back": back, "lay": lay}

    # --- runner not found: once-per-day debug -----------------------------------
    mute_file = os.path.join(os.path.dirname(__file__), "runner_not_found_seen.json")
    today_key = date.today().isoformat()
    try:
        seen = json.load(open(mute_file, "r")) if os.path.exists(mute_file) else {}
    except Exception:
        seen = {}
    if seen.get("_date") != today_key:
        seen = {"_date": today_key}
    uid = f"{marketId}-{selectionId}"
    if uid not in seen:
        seen[uid] = 1
        try:
            json.dump(seen, open(mute_file, "w"))
        except Exception:
            pass
        logging.debug(f"runner {selectionId} not found in market {marketId}")

    return {}

def fetch_live_odds_for_pairs(pairs, session_token=None):
    """
    pairs = list of (marketId, selectionId)
    returns dict {(marketId, selectionId): {back, lay}}
    """
    results = {}

    for marketId, selectionId in pairs:
        odds = fetch_live_odds(
            session_token=session_token,
            marketId=str(marketId),
            selectionId=str(selectionId),
        )
        if odds:
            results[(str(marketId), str(selectionId))] = odds

    return results



# -----------------------------
# ✅ CHECK IF MARKET IS IN-PLAY (Refactored)
# -----------------------------

def check_market_off_flag(marketId, session_token):
    if not session_token:
        raise ValueError("❌ check_market_off_flag() requires a session_token")

    url = "https://api.betfair.com/exchange/betting/json-rpc/v1"
    headers = {
        "X-Application": "CZHojduNWa3kxWIn",
        "X-Authentication": session_token,
        "Content-Type": "application/json"
    }

    payload = json.dumps([
        {
            "jsonrpc": "2.0",
            "method": "SportsAPING/v1.0/listMarketBook",
            "params": {
                "marketIds": [marketId],
                "priceProjection": {},
                "virtualise": True
            },
            "id": 1
        }
    ])

    try:
        response = requests.post(url, headers=headers, data=payload)
        if response.status_code != 200:
            logging.error(f"❌ OFF flag fetch failed: {response.status_code}: {response.text}")
            return False

        result = response.json()[0].get("result", [])
        if not result:
            logging.warning(f"⚠️ Empty OFF result for {marketId}")
            return False

        return result[0].get("inplay", False)

    except Exception as e:
        logging.error(f"❌ Error checking in-play flag: {e}")
        return False

# ======================================================================
# PUBLIC API — Legacy matched parents + live odds (V2)
# ======================================================================

def get_legacy_parent_odds_snapshot(session_token=None):
    """
    Enumerate LEGACY matched parents for today (UTC) and enrich
    each with live back/lay odds.

    DB-first, read-only, side-effect free.

    Returns:
        List[dict] with keys:
            parent_id
            marketId
            selectionId
            side
            entry_odds
            entry_stake
            live_back
            live_lay
    """

    from engines.config_paths import auto_conn
   
    import sqlite3

    con = auto_conn(rw=False)
    con.row_factory = sqlite3.Row

    try:
        rows = con.execute("""
            SELECT
                p.id          AS parent_id,
                p.marketId    AS marketId,
                p.selectionId AS selectionId,
                p.side        AS side,
                p.entry_odds  AS entry_odds,
                p.entry_stake AS entry_stake
            FROM orders p
            WHERE p.role = 'PARENT'
              AND p.engine = 'LEGACY'
              AND p.entry_status = 'MATCHED'
              AND date(p.opened_at) = date('now','utc')
            ORDER BY p.opened_at ASC
        """).fetchall()
    finally:
        con.close()

    if not rows:
        return []

    enriched = []

    for r in rows:
        odds = fetch_live_odds(
            session_token=session_token,
            marketId=r["marketId"],
            selectionId=r["selectionId"],
        ) or {}

        enriched.append({
            "parent_id":   r["parent_id"],
            "marketId":    r["marketId"],
            "selectionId": r["selectionId"],
            "side":        r["side"],
            "entry_odds":  r["entry_odds"],
            "entry_stake": r["entry_stake"],
            "live_back":   odds.get("back"),
            "live_lay":    odds.get("lay"),
        })

    return enriched

from datetime import datetime, timezone


# ============================================================================
# V7 IN-PLAY SNAPSHOT HELPER (FINAL, CANONICAL)
# ============================================================================
#
# PURPOSE
# -------
# Provide a DB-first, schema-truthful snapshot of ALL runners
# in a given market for IN-PLAY decision making.
#
# • No execution logic
# • No assumptions
# • No legacy mastery tables
# • No external API calls
#
# This is the ONLY surface MSC_INPLAY should consume.
#
# ============================================================================

def get_v7_inplay_snapshot(market_id: str):
    """
    DB-first, schema-truthful snapshot of ALL runners in a market
    for MSC_INPLAY decisioning.

    • Uses inbound_oc_cache for odds (RISC-consistent)
    • Uses v_mastery_intel_v7 for intelligence
    • Uses v7_race_position_inferred for in-play position
    • No assumptions
    • No execution logic
    """

    from engines.config_paths import auto_conn
    from datetime import datetime, timezone
    import sqlite3

    con = auto_conn(rw=False)
    con.row_factory = sqlite3.Row

    try:
        rows = con.execute("""
            SELECT
                oc.marketId,
                oc.selectionId,

                -- === ODDS (RISC CANONICAL) ===
                COALESCE(oc.oc1, oc.anchor_odd)        AS odds,

                -- === V7 INTELLIGENCE ===
                mi.fav_rank_entry                      AS fav_rank,
                mi.success                             AS success,
                mi.weight                              AS weight,
                mi.drift_pct                           AS drift_pct,
                mi.actual_drift_pct                    AS actual_drift_pct,
                mi.reversal_flag                       AS reversal_flag,
                mi.mto_minutes                         AS mto_minutes,

                -- === IN-PLAY POSITION ===
                pos.pos_inplay                         AS pos_inplay,
                pos.anchor_odd                         AS anchor_odd,
                pos.drift_ratio                        AS drift_ratio

            FROM inbound_oc_cache oc

            LEFT JOIN v_mastery_intel_v7 mi
                   ON mi.marketId = oc.marketId
                  AND mi.selectionId = oc.selectionId
                  AND mi.mode = 'LIVE'

            LEFT JOIN v7_race_position_inferred pos
                   ON pos.marketId = oc.marketId
                  AND pos.selectionId = oc.selectionId

            WHERE oc.marketId = ?

            ORDER BY odds ASC
        """, (str(market_id),)).fetchall()

    finally:
        con.close()

    snapshot = []
    now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for r in rows:
        mto = r["mto_minutes"]

        # Derive race quartile (purely diagnostic)
        if mto is None:
            race_quartile = None
        elif mto > 45:
            race_quartile = "Q1"
        elif mto > 30:
            race_quartile = "Q2"
        elif mto > 15:
            race_quartile = "Q3"
        else:
            race_quartile = "Q4"

        snapshot.append({
            # identity
            "marketId": r["marketId"],
            "selectionId": r["selectionId"],

            # odds
            "odds": r["odds"],

            # intelligence
            "fav_rank": r["fav_rank"],
            "is_favourite": (r["fav_rank"] == 1),
            "success": r["success"],
            "weight": r["weight"],

            # drift / collapse
            "anchor_odd": r["anchor_odd"],
            "drift_ratio": r["drift_ratio"],
            "drift_pct": r["drift_pct"],
            "actual_drift_pct": r["actual_drift_pct"],
            "reversal_flag": bool(r["reversal_flag"]),

            # timing
            "mto_minutes": mto,
            "race_quartile": race_quartile,

            # in-play position
            "pos_inplay": r["pos_inplay"],

            # diagnostics
            "ts_utc": now_utc,
        })

    return snapshot

# ============================================================================
# LEGACY SNAPSHOT HELPER (CANONICAL, DB-FIRST)
# ============================================================================
def get_legacy_snapshot():
    """
    FINAL, CANONICAL LEGACY SNAPSHOT

    What this does (and ONLY this):
      1) Ask Scope for marketIds
      2) Ask MarketMonitor for runners per market
      3) Fetch live odds for each (marketId, selectionId)
      4) Return the combined snapshot

    ❌ No eligibility logic
    ❌ No band filtering
    ❌ No BUS assumptions
    ❌ No DB reconstruction

    BUS will decide what to ignore later.
    """

    from engines.decision_engine.decide_once.scope import build_and_maintain_scope
    from engines.market_monitor import monitor
    from engines.market_monitor.monitor import get_market_state
 
    from datetime import datetime, timezone

    # --------------------------------------------------
    # 1️⃣ Build scope (authoritative)
    # --------------------------------------------------
    scope = build_and_maintain_scope(inplay_window_min=15)

    markets = scope.get("markets", []) or []

    mids = []
    for m in markets:
        if isinstance(m, dict):
            mid = m.get("marketId")
        else:
            mid = str(m)
        if mid:
            mids.append(str(mid))

    if not mids:
        return []

    # --------------------------------------------------
    # 2️⃣ Ensure MarketMonitor state
    # --------------------------------------------------
    monitor.refresh(mids)

    # --------------------------------------------------
    # 3️⃣ Collect (mid, sid) pairs
    # --------------------------------------------------
    pairs = []
    runner_meta = {}  # (mid, sid) -> runner info

    for mid in mids:
        st = get_market_state(mid) or {}
        runners = st.get("runners") or {}

        for sid, r in runners.items():
            sid = str(sid)
            pairs.append((mid, sid))
            runner_meta[(mid, sid)] = {
                "band": r.get("band"),
                "is_favourite": bool(r.get("is_fav")),
                "monitor_px": r.get("px"),
            }

    if not pairs:
        return []

    # --------------------------------------------------
    # 4️⃣ Fetch live odds (bulk)
    # --------------------------------------------------
    odds_map = fetch_live_odds_for_pairs(pairs)

    # --------------------------------------------------
    # 5️⃣ Build snapshot
    # --------------------------------------------------
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    snapshot = []

    for (mid, sid), meta in runner_meta.items():
        odds = odds_map.get((mid, sid))
        if not odds:
            continue

        px = odds.get("back") or odds.get("lay")
        if px is None:
            continue

        snapshot.append({
            "marketId": mid,
            "selectionId": sid,

            # execution odds
            "odds": px,
            "back": odds.get("back"),
            "lay": odds.get("lay"),

            # monitor truth
            "band": meta["band"],
            "is_favourite": meta["is_favourite"],
            "monitor_px": meta["monitor_px"],

            # diagnostics
            "ts_utc": ts,
        })

    return snapshot



# -----------------------------
# ✅ STANDALONE TEST TOOL (SCRAPER)
# -----------------------------

if __name__ == "__main__":
    import os
    import time
    import json
    from datetime import datetime, timedelta
    import requests

    SESSION_TOKEN = os.getenv("SESSION_TOKEN") or input("🔐 Enter your Betfair session token: ")
    os.environ["SESSION_TOKEN"] = SESSION_TOKEN

    print("⏳ Initializing...")
    time.sleep(3)
    print("📱 Fetching live UK/IE WIN markets with 7+ runners...")

    try:
        response = requests.post(
            url="https://api.betfair.com/exchange/betting/json-rpc/v1",
            headers={
                "X-Application": "CZHojduNWa3kxWIn",
                "X-Authentication": SESSION_TOKEN,
                "Content-Type": "application/json"
            },
            data=json.dumps([
                {
                    "jsonrpc": "2.0",
                    "method": "SportsAPING/v1.0/listMarketCatalogue",
                    "params": {
                        "filter": {
                            "eventTypeIds": ["7"],
                            "marketCountries": ["GB", "IE"],
                            "marketTypeCodes": ["WIN"],
                            "marketStartTime": {
                                "from": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "to": (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
                            }
                        },
                        "marketProjection": ["RUNNER_DESCRIPTION", "MARKET_START_TIME", "EVENT"],
                        "sort": "FIRST_TO_START",
                        "maxResults": "1"
                    },
                    "id": 1
                }
            ])
        )

        result = response.json()[0].get("result", [])
        if not result:
            print("⚠️ No markets returned. Are you logged in?")
            exit()

        market = result[0]
        market_id = market["marketId"]
        runner = market["runners"][0]
        selection_id = runner["selectionId"]

        print(f"✅ Test Market: {market_id}")
        print(f"🐎 Test Runner: {runner['runnerName']} (SelectionId: {selection_id})")

        print("\n⭯️ Fetching live odds...\n")
        odds = fetch_live_odds(
            marketId=market_id,
            selectionId=selection_id,
            session_token=SESSION_TOKEN
        )

        print(f"✅ Live Odds for {runner['runnerName']}: {odds}")

        # ------------------------------------------------------------------
        # ✅ V2 HELPER: LEGACY MATCHED PARENTS + LIVE ODDS (TODAY)
        # ------------------------------------------------------------------

        import sqlite3
        from engines.config_paths import auto_conn

        print("\n=== V2: LEGACY MATCHED PARENTS + LIVE ODDS (TODAY UTC) ===\n")

        con = auto_conn(rw=False)
        con.row_factory = sqlite3.Row

        # 1️⃣ Enumerate LEGACY matched parents today (authoritative DB truth)
        rows = con.execute("""
            SELECT
                p.id          AS parent_id,
                p.marketId    AS marketId,
                p.selectionId AS selectionId,
                p.side,
                p.entry_odds
            FROM orders p
            WHERE p.role = 'PARENT'
              AND p.engine = 'LEGACY'
              AND p.entry_status = 'MATCHED'
              AND date(p.opened_at) = date('now','utc')
            ORDER BY p.opened_at ASC
        """).fetchall()

        con.close()

        if not rows:
            print("⚠️ No matched LEGACY parents found today")
        else:
            print(f"Found {len(rows)} LEGACY matched parents\n")

            enriched = []

            # 2️⃣ Enrich each (marketId, selectionId) with live odds
            for r in rows:
                odds = fetch_live_odds(
                    session_token=SESSION_TOKEN,
                    marketId=r["marketId"],
                    selectionId=r["selectionId"],
                )

                enriched.append({
                    "parent_id":   r["parent_id"],
                    "marketId":    r["marketId"],
                    "selectionId": r["selectionId"],
                    "side":        r["side"],
                    "entry_odds":  r["entry_odds"],
                    "live_back":   odds.get("back"),
                    "live_lay":    odds.get("lay"),
                })

            # 3️⃣ Print sample (human proof)
            for row in enriched[:10]:
                print(row)

        print("\n=== END V2 HELPER ===\n")

        # ------------------------------------------------------------------
        # ✅ V7 IN-PLAY SNAPSHOT (PRINT-ONLY)
        # ------------------------------------------------------------------



        print("\n=== V7 IN-PLAY SNAPSHOT (DB-FIRST) ===\n")

        markets_to_print = []

        # Prefer in-play markets if they exist
        try:
            from engines.decision_engine.decide_once.scope import scope_snapshot
            scope = scope_snapshot(inplay_window_min=15)
            inplay = scope.get("in_play", []) or []
            markets_to_print = [m[0] for m in inplay]
        except Exception:
            markets_to_print = []

        # Fallback: reuse test market
        if not markets_to_print:
            markets_to_print = [market_id]
            print("⚠️ No in-play markets — using test market fallback\n")

        for mid in markets_to_print:
            print(f"📊 Market {mid}\n" + "-" * 60)

            snap = get_v7_inplay_snapshot(mid)

            if not snap:
                print("  (no runner data)\n")
                continue

            for r in snap:
                print(
                    f"  sid={r['selectionId']:<8} "
                    f"odds={r['odds']!s:<6} "
                    f"fav_rank={r['fav_rank']!s:<3} "
                    f"drift_pct={r['drift_pct']!s:<6} "
                    f"pos={r['pos_inplay']!s:<10} "
                    f"race={r['race_quartile']}"
                )


            print()

        print("=== END V7 SNAPSHOT ===\n")

        # ------------------------------------------------------------------
        # ✅ V7 LEGACY SNAPSHOT
        # ------------------------------------------------------------------

        print("\n=== LEGACY SNAPSHOT (DB-FIRST, SCOPE-DRIVEN) ===\n")

        snap = get_legacy_snapshot()

        if not snap:
            print("(no legacy-eligible runners)\n")
            raise SystemExit(0)

        by_market: Dict[str, List[Dict[str, Any]]] = {}
        for r in snap:
            by_market.setdefault(r["marketId"], []).append(r)

        for mid, runners in by_market.items():
            print(f"📊 Market {mid}")
            print("-" * 60)

            for r in sorted(runners, key=lambda x: (x["odds"] or 999)):
                print(
                    f"  sid={r['selectionId']:<10} "
                    f"odds={r['odds']!s:<6} "
                    f"band={r['band']:<7} "
                    f"fav={str(r['is_favourite']):<5} "
                    f"px={r['monitor_px']!s:<6}"
                )


            print()

        print("=== END LEGACY SNAPSHOT ===\n")

        # ------------------------------------------------------------------
        # ✅ V7 BUS SNAPSHOT
        # ------------------------------------------------------------------

        r = build_full_cycle()

        print(f"Total plans: {len(r)}")
        by_engine = defaultdict(int)
        for eng, _, _ in r:
            by_engine[eng] += 1

        print("By engine:")
        for k, v in by_engine.items():
            print(f"  {k}: {v}")

        print("=== END BUS SNAPSHOT ===\n")


    except Exception as e:
        print(f"❌ Error: {e}")
