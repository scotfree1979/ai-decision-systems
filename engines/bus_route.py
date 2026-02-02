# ✅ bus_route.py – Cloned API Functions (Updated to be the authority on mid & Sid lookup for trading)

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
from engines.micro_scalper_v7.v7_snapshot_helper import get_v7_inplay_snapshot



# --- at module level (top of file) ---
import requests
_session = requests.Session()
_adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=100, max_retries=2)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)

# ============================================================
# BUS ROUTE CONFIG (LOCKED)
# ============================================================

PLANS_PER_TICK = 70
CYCLE_SIZE = 700          # parents per full cycle
TICKS_PER_CYCLE = 10

# Per-tick allocation
ROUTE_SPLIT = {
    "LEGACY": 14,
    "MSC_RISK": 30,
    "MSC_INPLAY": 10,
    "MSC_EXPLORATORY": 6,
    "OVERWATCHER": 10,
}

def _order_runner_pool_by_market_time(pairs):
    """
    Reorder (marketId, selectionId) pairs so that:
    - All runners from the same market are contiguous
    - Markets are ordered by earliest marketStartTime first
    """

    from engines.config_paths import connect_db
    import sqlite3
    from datetime import datetime, timezone

    if not pairs:
        return []

    # Group runners by market
    by_market = {}
    for mid, sid in pairs:
        by_market.setdefault(str(mid), []).append((str(mid), str(sid)))

    # Load market start times (authoritative: bets.db)
    con = connect_db(ro=True)
    con.row_factory = sqlite3.Row

    market_times = {}
    try:
        for mid in by_market.keys():
            row = con.execute(
                """
                SELECT marketStartTime
                FROM bets
                WHERE marketId = ?
                LIMIT 1
                """,
                (mid,),
            ).fetchone()

            if row and row["marketStartTime"]:
                off = datetime.fromisoformat(
                    row["marketStartTime"].replace("Z", "+00:00")
                )
                market_times[mid] = off
            else:
                # Push unknown markets to the end safely
                market_times[mid] = datetime.max.replace(tzinfo=timezone.utc)
    finally:
        con.close()

    # Sort markets by off time
    ordered_markets = sorted(
        market_times.items(),
        key=lambda x: x[1]
    )

    # Flatten runners market-by-market
    ordered_pairs = []
    for mid, _off in ordered_markets:
        ordered_pairs.extend(by_market.get(mid, []))

    return ordered_pairs

def _filter_valid_markets(
    runner_pairs: list[tuple[str, str]],
    *,
    min_runners_per_market: int = 6,
    min_markets: int = 5,
):
    """
    Enforce route-quality invariants:
    - Prefer markets with >= min_runners_per_market runners
    - GUARANTEE at least min_markets markets by extending with next-best markets

    Never blocks the route.
    """

    from collections import defaultdict

    by_market = defaultdict(list)
    for mid, sid in runner_pairs:
        by_market[str(mid)].append((mid, sid))

    # 1️⃣ Primary valid markets
    valid_markets = {
        mid: pairs
        for mid, pairs in by_market.items()
        if len(pairs) >= min_runners_per_market
    }

    if len(valid_markets) >= min_markets:
        out = []
        for pairs in valid_markets.values():
            out.extend(pairs)
        return out

    # 2️⃣ Extend with largest remaining markets
    print(
        f"[BUS_ROUTE][WARN] only {len(valid_markets)} valid markets "
        f"(need {min_markets}, ≥{min_runners_per_market} runners each) — extending route"
    )

    # Sort remaining markets by runner count (desc)
    remaining = sorted(
        (
            (mid, pairs)
            for mid, pairs in by_market.items()
            if mid not in valid_markets
        ),
        key=lambda x: len(x[1]),
        reverse=True,
    )

    extended_markets = dict(valid_markets)

    for mid, pairs in remaining:
        extended_markets[mid] = pairs
        if len(extended_markets) >= min_markets:
            break

    # 3️⃣ Flatten, preserving market grouping
    out = []
    for pairs in extended_markets.values():
        out.extend(pairs)

    return out


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

class BusRouteSnapshot:
    def __init__(self):
        self.route_id = 0
        self.runner_pool = []
        self.bus_stops = {}
        self.ctx_map = {}  # (marketId, selectionId) -> ctx

    # ======================================================================================================
    # 📍 TARGET: engines/bus_route.py
    # 🧩 ACTION: HYBRID — CTX reuse + dynamic refresh separation
    # 📆 PATCHED: 2026-01-24 — CTX built once per route, reused per tick
    #
    # PRESERVES:
    # - legacy parent binding
    # - v7 in-play enrichment
    # - full CTX shape
    #
    # CHANGES:
    # - CTX is built ONCE per (mid, sid)
    # - dynamic fields (px/odds) are cleared here
    # - BUS becomes sole refresher of dynamic data
    # ======================================================================================================

    def build_route(self):
        self.route_id += 1
        raw_pairs = list(get_root_ctx_runner_pairs())
        ordered = _order_runner_pool_by_market_time(raw_pairs)

        # 🔒 ROUTE QUALITY ENFORCEMENT
        self.runner_pool = _filter_valid_markets(
            ordered,
            min_runners_per_market=6,
            min_markets=5,
        )

        # --------------------------------------------------
        # Resolve session token ONCE for the entire route
        # --------------------------------------------------
        session_token = (
            os.getenv("SESSION_TOKEN")
            or os.getenv("BETFAIR_SESSION_TOKEN")
        )

        # --------------------------------------------------
        # BUILD / REUSE CTX (AUTHORITATIVE, STATIC HERE)
        # --------------------------------------------------
        from engines.mastery.context_builder import build_context, build_context_for_runner

        base_ctx, _meta = build_context(source="LIVE")

        # ⛓️ Preserve existing CTX map if present
        ctx_map = dict(self.ctx_map) if self.ctx_map else {}

        # --------------------------------------------------
        # Build CTX ONLY for unseen runners
        # --------------------------------------------------
        for mid, sid in self.runner_pool:
            key = (str(mid), str(sid))

            # 🔒 REUSE — do NOT rebuild CTX
            if key in ctx_map:
                continue

            try:
                # --------------------------------------------------
                # BUILD FULL STATIC CTX (ONCE PER ROUTE)
                # --------------------------------------------------
                ctx, _ = build_context_for_runner(mid, sid, source="LIVE")

                # --------------------------------------------------
                # 🔧 DYNAMIC FIELDS — CLEARED HERE (BUS OWNS REFRESH)
                # --------------------------------------------------
                # 🔧 DYNAMIC FIELDS — PRESERVE SNAPSHOT ODDS
                # Do NOT clear odds here.
                # BUS refresh will overwrite when Betfair returns data.
                ctx.setdefault("px",   ctx.get("odds"))
                ctx.setdefault("odds", ctx.get("odds"))
                ctx.setdefault("back", None)
                ctx.setdefault("lay",  None)

                # --------------------------------------------------
                # LEGACY parent binding (STATIC FOR ROUTE)
                # --------------------------------------------------
                legacy_parents = get_legacy_parent_odds_snapshot(
                    session_token=session_token
                )

                # Index for O(1) lookup
                legacy_parent_by_runner = {
                    (str(p["marketId"]), str(p["selectionId"])): p
                    for p in legacy_parents
                }

                # --------------------------------------------------
                # LEGACY parent binding (STATIC FOR ROUTE)
                # --------------------------------------------------
                parent = legacy_parent_by_runner.get((str(mid), str(sid)))

                if parent:
                    ctx["legacy_parent_id"]   = parent["parent_id"]
                    ctx["legacy_entry_odds"]  = parent["entry_odds"]
                    ctx["legacy_entry_side"]  = parent["side"]
                    ctx["legacy_entry_stake"] = parent.get("entry_stake")
                else:
                    ctx["legacy_parent_id"] = None


                # --------------------------------------------------
                # V7 IN-PLAY INTEL (STATIC SNAPSHOT PER ROUTE)
                # --------------------------------------------------
                snap = get_v7_inplay_snapshot(mid)

                if snap:
                    by_sid = {str(r["selectionId"]): r for r in snap}
                    intel = by_sid.get(str(sid))
                    if intel:
                        ctx.update({
                            "fav_rank": intel.get("fav_rank"),
                            "success": intel.get("success"),
                            "weight": intel.get("weight"),
                            "drift_pct": intel.get("drift_pct"),
                            "actual_drift_pct": intel.get("actual_drift_pct"),
                            "reversal_flag": intel.get("reversal_flag"),
                            "mto_minutes": intel.get("mto_minutes"),
                            "pos_inplay": intel.get("pos_inplay"),
                            "drift_ratio": intel.get("drift_ratio"),
                        })

                ctx_map[key] = ctx

            except Exception:
                continue  # fail-open (route must never die)

        self.ctx_map = ctx_map
        self.partition_into_bus_stops()


    # ======================================================================================================
    # 📍 TARGET: engines/bus_route.py
    # 🧩 ACTION: ADD — dynamic CTX refresh (BUS-called)
    # 📆 PATCHED: 2026-01-24 — odds-only refresh + timing probe
    #
    # PURPOSE:
    # - Refresh ONLY dynamic fields
    # - Measure real per-tick cost
    # ======================================================================================================

    def refresh_ctx_dynamic_fields(self):
        """
        Refresh px / odds / back / lay only.
        Returns elapsed time (seconds).
        """
        import time
        from engines.bus_route import get_runner_odds_map

        t0 = time.time()

        odds_map = get_runner_odds_map(list(self.ctx_map.keys()))

        for (mid, sid), odds in odds_map.items():
            ctx = self.ctx_map.get((mid, sid))
            if not ctx:
                continue

            ctx["px"]   = odds["px"]
            ctx["odds"] = odds["px"]
            ctx["back"] = odds.get("back")
            ctx["lay"]  = odds.get("lay")

        return time.time() - t0


    def get_ctx_map(self):
        """
        Authoritative CTX map for the entire route.
        BUS must consume this directly.
        """
        return self.ctx_map


    def get_bus_stop_pairs(self, tick: int):
        """
        Canonical BUS helper.

        Returns:
            List[(marketId, selectionId)] for this bus stop.
        """
        return self.bus_stops.get(tick, [])

    def partition_into_bus_stops(self):
        n = len(self.runner_pool)
        if n == 0:
            return

        base = n // TICKS_PER_CYCLE
        remainder = n % TICKS_PER_CYCLE

        self.bus_stops = {}
        idx = 0

        for tick in range(1, TICKS_PER_CYCLE + 1):
            size = base + (1 if tick <= remainder else 0)
            self.bus_stops[tick] = self.runner_pool[idx:idx+size]
            idx += size

    def get_bus_stop(self, tick):
        return self.bus_stops.get(tick, [])

    def get_all_runners(self):
        return self.runner_pool

# ============================================================================
# CANONICAL ROOT CTX RUNNER SET
# ============================================================================
# ======================================================================================================
# 📍 TARGET: engines/bus_route.py
# 🔎 SEARCH: def get_root_ctx_runner_pairs():
# 🧩 ACTION: REPLACE FUNCTION BODY
# 📆 PATCHED: 2026-01-29 — Ensure RISK + INPLAY runners are always included in route snapshot
#
# WHY:
# - BUS refreshes PX ONLY for runners present in BusRouteSnapshot
# - MSC_RISK and MSC_INPLAY may emit (mid, sid) pairs not present in scope runner pool
# - Missing inclusion caused PX to never refresh, leading to false risk_missing_px
#
# INVARIANT ENFORCED:
# - If an engine can evaluate a runner, that runner MUST be in the route snapshot
# ======================================================================================================

def get_root_ctx_runner_pairs():
    """
    Authoritative union of ALL (marketId, selectionId) pairs
    required by any BUS lane.

    BUS ROUTE is the ONLY place where runner identity is expanded.
    Lanes must NEVER introduce new runners.
    """

    pairs = set()

    # --------------------------------------------------
    # 1️⃣ LEGACY / EXPLORATORY route runners
    # --------------------------------------------------
    try:
        pairs |= set(_build_runner_pool())
    except Exception:
        pass

    # --------------------------------------------------
    # 2️⃣ RISK legacy-parent anchor runners
    # --------------------------------------------------
    try:
        for mid, sid, _legacy_pid, _anchor_px in get_risk_legacy_parent_pairs():
            if mid and sid:
                pairs.add((str(mid), str(sid)))
    except Exception:
        pass

    # --------------------------------------------------
    # 3️⃣ RISK exclusion cycles (unmatched RISK children)
    # --------------------------------------------------
    try:
        for mid, sid, _risk_pid in get_risk_cycle_exclusions():
            if mid and sid:
                pairs.add((str(mid), str(sid)))
    except Exception:
        pass

    # --------------------------------------------------
    # 4️⃣ EXPLORATORY exclusion runners
    # --------------------------------------------------
    try:
        pairs |= {
            (str(mid), str(sid))
            for (mid, sid) in get_exploratory_active_parent_pairs()
        }
    except Exception:
        pass

    # --------------------------------------------------
    # 7️⃣ IN-PLAY parent runners (BUS Phase-0 mirror)
    # --------------------------------------------------
    try:
        pairs |= get_inplay_parent_runner_pairs()
    except Exception:
        pass


    # --------------------------------------------------
    # 6️⃣ STOPLOSS surfaces (Overwatcher)
    # --------------------------------------------------
    try:
        pairs |= {
            (str(mid), str(sid))
            for (mid, sid) in get_stoploss_parent_pairs()
        }
    except Exception:
        pass

    return pairs





# ============================================================================
# RISK ELIGIBILITY — LEGACY PARENTS WITHOUT MATCHED CHILD
# ============================================================================
# ======================================================================
# 📍 TARGET: engines/bus_route.py
# 🔎 ADD: risk cycle exclusion helper
# 📆 PATCHED: 2026-03-22 — risk child-missing exclusion (BUS authority)
# ======================================================================

# ======================================================================
# 📍 TARGET: engines/bus_route.py
# 🔎 SEARCH: def get_risk_cycle_exclusions():
# 🧩 ACTION: SCOPE EXCLUSIONS PER LEGACY PARENT CYCLE
# 📆 PATCHED: 2026-03-22 — Risk exclusion keyed by legacy_parent_id
#
# WHY:
# - Risk cycles are PER LEGACY PARENT, not per runner
# - One blocked child must NOT suppress other shadow cycles
# - Prevents silent risk starvation
#
# RETURNS:
#   Set[(legacy_parent_id)]
# ======================================================================

def get_risk_cycle_exclusions():
    """
    Return LEGACY parent IDs whose MSC_RISK shadow cycle
    is currently blocked (risk parent exists but child not matched).

    Scope:
      • One exclusion per LEGACY parent
      • Runner-level sharing is forbidden
    """

    from engines.config_paths import auto_conn
    import sqlite3

    con = auto_conn(rw=False)
    con.row_factory = sqlite3.Row

    try:
        rows = con.execute(
            """
            SELECT DISTINCT
                p.id AS legacy_parent_id
            FROM orders r
            JOIN orders p
              ON p.id = r.hedge_of
            LEFT JOIN orders c
              ON c.hedge_of = r.id
             AND c.role = 'CHILD'
            WHERE r.engine = 'MSC_RISK'
              AND r.role = 'PARENT'
              AND UPPER(r.entry_status) IN ('PLACED','MATCHED')
              AND (c.id IS NULL OR UPPER(c.entry_status) <> 'MATCHED')
            """
        ).fetchall()
    finally:
        con.close()

    return {int(r["legacy_parent_id"]) for r in rows}


# ======================================================================================================
# 📍 TARGET: engines/bus_route.py
# 🔎 SEARCH: def get_risk_legacy_parent_pairs():
# 🧩 ACTION: REPLACE — redefine Shadowbit eligibility (LEGACY shadow followers)
# 📆 PATCHED: 2026-01-23 — Shadowbit eligibility made lifecycle-agnostic
#
# RATIONALE:
# - Shadowbit is NOT a risk hedge
# - Every LEGACY parent spawns a Shadowbit
# - Shadowbit lifecycle is market-time based, not parent-state based
#
# NEW INVARIANT:
# - Any LEGACY parent (PLACED or MATCHED) qualifies
# - Shadowbit remains active until market goes in-play
# - No child / release / exposure semantics here
#
# BUS remains the sole authority on stake, exposure, and routing.
# ======================================================================================================

def get_risk_legacy_parent_pairs():
    """
    Return ONLY TODAY's MATCHED LEGACY parents eligible for MSC_RISK.

    HARD INVARIANTS:
    - LIVE mode
    - LEGACY engine
    - PARENT role
    - entry_status = MATCHED
    - exit_status != MATCHED
    - opened TODAY (UTC)
    - excluded risk cycles removed
    """

    from engines.config_paths import open_auto_db
    from engines.bus_route import get_risk_cycle_exclusions
    import sqlite3

    con = open_auto_db(rw=False)
    con.row_factory = sqlite3.Row

    try:
        # Cycle exclusions are parent_id based
        excluded_legacy_parents = get_risk_cycle_exclusions()

        rows = con.execute(
            """
            SELECT
                p.marketId,
                p.selectionId,
                p.id         AS legacy_parent_id,
                p.entry_odds AS anchor_px
            FROM orders p
            WHERE p.mode = 'LIVE'
              AND p.role = 'PARENT'
              AND p.engine = 'LEGACY'
              AND UPPER(p.entry_status) = 'MATCHED'
              AND (p.exit_status IS NULL OR UPPER(p.exit_status) != 'MATCHED')
              AND date(p.opened_at) = date('now','utc')
            """
        ).fetchall()

        out = []
        for r in rows:
            pid = int(r["legacy_parent_id"])

            # 🔒 FINAL GUARD — cycle-scoped exclusion
            if pid in excluded_legacy_parents:
                continue

            out.append(
                (
                    str(r["marketId"]),
                    str(r["selectionId"]),
                    pid,
                    float(r["anchor_px"]),
                )
            )

        return out

    finally:
        con.close()


# ============================================================================
# INPLAY LIFECYCLE — ALL ACTIVE MIDS & SIDS
# ============================================================================
def get_inplay_parent_runner_pairs():
    """
    Authoritative IN-PLAY runner surface.

    Returns (marketId, selectionId) for ALL runners that:
      - Have ANY parent today (any engine)
      - Market has started (or is flagged in-play)
      - DB-first
    """

    from engines.config_paths import auto_conn
    import sqlite3
    con.execute("ATTACH DATABASE 'data/bets.db' AS bets")

    con = auto_conn(rw=False)
    con.row_factory = sqlite3.Row

    try:
        rows = con.execute("""
            SELECT DISTINCT
                p.marketId,
                p.selectionId
            FROM orders p
            JOIN bets.bets b
              ON b.marketId = p.marketId
            WHERE date(p.opened_at) = date('now','utc')
              AND julianday(b.marketStartTime) <= julianday('now','utc')
        """).fetchall()
    finally:
        con.close()

    return {
        (str(r["marketId"]), str(r["selectionId"]))
        for r in rows
        if r["marketId"] and r["selectionId"]
    }

# ============================================================================
# EXPLORATORY LIFECYCLE — ACTIVE EXPLORATORY PARENTS
# ============================================================================
def get_exploratory_active_parent_pairs():
    """
    Returns (marketId, selectionId) where MSC_EXPLORATORY has
    a MATCHED parent TODAY (UTC) with NO matched child.

    Used to EXCLUDE runners from exploratory re-entry.
    """

    from engines.config_paths import auto_conn
    import sqlite3

    con = auto_conn(rw=False)
    con.row_factory = sqlite3.Row

    try:
        rows = con.execute(
            """
            SELECT DISTINCT
                p.marketId,
                p.selectionId
            FROM orders p
            LEFT JOIN orders c
              ON c.hedge_of = p.id
             AND UPPER(c.exit_status) = 'MATCHED'
            WHERE p.engine = 'MSC_EXPLORATORY'
              AND p.role = 'PARENT'
              AND UPPER(p.entry_status) = 'MATCHED'
              AND date(p.opened_at) = date('now','utc')
              AND c.id IS NULL
            """
        ).fetchall()
    finally:
        con.close()

    return {
        (str(r["marketId"]), str(r["selectionId"]))
        for r in rows
        if r["marketId"] and r["selectionId"]
    }

# ======================================================================================================
# 📍 TARGET: engines/bus_route.py
# 🧩 ACTION: ADD helper — STOPLOSS eligibility surface
# 📆 PATCHED: 2026-01-25 — unify LEGACY + EXPLORATORY parents for Overwatcher
#
# PURPOSE:
# - Provide a canonical STOPLOSS surface
# - Include ALL parent types that can bleed
# - No lifecycle or child semantics
# - Time-gated only
# ======================================================================================================

def get_stoploss_parent_pairs():
    """
    STOPLOSS eligibility (AUTHORITATIVE).

    Returns (marketId, selectionId) for ANY runner that has:
      • a LEGACY parent (PLACED or MATCHED today), OR
      • an MSC_EXPLORATORY parent (MATCHED today)

    Constraints:
      • Market not started
      • > 3 minutes to off
      • DB-first
      • No child / exposure / lifecycle logic
    """

    from engines.config_paths import auto_conn
    import sqlite3

    con = auto_conn(rw=False)
    con.row_factory = sqlite3.Row

    try:
        # Attach BETS DB for market time authority
        con.execute("ATTACH DATABASE 'data/bets.db' AS bets")

        rows = con.execute(
            """
            SELECT DISTINCT
                p.marketId,
                p.selectionId
            FROM orders p
            JOIN bets.bets b
              ON b.marketId = p.marketId
            WHERE
                (
                    -- LEGACY parents (PLACED or MATCHED)
                    (
                        p.engine = 'LEGACY'
                        AND p.role = 'PARENT'
                        AND UPPER(p.entry_status) IN ('PLACED','MATCHED')
                    )
                    OR
                    -- EXPLORATORY parents (MATCHED only)
                    (
                        p.engine = 'MSC_EXPLORATORY'
                        AND p.role = 'PARENT'
                        AND UPPER(p.entry_status) = 'MATCHED'
                    )
                )
                AND date(p.opened_at) = date('now','utc')

                -- market has NOT started
                AND julianday(b.marketStartTime) > julianday('now','utc')

                -- more than 3 minutes to off
                AND (julianday(b.marketStartTime) - julianday('now','utc')) > (3.0 / 1440.0)
            """
        ).fetchall()

    finally:
        try:
            con.execute("DETACH DATABASE bets")
        except Exception:
            pass
        con.close()

    return [
        (str(r["marketId"]), str(r["selectionId"]))
        for r in rows
        if r["marketId"] and r["selectionId"]
    ]

def get_stoploss_parent_surfaces():
    """
    STOPLOSS execution surface (EXTENSION).

    Builds on get_stoploss_parent_pairs() and enriches each
    (marketId, selectionId) with parent execution fields.

    Does NOT change eligibility logic.
    Does NOT alter existing helpers.
    """

    from engines.config_paths import auto_conn
    import sqlite3

    pairs = set(get_stoploss_parent_pairs())
    if not pairs:
        return []

    con = auto_conn(rw=False)
    con.row_factory = sqlite3.Row

    try:
        rows = con.execute("""
            SELECT
                p.id            AS parent_id,
                p.engine        AS engine,
                p.marketId,
                p.selectionId,
                p.side,
                p.entry_odds,
                p.entry_stake,
                p.stop_ticks
            FROM orders p
            WHERE p.role = 'PARENT'
              AND UPPER(p.entry_status) = 'MATCHED'
              AND p.stop_ticks IS NOT NULL
              AND date(p.opened_at) = date('now','utc')
        """).fetchall()
    finally:
        con.close()

    out = []

    for r in rows:
        key = (str(r["marketId"]), str(r["selectionId"]))
        if key not in pairs:
            continue

        out.append({
            "parent_id":   int(r["parent_id"]),
            "engine":      r["engine"],
            "marketId":    key[0],
            "selectionId": key[1],
            "side":        r["side"],
            "entry_odds":  float(r["entry_odds"]),
            "entry_stake": float(r["entry_stake"]),
            "stop_ticks":  int(r["stop_ticks"]),
        })

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
    legacy_parents = get_legacy_parent_odds_snapshot(
        session_token=session_token
    )


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

# === PATCH START ==============================================================
# 📍 TARGET: engines/micro_scalper_v7/v7_snapshot_helper.py
# 🔎 SEARCH: def get_v7_inplay_snapshot(market_id: str):
# 🛠 ACTION: UPGRADE — widen in-play surface to ALL parent runners (DB-first)
# 📆 PATCHED: 2026-01-26 — MSC_INPLAY visibility fixed (parent-anchored snapshot)
# ==============================================================================

def get_v7_inplay_snapshot(market_id: str):
    """
    DB-first, schema-truthful snapshot of ALL runners in a market
    relevant to MSC_INPLAY decisioning.

    UPGRADE:
    - Anchor visibility on *orders* (parents today), not inbound_oc_cache
    - inbound_oc_cache / intel / position are enrichments, not gates
    - No execution logic
    - No time gating (handled upstream)
    """

    from engines.config_paths import auto_conn
    from datetime import datetime, timezone
    import sqlite3

    con = auto_conn(rw=False)
    con.row_factory = sqlite3.Row

    try:
        rows = con.execute(
            """
            WITH parent_runners AS (
                SELECT DISTINCT
                    p.marketId,
                    p.selectionId
                FROM orders p
                WHERE p.role = 'PARENT'
                  AND date(p.opened_at) = date('now','utc')
                  AND p.marketId = ?
            )
            SELECT
                pr.marketId,
                pr.selectionId,

                -- === ODDS (BEST AVAILABLE, NON-GATING) ===
                COALESCE(oc.oc1, oc.anchor_odd)        AS odds,

                -- === V7 INTELLIGENCE (OPTIONAL) ===
                mi.fav_rank_entry                      AS fav_rank,
                mi.success                             AS success,
                mi.weight                              AS weight,
                mi.drift_pct                           AS drift_pct,
                mi.actual_drift_pct                    AS actual_drift_pct,
                mi.reversal_flag                       AS reversal_flag,
                mi.mto_minutes                         AS mto_minutes,

                -- === IN-PLAY POSITION (OPTIONAL) ===
                pos.pos_inplay                         AS pos_inplay,
                pos.anchor_odd                         AS anchor_odd,
                pos.drift_ratio                        AS drift_ratio

            FROM parent_runners pr

            LEFT JOIN inbound_oc_cache oc
                   ON oc.marketId = pr.marketId
                  AND oc.selectionId = pr.selectionId

            LEFT JOIN v_mastery_intel_v7 mi
                   ON mi.marketId = pr.marketId
                  AND mi.selectionId = pr.selectionId
                  AND mi.mode = 'LIVE'

            LEFT JOIN v7_race_position_inferred pos
                   ON pos.marketId = pr.marketId
                  AND pos.selectionId = pr.selectionId

            ORDER BY odds ASC
            """,
            (str(market_id),),
        ).fetchall()

    finally:
        con.close()

    snapshot = []
    now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for r in rows:
        mto = r["mto_minutes"]

        # Diagnostic race quartile (unchanged)
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

            # odds (engine decides if None is acceptable)
            "odds": float(r["odds"]) if r["odds"] is not None else None,

            # intelligence (TYPE NORMALISED)
            "fav_rank": int(r["fav_rank"]) if r["fav_rank"] is not None else None,
            "is_favourite": int(r["fav_rank"]) == 1 if r["fav_rank"] is not None else False,
            "success": float(r["success"]) if r["success"] is not None else None,
            "weight": float(r["weight"]) if r["weight"] is not None else None,

            # drift / collapse (TYPE NORMALISED)
            "anchor_odd": float(r["anchor_odd"]) if r["anchor_odd"] is not None else None,
            "drift_ratio": float(r["drift_ratio"]) if r["drift_ratio"] is not None else None,
            "drift_pct": float(r["drift_pct"]) if r["drift_pct"] is not None else None,
            "actual_drift_pct": float(r["actual_drift_pct"]) if r["actual_drift_pct"] is not None else None,
            "reversal_flag": bool(r["reversal_flag"]),

            # timing (TYPE NORMALISED)
            "mto_minutes": float(mto) if mto is not None else None,
            "race_quartile": race_quartile,

            # in-play position (TYPE NORMALISED)
            "pos_inplay": int(r["pos_inplay"]) if r["pos_inplay"] is not None else None,

            # diagnostics
            "ts_utc": now_utc,
        })


    return snapshot

# === PATCH END ==============================================================


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

# ============================================================================
# CANONICAL ODDS LOOKUP — BUS_ROUTE AUTHORITY
# ============================================================================
def get_runner_odds_map(
    pairs: List[Tuple[str, str]],
    session_token: str | None = None,
):
    """
    Canonical odds resolver for BUS.

    Given a list of (marketId, selectionId), return the
    best-known odds using the BusRoute odds stack.

    - Betfair API via fetch_live_odds
    - No Scope
    - No MarketMonitor
    - No DB dependency
    - Safe to call every tick

    Returns:
        dict[(marketId, selectionId)] = {
            "px": float,
            "back": float | None,
            "lay": float | None,
        }
    """

    odds_map = {}

    for mid, sid in pairs:
        try:
            odds = fetch_live_odds(
                session_token=session_token,
                marketId=str(mid),
                selectionId=str(sid),
            ) or {}

            back = odds.get("back")
            lay  = odds.get("lay")

            # Choose execution px
            px = back or lay
            if px is None:
                continue

            odds_map[(str(mid), str(sid))] = {
                "px": float(px),
                "back": back,
                "lay": lay,
            }

        except Exception:
            # Fail silent — BUS will simply not evaluate this runner
            continue

    return odds_map



# ======================================================================================================
# 📍 TARGET: engines/api_tools.py
# 🔎 ANCHOR: end of file (before __main__ or final EOF)
# 🧩 ACTION: ADD
# 📆 PATCHED: 2026-03-15 — BUS live loop starter (authoritative)
#
# PURPOSE:
# - Allow api_tools users to start BUS without orchestrator
# - Ensure BUS ticks advance when api_tools is imported or run
# - Preserve orchestrator ownership in production
#
# CONTRACT:
# - BUS remains execution authority
# - Placement worker must already be running
# - This starts ONLY the BUS loop
# ======================================================================================================
def start_bus_loop():
    """
    Route bootstrap only.
    No loop.
    No timing.
    No ticking.
    """
    from engines.bus.bus import BUS

    BUS._route_snapshot = BusRouteSnapshot()
    BUS._route_snapshot.build_route()
    BUS._route_snapshot.partition_into_bus_stops()

    BUS._route_ctx_map = BUS._route_snapshot.get_ctx_map()


    print("[BUS_ROUTE] route initialised (no loop)")



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
        # ✅ V7 BUS ROUTE CTX
        # ------------------------------------------------------------------
        print("\n=== BUS ROUTE CTX ===\n")
        snap = BusRouteSnapshot()
        snap.build_route()
        ctx_map = snap.get_ctx_map()


        # pick any runner from route
        (mid, sid), ctx = next(iter(ctx_map.items()))

        print("\n=== CTX PROOF ===")
        print("marketId      :", ctx.get("marketId"))
        print("selectionId   :", ctx.get("selectionId"))
        print("px            :", ctx.get("px"))
        print("odds          :", ctx.get("odds"))
        print("band / fav    :", ctx.get("is_fav"), ctx.get("fav_rank"))
        print("bias          :", ctx.get("bias"), ctx.get("bias_dir"))
        print("slope_ppm     :", ctx.get("slope_ppm"))
        print("oc_momentum   :", ctx.get("oc_momentum_ticks"))
        print("legacy_parent :", ctx.get("legacy_parent_id"))
        print("=================\n")
        print("=== BUS ROUTE CTX ===\n")

        # ------------------------------------------------------------------
        # ✅ V7 BUS ROUTE SNAPSHOT
        # ------------------------------------------------------------------

        snap = BusRouteSnapshot()
        snap.build_route()
        snap.partition_into_bus_stops()

        print(f"Route {snap.route_id}")
        print(f"Total runners: {len(snap.get_all_runners())}")

        for t in range(1, 11):
            runners = snap.get_bus_stop(t)
            print(f"Tick {t}: {len(runners)} runners")
            for mid, sid in runners[:5]:
                print(" ", mid, sid)


        print("=== END BUS LOOKUPS ===\n")

        # ------------------------------------------------------------------
        # ✅ V7 BUS ROUTE LOOKUPS
        # ------------------------------------------------------------------

        print("RISK legacy parents:")
        for x in get_risk_legacy_parent_pairs()[:10]:
            print(" ", x)

        print("\nExploratory exclusions:")
        for x in list(get_exploratory_active_parent_pairs())[:10]:
            print(" ", x)
        print("=== END BUS LOOKUPS ===\n")

    except Exception as e:
        print(f"❌ Error: {e}")
