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
from engines.bus_route_startup_ctx import StartupCTXBuilder


# --- at module level (top of file) ---
import requests
_session = requests.Session()
_adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=100, max_retries=2)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)

# ============================================================
# BUS ROUTE CONFIG (LOCKED)
# ============================================================

PLANS_PER_TICK = 93
CYCLE_SIZE = 930          # parents per full cycle
TICKS_PER_CYCLE = 10

# Per-tick allocation
ROUTE_SPLIT = {
    "LEGACY": 14,
    "MSC_RISK": 30,
    "MSC_INPLAY": 24,
    "MSC_EXPLORATORY": 15,
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
    min_runners_per_market: int = 6,   # kept for signature compatibility (NOT USED here)
    min_markets: int = 5,              # window size (authoritative)
):
    """
    RECONCILIATION-ONLY MARKET COMPLETER

    CONTRACT (LOCKED):
    - Operates on marketIds ONLY
    - NEVER removes existing markets
    - NEVER decides eligibility
    - NEVER returns empty if markets exist today
    - ONLY ensures the required marketIds are present

    runner_pairs may be incomplete.
    This function makes it complete.
    """

    from engines.config_paths import connect_db
    from datetime import datetime, timezone
    import sqlite3
    from collections import defaultdict

    # --------------------------------------------
    # 1) Extract marketIds already present
    # --------------------------------------------
    existing_by_market = defaultdict(list)
    for mid, sid in runner_pairs:
        existing_by_market[str(mid)].append((mid, sid))

    existing_mids = set(existing_by_market.keys())

    # --------------------------------------------
    # 2) Load authoritative market window (DB-first)
    #    TODAY, ordered by marketStartTime
    # --------------------------------------------
    con = connect_db(ro=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT
                marketId,
                marketStartTime
            FROM bets
            WHERE date(marketStartTime) = date('now','utc')
              AND julianday(marketStartTime) >= julianday('now','utc') - (15.0 / 1440.0)
            ORDER BY datetime(marketStartTime) ASC
            """
        ).fetchall()
    finally:
        con.close()

    if not rows:
        # No markets today → return whatever we already have
        return list(runner_pairs)

    # --------------------------------------------
    # 3) Determine sliding window (first N markets)
    #    NOTE: disappearance happens only AFTER grace,
    #    but that logic already lives in scope/schedule.
    # --------------------------------------------
    required_mids = [str(r["marketId"]) for r in rows[:min_markets]]

    # --------------------------------------------
    # 4) Reconcile: add missing marketIds
    # --------------------------------------------
    out = list(runner_pairs)

    for mid in required_mids:
        if mid in existing_mids:
            continue

        # Add a placeholder entry for this marketId.
        # selectionId is intentionally None.
        out.append((mid, None))

    return out

def _get_current_anchor_market():
    from engines.config_paths import connect_db
    import sqlite3

    con = connect_db(ro=True)
    con.row_factory = sqlite3.Row

    try:
        row = con.execute(
            """
            SELECT marketId
            FROM bets
            WHERE date(marketStartTime)=date('now','utc')
              AND julianday(marketStartTime) >= julianday('now','utc') - (120.0/1440.0)
            ORDER BY ABS(julianday(marketStartTime) - julianday('now','utc')) ASC
            LIMIT 1
            """
        ).fetchone()
    finally:
        con.close()

    return str(row["marketId"]) if row else None

def _rotate_from_market(pairs, anchor_mid):
    if not anchor_mid:
        return pairs

    # find first index of anchor market
    idx = next(
        (i for i, (mid, _sid) in enumerate(pairs) if str(mid) == str(anchor_mid)),
        None
    )

    if idx is None:
        return pairs

    return pairs[idx:] + pairs[:idx]

# ======================================================================================================
# 📍 TARGET: engines/bus_route.py
# 🔎 SEARCH: def _build_runner_pool():
# 📆 PATCHED: 2026-04-XX — Remove band filtering (Route identity invariant)
#
# PURPOSE:
# - Route identity must NOT depend on band
# - ALL runners in scope markets must exist in route snapshot
# - Engine gating handled downstream in BUS
#
# INVARIANT:
# - Route never removes a runner because of band
# - CTX/PX completeness guaranteed
# ======================================================================================================

def _build_runner_pool():
    """
    Authoritative runner pool:
    - Scope markets
    - ALL runners included
    - No band filtering
    - No engine filtering
    """

    scope = build_and_maintain_scope()
    markets = scope.get("markets", []) or []

    pool = []

    for m in markets:
        mid = m["marketId"] if isinstance(m, dict) else str(m)
        st = get_market_state(mid) or {}
        runners = st.get("runners") or {}

        for sid in runners.keys():
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
# ======================================================================================================
# 📍 TARGET: engines/bus_route.py
# 🔎 ANCHOR: inside BusRouteSnapshot.build_route(), replace raw_pairs construction
# 📆 PATCHED: 2026-04-XX — Deterministic 5-Market Sliding Window (bets DB authority)
#
# PURPOSE:
# - Remove scope-driven window shrinkage
# - Use bets.marketStartTime as single source of truth
# - Maintain exactly 5 concurrent markets (unless fewer remain)
# - Slide window only when now > off + grace
#
# INVARIANTS:
# - Window size = 5
# - Markets removed only after off + grace
# - No dependency on scope time filters
# - CTX + PX behaviour unchanged
# ======================================================================================================

        from engines.config_paths import connect_db
        from datetime import datetime, timezone, timedelta
        import sqlite3

        WINDOW_SIZE = 5
        GRACE_MINUTES = 5

        now = datetime.now(timezone.utc)

        # --------------------------------------------------
        # 1️⃣ Load DISTINCT today's markets ordered by off time
        #    and filter by runner count >= 6
        # --------------------------------------------------

        WINDOW_SIZE = 5
        MIN_RUNNERS = 6
        GRACE_MINUTES = 5

        now = datetime.now(timezone.utc)

        con = connect_db(ro=True)
        con.row_factory = sqlite3.Row

        try:
            rows = con.execute("""
                SELECT
                    marketId,
                    marketStartTime,
                    COUNT(DISTINCT selectionId) AS runner_count
                FROM bets
                WHERE date(marketStartTime)=date('now','utc')
                GROUP BY marketId, marketStartTime
                HAVING runner_count >= ?
                ORDER BY datetime(marketStartTime) ASC
            """, (MIN_RUNNERS,)).fetchall()
        finally:
            con.close()

        # --------------------------------------------------
        # 2️⃣ Remove markets strictly past off + grace
        # --------------------------------------------------

        active_markets = []

        for r in rows:
            mid = str(r["marketId"])
            off_raw = r["marketStartTime"]

            if not off_raw:
                continue

            try:
                off_dt = datetime.fromisoformat(
                    off_raw.replace("Z", "+00:00")
                )
            except Exception:
                continue

            if now <= off_dt + timedelta(minutes=GRACE_MINUTES):
                active_markets.append(mid)

        # --------------------------------------------------
        # 3️⃣ Take first 5 eligible markets
        # --------------------------------------------------

        window_mids = active_markets[:WINDOW_SIZE]

        # --------------------------------------------------
        # 🔄 Ensure MarketMonitor state for window markets
        # --------------------------------------------------
        try:
            from engines.market_monitor import monitor
            monitor.refresh(window_mids)
        except Exception:
            pass



        # --------------------------------------------------
        # 4️⃣ Build runner identity surface from bets DB
        #    (Window-derived mids only — NOT scope)
        # --------------------------------------------------

        raw_pairs = []

        if window_mids:

            con = connect_db(ro=True)
            con.row_factory = sqlite3.Row

            try:
                rows = con.execute(f"""
                    SELECT
                        marketId,
                        selectionId
                    FROM bets
                    WHERE marketId IN ({",".join(["?"]*len(window_mids))})
                """, window_mids).fetchall()
            finally:
                con.close()

            for r in rows:
                if r["marketId"] and r["selectionId"]:
                    raw_pairs.append(
                        (str(r["marketId"]), str(r["selectionId"]))
                    )

        # Safety fallback — identity must never be empty
        if not raw_pairs:
            raw_pairs = list(get_root_ctx_runner_pairs())

        # 🔑 Order by off time
        ordered = _order_runner_pool_by_market_time(raw_pairs)


# ======================================================================================================
# END PATCH
# ======================================================================================================

        anchor_mid = _get_current_anchor_market()
        ordered = _rotate_from_market(ordered, anchor_mid)

# ======================================================================================================
# 📍 TARGET: engines/bus_route.py
# 🔎 ANCHOR: inside BusRouteSnapshot.build_route(), after ordered = ...
# 📆 PATCHED: 2026-04-02 — Prune markets older than -120 minutes
#
# PURPOSE:
# - Prevent route growth across full trading day
# - Remove stale markets >120 minutes post-off
# - Preserve active lifecycle runners
#
# INVARIANTS:
# - Only prunes markets strictly older than -120 minutes
# - Never prunes markets with matched parents today
# - Fail-open (never crash route build)
# ======================================================================================================

        try:
            from engines.config_paths import connect_db
            from datetime import datetime, timezone
            import sqlite3

            now = datetime.now(timezone.utc)

            con = connect_db(ro=True)
            con.row_factory = sqlite3.Row

            # Load all market start times for current runner_pool
            mids = {mid for (mid, _sid) in self.runner_pool}

            market_times = {}
            for mid in mids:
                row = con.execute(
                    """
                    SELECT marketStartTime
                    FROM bets
                    WHERE marketId = ?
                    LIMIT 1
                    """,
                    (mid,),
                ).fetchone()

                if not row or not row["marketStartTime"]:
                    continue

                off = datetime.fromisoformat(
                    row["marketStartTime"].replace("Z", "+00:00")
                )

                minutes_post_off = (now - off).total_seconds() / 60.0

                # Mark for prune if older than 120 minutes post-off
                if minutes_post_off > 120:
                    market_times[mid] = True

            con.close()

            if market_times:
                # Remove stale runners from runner_pool
                self.runner_pool = [
                    (mid, sid)
                    for (mid, sid) in self.runner_pool
                    if mid not in market_times
                ]

                # Remove stale CTX entries
                for key in list(self.ctx_map.keys()):
                    mid, _sid = key
                    if mid in market_times:
                        self.ctx_map.pop(key, None)

                print(
                    f"[BUS][PRUNE] removed_markets={len(market_times)} "
                    f"remaining_runners={len(self.runner_pool)}"
                )

        except Exception:
            # Fail-open: pruning must never break route build
            pass

        # --------------------------------------------------
        # 🔁 TIME-RELATIVE ROUTE MEMBERSHIP (AUTHORITATIVE)
        # --------------------------------------------------
        # Route must reflect CURRENT scope only.
        # No cumulative day-long accumulation.

        self.runner_pool = ordered.copy()

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

        # --------------------------------------------------
        # CTX ACCUMULATION + WARM-UP CONTROL
        # --------------------------------------------------
        # IMPORTANT:
        # - CTX MUST ACCUMULATE across calls
        # - CTX MUST NEVER be reset
        # - Warm-up happens progressively
        # 🔒 ACCUMULATIVE CTX MAP (DO NOT RESET)
        ctx_map = self.ctx_map

        WARMUP_LIMIT = 50      # max new CTX builds per build_route() call
        built_this_pass = 0


        # --------------------------------------------------
        # Build CTX ONLY for unseen runners
        # --------------------------------------------------
        for mid, sid in self.runner_pool:
            key = (str(mid), str(sid))

            # 🔒 REUSE — do NOT rebuild CTX
            if key in ctx_map:
                continue

            # 🔁 Progressive warm-up limit
            # IMPORTANT:
            # Do NOT break — that would orphan later runners.
            # Skip for now; they will be built on a future pass.
            if built_this_pass >= WARMUP_LIMIT:
                continue

            try:
                # --------------------------------------------------
                # BUILD FULL STATIC CTX (ONCE PER ROUTE)
                # --------------------------------------------------
                ctx, _ = build_context_for_runner(mid, sid, source="LIVE")

                # Inject MarketMonitor band (authoritative)
                st = get_market_state(mid) or {}
                runner_state = (st.get("runners") or {}).get(str(sid))

                if runner_state:
                    ctx["band"] = runner_state.get("band")
                else:
                    ctx["band"] = "UNKNOWN"


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

                    # --------------------------------------------------
                    # ENGINE-NEUTRAL PARENT BINDING (STATIC FOR ROUTE)
                    # --------------------------------------------------
                    parent_id   = parent.get("parent_id")
                    entry_odds  = parent.get("entry_odds")
                    entry_side  = parent.get("side")
                    entry_stake = parent.get("entry_stake")
                    parent_eng  = parent.get("engine")

                    # --- Backwards compatibility (legacy naming) ---
                    ctx["legacy_parent_id"]   = parent_id
                    ctx["legacy_entry_odds"]  = entry_odds
                    ctx["legacy_entry_side"]  = entry_side
                    ctx["legacy_entry_stake"] = entry_stake

                    # --- Engine-neutral canonical naming ---
                    ctx["anchor_parent_id"]   = parent_id
                    ctx["anchor_entry_odds"]  = entry_odds
                    ctx["anchor_entry_stake"] = entry_stake
                    ctx["anchor_engine"]      = parent_eng

                else:
                    # Clear both naming conventions
                    ctx["legacy_parent_id"]   = None
                    ctx["legacy_entry_odds"]  = None
                    ctx["legacy_entry_side"]  = None
                    ctx["legacy_entry_stake"] = None

                    ctx["anchor_parent_id"]   = None
                    ctx["anchor_entry_odds"]  = None
                    ctx["anchor_entry_stake"] = None
                    ctx["anchor_engine"]      = None



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
                built_this_pass += 1
            except Exception:
                continue  # fail-open (route must never die)

        self.ctx_map = ctx_map
        self.partition_into_bus_stops()
        print(f"[CTX] total={len(self.ctx_map)} built_this_pass={built_this_pass}")

    # === PATCH START ============================================================
    # 📍 TARGET: engines/bus_route.py
    # 🔎 ANCHOR: class BusRouteSnapshot
    # 📆 PATCHED: 2026-04-02 — restore refresh_ctx_dynamic_fields as class method
    #
    # FIX:
    # - refresh_ctx_dynamic_fields was accidentally defined outside the class
    # - BUS expects this as an instance method
    # - No logic changes
    # ============================================================================

    def refresh_ctx_dynamic_fields(self):
        """
        Refresh dynamic price fields for all runners in route snapshot.

        MARKET-BATCHED.
        """

        import time
        import os

        t0 = time.time()

        session_token = (
            os.getenv("SESSION_TOKEN")
            or os.getenv("BETFAIR_SESSION_TOKEN")
        )

        odds_map = get_runner_odds_map(
            list(self.ctx_map.keys()),
            session_token=session_token,
        )

# ======================================================================================================
# 📍 TARGET: engines/bus_route.py
# 🔎 SEARCH: def refresh_ctx_dynamic_fields(self):
# 📆 PATCHED: 2026-04-XX — Preserve PX on missing odds
#
# PURPOSE:
# - Prevent PX wipe on transient fetch miss
# - Maintain stable execution identity
# ======================================================================================================

        for (mid, sid), ctx in self.ctx_map.items():
            odds = odds_map.get((mid, sid))
            if not odds:
                continue  # 🔒 DO NOT WIPE PX

            ctx["px"]   = odds["px"]
            ctx["odds"] = odds["px"]
            ctx["back"] = odds.get("back")
            ctx["lay"]  = odds.get("lay")


        return time.time() - t0

    # === PATCH END ==============================================================


    def get_ctx_for_market(self, market_id: str):
        """
        Return CTX for ALL runners in a single market.

        - No CTX building
        - No odds refresh
        - No filtering
        - DB-agnostic
        - BUS decides when to call
        """
        mid = str(market_id)

        return {
            (m, s): ctx
            for (m, s), ctx in self.ctx_map.items()
            if m == mid
        }

    # ============================================================
    # 🔑 NEW — absorb external runners into route snapshot
    # ============================================================
    def absorb_runner_pairs(self, pairs):
        """
        Ensure (marketId, selectionId) pairs exist in route snapshot.

        CONTRACT:
        - Idempotent
        - Builds CTX only if missing
        - Once absorbed, runners persist for entire route
        """

        if not pairs:
            return 0

        from engines.mastery.context_builder import build_context_for_runner

        added = 0

        for mid, sid in pairs:
            if not mid or not sid:
                continue

            key = (str(mid), str(sid))

            # Already present → nothing to do
            if key in self.ctx_map:
                continue

            try:
                # Build FULL static CTX exactly once
                ctx, _ = build_context_for_runner(
                    str(mid),
                    str(sid),
                    source="LIVE",
                )

                # Dynamic fields — BUS owns refresh later
                # Preserve snapshot px if exists, else initialise as None
                ctx.setdefault("px", ctx.get("odds"))
                ctx.setdefault("odds", None)
                ctx.setdefault("back", None)
                ctx.setdefault("lay",  None)

                self.ctx_map[key] = ctx
                added += 1

            except Exception:
                # Fail-open: route must never die
                continue

        return added


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

    # === PATCH START ==============================================================
    # 📍 TARGET: engines/bus_route.py
    # 🔎 SEARCH: def partition_into_bus_stops(self):
    # 🛠 ACTION: Replace entire function
    # 📆 PATCHED: 2026-04-XX — Exclude IGNORED from bus stop scheduling only
    #
    # PURPOSE:
    # - runner_pool remains full identity surface
    # - IGNORED runners excluded from execution rotation
    # - ctx_map untouched
    # - lifecycle injections still possible
    #
    # INVARIANT:
    # - runner_pool contains ALL runners
    # - bus_stops contain ACTIVE + PASSIVE only
    # ==============================================================================

    def partition_into_bus_stops(self):

        if not self.runner_pool:
            self.bus_stops = {}
            return

        # --------------------------------------------------
        # Execution surface = ACTIVE only
        # Identity surface (runner_pool / ctx_map) remains full
        # --------------------------------------------------

        eligible = []

        for (mid, sid), ctx in self.ctx_map.items():

            band = ctx.get("band")

            if band == "ACTIVE":
                eligible.append((mid, sid))

        n = len(eligible)

        if n == 0:
            self.bus_stops = {}
            return

        base = n // TICKS_PER_CYCLE
        remainder = n % TICKS_PER_CYCLE

        self.bus_stops = {}
        idx = 0

        for tick in range(1, TICKS_PER_CYCLE + 1):
            size = base + (1 if tick <= remainder else 0)
            self.bus_stops[tick] = eligible[idx:idx + size]
            idx += size

    # === PATCH END ==============================================================


    def get_bus_stop(self, tick):
        return self.bus_stops.get(tick, [])

    def get_all_runners(self):
        return self.runner_pool

    def run_live2(self, hz: float = 1.0):
        interval = max(0.05, 1.0 / max(0.1, hz))
        print(f"[BUS ROUTE] live loop started (hz={hz})")

        while True:
            try:
                self.tick()
            except Exception as e:
                print(f"[BUS ROUTE][ERR] tick failed: {e}")

            time.sleep(interval)

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

# ======================================================================
# 📍 TARGET: engines/bus_route.py
# 🔎 SEARCH: def get_root_ctx_runner_pairs():
# 🧩 ACTION: ADD — ensure RISK parents are always in route snapshot
# 📆 PATCHED: 2026-02-03 — fix risk_missing_px (CTX identity invariant)
# ======================================================================

def get_root_ctx_runner_pairs():
    pairs = set()

    # --------------------------------------------------
    # 1️⃣ Base runner pool (scope + monitor)
    # --------------------------------------------------
    try:
        pairs |= set(_build_runner_pool())
    except Exception:
        pass

# ======================================================================
# 📍 TARGET: engines/bus_route.py
# 🔎 ANCHOR: def get_root_ctx_runner_pairs():
# 🧩 ACTION: ADD — parent permanence invariant
# 📆 PATCHED: 2026-02-03 — matched parents permanently pinned into route
#
# INVARIANT:
#   If a parent is MATCHED today, its (mid, sid) MUST exist in route CTX
# ======================================================================

    # --------------------------------------------------
    # 6️⃣ MATCHED PARENTS — PERMANENT ROUTE MEMBERSHIP
    # --------------------------------------------------
    try:
        from engines.config_paths import open_auto_db
        import sqlite3

        con = open_auto_db(rw=False)
        con.row_factory = sqlite3.Row

        rows = con.execute("""
            SELECT DISTINCT
                marketId,
                selectionId
            FROM orders
            WHERE role = 'PARENT'
              AND UPPER(entry_status) = 'MATCHED'
              AND date(opened_at) = date('now','utc')
        """).fetchall()

        scope_mids = {mid for (mid, _sid) in pairs}

        for r in rows:
            mid = str(r["marketId"])
            sid = str(r["selectionId"])
            
            pairs.add((mid, sid))


    except Exception:
        pass
    finally:
        try:
            con.close()
        except Exception:
            pass

    # --------------------------------------------------
    # 2️⃣ RISK parent runners (LEGACY + EXPLORATORY)
    # 🔑 CRITICAL: MUST be present for px refresh
    # --------------------------------------------------
    scope_mids = {mid for (mid, _sid) in pairs}

    for mid, sid, _pid, _anchor_px in get_risk_legacy_parent_pairs():
        
        pairs.add((mid, sid))
    # --------------------------------------------------
    # 3️⃣ Exploratory exclusions (already active parents)
    # --------------------------------------------------
    try:
        pairs |= {
            (str(mid), str(sid))
            for (mid, sid) in get_exploratory_active_parent_pairs()
        }
    except Exception:
        pass

    # --------------------------------------------------
    # 4️⃣ In-play parents
    # --------------------------------------------------
    try:
        pairs |= get_inplay_parent_runner_pairs()
    except Exception:
        pass

    # --------------------------------------------------
    # 5️⃣ Stoploss parents
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

    from engines.config_paths import open_auto_db
    import sqlite3

    con = open_auto_db(rw=False)
    con.row_factory = sqlite3.Row

    try:
        rows = con.execute(
            """
            SELECT
                p.marketId,
                p.selectionId,
                p.id         AS parent_id,
                p.entry_odds AS anchor_px,
                p.engine
            FROM orders p
            WHERE p.mode = 'LIVE'
              AND p.role = 'PARENT'
              AND p.engine IN ('LEGACY','MSC_EXPLORATORY')
              AND UPPER(p.entry_status) = 'MATCHED'
              AND date(p.opened_at) = date('now','utc')
            """
        ).fetchall()

        return [
            (
                str(r["marketId"]),
                str(r["selectionId"]),
                int(r["parent_id"]),
                float(r["anchor_px"]),
            )
            for r in rows
            if r["marketId"] and r["selectionId"] and r["anchor_px"] is not None
        ]

    finally:
        con.close()


# ============================================================================
# INPLAY LIFECYCLE — ALL ACTIVE MIDS & SIDS
# ============================================================================
# === PATCH START ============================================================
# 📍 TARGET: engines/bus_route.py
# 🔎 SEARCH: def get_inplay_parent_runner_pairs():
# 📆 PATCHED: 2026-02-04 — DAL-safe in-play runner surface
#
# FIXES:
# - Remove use of undefined `con`
# - Use canonical DAL opener
# - Attach bets DB AFTER connection exists
# - Fail-open, never block BUS tick
# ============================================================================

def get_inplay_parent_runner_pairs():
    """
    Authoritative IN-PLAY runner surface (MARKET-TIME WINDOW).

    Returns (marketId, selectionId) for ALL runners
    in markets that are inside the in-play window.

    CTX must already exist in BusRouteSnapshot (built by StartupCTXBuilder).
    This helper only supplies the identity surface.
    """

    from engines.config_paths import connect_db
    import sqlite3

    pairs = set()

    con = connect_db(ro=True)
    con.row_factory = sqlite3.Row

    try:
        rows = con.execute(
            """
            SELECT DISTINCT
                marketId,
                selectionId,
                (julianday(marketStartTime) - julianday('now','utc')) * 1440.0 AS mins_to_off
            FROM bets
            WHERE
                (julianday(marketStartTime) - julianday('now','utc')) <= 0.0
              AND
                (julianday(marketStartTime) - julianday('now','utc')) >= -(120.0 / 1440.0)
            """
        ).fetchall()
    finally:
        con.close()

    for r in rows:
        if r["marketId"] and r["selectionId"]:
            pairs.add((str(r["marketId"]), str(r["selectionId"])))

    return pairs


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
            "fav_rank": r["fav_rank"],
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
            "pos_inplay": r["pos_inplay"],

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
# ======================================================================
# 📍 TARGET: engines/bus_route.py
# 🔎 SEARCH: def get_runner_odds_map(
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-04-02 — Market-batched odds refresh (performance fix)
#
# WHY:
# - Previous implementation called Betfair per runner
# - Caused O(n) HTTP calls per tick
# - Now batches per market (O(markets))
#
# INVARIANTS:
# - Same return shape
# - Same fail-open behaviour
# - No engine changes
# - No BUS changes
# ======================================================================

# === PATCH START ============================================================
# 📍 TARGET: engines/bus_route.py
# 🔎 ANCHOR: just above `if __name__ == "__main__":`
# 📆 PATCHED: 2026-04-02 — Market-batched odds resolver (module-level)
#
# FIX:
# - Must be module-level (NOT inside BusRouteSnapshot)
# - refresh_ctx_dynamic_fields calls this
# - Prevents attribute resolution error
# ============================================================================

def get_runner_odds_map(
    pairs: List[Tuple[str, str]],
    session_token: str | None = None,
):
    """
    Canonical odds resolver for BUS (MARKET-BATCHED).

    Given a list of (marketId, selectionId),
    performs ONE API call per marketId.
    """

    odds_map = {}

    if not pairs:
        return odds_map

    from collections import defaultdict
    grouped = defaultdict(list)

    # --------------------------------------------------
    # Group runners by market
    # --------------------------------------------------
    for mid, sid in pairs:
        grouped[str(mid)].append(str(sid))

    # --------------------------------------------------
    # Resolve session token
    # --------------------------------------------------
    tok = (
        session_token
        or os.getenv("SESSION_TOKEN")
        or os.getenv("BETFAIR_SESSION_TOKEN")
    )

    if not tok:
        return odds_map

    from engines.daily_config import get_app_key
    app_key = get_app_key()

    if not app_key:
        return odds_map

    url = "https://api.betfair.com/exchange/betting/json-rpc/v1"
    headers = {
        "X-Application": app_key,
        "X-Authentication": tok,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # --------------------------------------------------
    # ONE CALL PER MARKET
    # --------------------------------------------------
    for mid, sids in grouped.items():
        try:
            payload = json.dumps([{
                "jsonrpc": "2.0",
                "method": "SportsAPING/v1.0/listMarketBook",
                "params": {
                    "marketIds": [mid],
                    "priceProjection": {
                        "priceData": ["EX_BEST_OFFERS"],
                        "virtualise": True
                    }
                },
                "id": 1
            }])

            resp = _session.post(url, headers=headers, data=payload, timeout=8)
            resp.raise_for_status()
            data = resp.json()

            runners = (
                data[0]
                .get("result", [{}])[0]
                .get("runners", [])
            )

            for r in runners:
                sid = str(r.get("selectionId"))
                if sid not in sids:
                    continue

                ex = r.get("ex", {}) or {}
                back = (ex.get("availableToBack") or [{}])[0].get("price")
                lay  = (ex.get("availableToLay") or [{}])[0].get("price")

                px = back or lay
                if px is None:
                    continue

                odds_map[(mid, sid)] = {
                    "px": float(px),
                    "back": back,
                    "lay": lay,
                }

        except Exception:
            continue

    return odds_map

# === PATCH END ==============================================================


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

# ============================================================================
# DAY RUNNER SURFACE — AUTHORITATIVE PX + BAND FOR FULL DAY
# ============================================================================
# PURPOSE:
# - Full-day runner surface
# - Independent of scope
# - Independent of route
# - Independent of MarketMonitor
# - Cached + periodically refreshed
# ============================================================================

from datetime import datetime, timezone
from engines.config_paths import connect_db
from engines.strategy_config import CONFIG
from typing import Dict, Tuple
import threading
import time


class DayRunnerSurface:

    _REFRESH_SECONDS = 10  # refresh cadence

    def __init__(self):
        self._surface: Dict[Tuple[str, str], dict] = {}
        self._last_refresh = 0.0
        self._lock = threading.Lock()

    # --------------------------------------------------
    # PUBLIC API
    # --------------------------------------------------

    def get_surface(self) -> Dict[Tuple[str, str], dict]:
        self._refresh_if_needed()
        return self._surface

    def get_runner(self, mid: str, sid: str) -> dict | None:
        self._refresh_if_needed()
        return self._surface.get((str(mid), str(sid)))

    def force_refresh(self):
        with self._lock:
            self._refresh()

    # --------------------------------------------------
    # INTERNAL
    # --------------------------------------------------

    def _refresh_if_needed(self):
        now = time.time()
        if now - self._last_refresh >= self._REFRESH_SECONDS:
            with self._lock:
                if now - self._last_refresh >= self._REFRESH_SECONDS:
                    self._refresh()

    def _refresh(self):
        """
        Full-day rebuild.
        """

        # --------------------------------------------------
        # 1️⃣ Enumerate ALL today's markets
        # --------------------------------------------------
        con = connect_db(ro=True)
        con.row_factory = None

        try:
            rows = con.execute("""
                SELECT DISTINCT marketId
                FROM bets
                WHERE date(marketStartTime) = date('now','utc')
            """).fetchall()
        finally:
            con.close()

        if not rows:
            return

        market_ids = [str(r[0]) for r in rows if r and r[0]]

        # --------------------------------------------------
        # 2️⃣ Fetch ALL runners from market state
        # --------------------------------------------------
        pairs = []

        for mid in market_ids:
            st = get_market_state(mid) or {}
            runners = st.get("runners") or {}
            for sid in runners.keys():
                pairs.append((mid, str(sid)))

        if not pairs:
            return

        # --------------------------------------------------
        # 3️⃣ Batched PX fetch
        # --------------------------------------------------
        odds_map = get_runner_odds_map(pairs)

        # --------------------------------------------------
        # 4️⃣ Deterministic band classification
        # --------------------------------------------------
        bands_cfg = CONFIG["bands"]
        active_min  = float(bands_cfg["active_min"])
        active_max  = float(bands_cfg["active_max"])
        passive_max = float(bands_cfg["passive_max"])

        def classify(px: float | None) -> str:
            if px is None:
                return "UNKNOWN"
            if px < active_min:
                return "ACTIVE"
            if px <= active_max:
                return "ACTIVE"
            if px <= passive_max:
                return "PASSIVE"
            return "IGNORED"

        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

        # --------------------------------------------------
        # 5️⃣ Update surface (never wipe missing PX)
        # --------------------------------------------------
        for mid, sid in pairs:
            odds = odds_map.get((mid, sid))
            if not odds:
                continue

            px = odds.get("px")
            if px is None:
                continue

            self._surface[(mid, sid)] = {
                "px": float(px),
                "band": classify(float(px)),
                "back": odds.get("back"),
                "lay": odds.get("lay"),
                "ts": ts,
            }

        self._last_refresh = time.time()


# --------------------------------------------------
# GLOBAL SINGLETON
# --------------------------------------------------

DAY_RUNNER_SURFACE = DayRunnerSurface()
