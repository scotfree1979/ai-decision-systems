# engines/odds/providers/db_fallback_provider.py
from __future__ import annotations

import os
import json
import sqlite3
from typing import List, Dict, Any, Optional
from engines.fallbackscope import _next5_from_bets_with_active
# --- DB path resolution (no guessing if the project helper is present) -------
try:
    from engines.config_paths import open_local_readonly_auto  # returns path to autoscalp_gui.db
except Exception:
    def autoscalp_db() -> str:
        # Last-ditch fallback only if the project helper is unavailable
        return os.environ.get("AUTOSCALP_DB", os.path.join("data", "autoscalp_gui.db"))


# --- Safe connection ----------------------------------------------------------
def _connect_ro(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=3.0, isolation_level=None)
    con.row_factory = sqlite3.Row
    return con


# --- Core API: used by odds_service.tick_update_for_scope ---------------------
# === PATCH START ===
# 📍 TARGET: engines/odds/providers/db_fallback_provider.py
# 🔎 SEARCH: ^def fetch_market_book\(market_id: str, recency_seconds: int = 300\) -> List\[Dict\[str, Any\]\]:
# ⛏️ ACTION: replace entire function with this tested version

# === PATCH START ===
# 📍 TARGET: engines/odds/providers/db_fallback_provider.py
# 📆 PATCHED: 2025-11-17Z — use raw LOCAL reader (never DAL, never cloud)

def fetch_market_book(market_id: str, recency_seconds: int = 300) -> list[dict]:
    """
    Return latest odds per selection for a market from inbound_oc_cache.
    Always read LOCAL autoscalp_gui.db via raw read-only connector.
    """
    from engines.config_paths import open_local_readonly_auto
    import sqlite3, json

    try:
        con = open_local_readonly_auto()   # ✅ REAL sqlite3 RO LOCAL connection
        rows = con.execute("""
            WITH latest AS (
              SELECT marketId, selectionId, MAX(id) AS max_id
                FROM inbound_oc_cache
               WHERE marketId = ?
               GROUP BY marketId, selectionId
            )
            SELECT i.selectionId,
                   COALESCE(i.oc1, i.anchor_odd) AS ltp,
                   i.oc1_band_json
              FROM inbound_oc_cache i
              JOIN latest l ON i.id = l.max_id
        """, (market_id,)).fetchall() or []
        con.close()
    except Exception as e:
        print(f"[DB_FMB] fetch_market_book warn mid={market_id}: {e}")
        return []

    out = []
    for r in rows:
        ltp = r["ltp"]
        if ltp is None:
            bj = r["oc1_band_json"]
            if bj:
                try:
                    arr = json.loads(bj)
                    if isinstance(arr, list) and arr:
                        ltp = float(arr[-1])
                except Exception:
                    pass
        if ltp is not None:
            out.append({"selectionId": str(r["selectionId"]), "ltp": float(ltp)})
    return out
# === PATCH END ===



    def _fetch(with_window: bool) -> List[Dict[str, Any]]:
        window = f"-{int(recency_seconds)} seconds"
        sql = f"""
        WITH t AS (
          SELECT
            marketId, selectionId, id, oc1, anchor_odd, oc1_band_json,
            COALESCE(
              datetime(last_sync_ts),
              datetime(replace(replace(last_sync_ts,'T',' '),'Z','')),
              datetime(substr(last_sync_ts,1,19))
            ) AS ts_norm
          FROM inbound_oc_cache
          WHERE marketId = ?
        ),
        j AS (
          SELECT selectionId, MAX(id) AS max_id
            FROM t
           {"WHERE ts_norm >= datetime('now','utc', ?)" if with_window else ""}
           GROUP BY selectionId
        )
        SELECT t.selectionId     AS selectionId,
               t.oc1             AS oc1,
               t.anchor_odd      AS anchor_odd,
               t.oc1_band_json   AS oc1_band_json
          FROM t
          JOIN j ON j.selectionId = t.selectionId AND j.max_id = t.id
        """
        params = (market_id,) if not with_window else (market_id, window)
        rows = con.execute(sql, params).fetchall() or []

        out: List[Dict[str, Any]] = []
        for r in rows:
            ltp = r["oc1"]
            if ltp is None:
                ltp = r["anchor_odd"]
            if ltp is None:
                bj = r["oc1_band_json"]
                if bj:
                    try:
                        arr = json.loads(bj) or []
                        if isinstance(arr, list) and arr:
                            ltp = float(arr[-1])
                    except Exception:
                        ltp = None
            if ltp is None:
                continue
            try:
                out.append({"selectionId": str(r["selectionId"]), "ltp": float(ltp)})
            except Exception:
                continue
        return out

    try:
        res = _fetch(with_window=True)
        if not res:
            res = _fetch(with_window=False)
        return res
    except Exception:
        return []
    finally:
        try:
            con.close()
        except Exception:
            pass
# === PATCH END ===


# --- Standalone helpers for a quick scope sanity run --------------------------
def _scope_snapshot(ahead_min: int = 30, lookback_min: int = 5, inbound_fresh_sec: int = 120, max_markets: int = 40) -> Dict[str, Any]:
    """
    Prefer your project scope if available. Otherwise, fall back to odds_current freshness.
    Returns: {'markets': [{'marketId': str, 'off_ts': str|None}]}
    """
    # Try to use the project’s scope first
    try:
        from engines.decision_engine.decide_once.scope import read_scope_window  # type: ignore
        snap = read_scope_window(ahead_min=ahead_min, lookback_min=lookback_min,
                                 inbound_fresh_sec=inbound_fresh_sec, max_markets=max_markets)
        # Ensure minimal shape
        mkts = []
        for m in (snap.get("markets") or []):
            mkts.append({"marketId": str(m.get("marketId")), "off_ts": m.get("off_ts")})
        return {"markets": mkts}
    except Exception:
        pass

    # If still empty, fallback to bets.db next-5
    if not mkts:
        try:
            mids = _next5_from_bets_with_active(max_items=max_markets)
            mkts = [{"marketId": m, "off_ts": None} for m in mids]
        except Exception:
            mkts = []

    # Fallback: infer markets from odds_current recency
    try:
        con = _connect_ro(autoscalp_db())
        rows = con.execute("""
            SELECT marketId, MAX(updated_ts) AS mx
              FROM odds_current
             WHERE datetime(updated_ts) >= datetime('now','utc', ?)
             GROUP BY marketId
             ORDER BY MAX(datetime(updated_ts)) DESC
             LIMIT ?
        """, (f"-{int(inbound_fresh_sec)} seconds", int(max_markets))).fetchall() or []
        mkts = [{"marketId": str(r["marketId"]), "off_ts": None} for r in rows]
        con.close()
        return {"markets": mkts}
    except Exception:
        return {"markets": []}


def _active_sids_from_fmb(mid: str, *, band_min: float = 1.5, band_max: float = 12.0, recency_seconds: int = 300) -> List[str]:
    """
    Use fetch_market_book(mid) and select SIDs in the active price band.
    """
    rows = fetch_market_book(mid, recency_seconds=recency_seconds) or []
    sids: List[str] = []
    for r in rows:
        try:
            px = float(r.get("ltp", 0.0))
            if band_min <= px <= band_max:
                sids.append(str(r.get("selectionId")))
        except Exception:
            continue
    return sids


# --- CLI / module entrypoint --------------------------------------------------
def _print_in_scope_active(*, ahead_min: int = 30, lookback_min: int = 5,
                           inbound_fresh_sec: int = 120, max_markets: int = 40,
                           recency_seconds: int = 300, band_min: float = 1.5, band_max: float = 12.0) -> None:
    """
    Prints in-scope marketIds and active SIDs (ltp ∈ [band_min, band_max]) using fetch_market_book.
    """
    snap = _scope_snapshot(ahead_min=ahead_min, lookback_min=lookback_min,
                           inbound_fresh_sec=inbound_fresh_sec, max_markets=max_markets)
    mkts = snap.get("markets") or []
    print("=== In-scope markets (source: scope or odds_current fallback) ===")
    print(f"total_markets={len(mkts)} (ahead={ahead_min}m, lookback={lookback_min}m)")

    live_count = 0
    for m in mkts:
        mid = str(m.get("marketId"))
        sids = _active_sids_from_fmb(mid, band_min=band_min, band_max=band_max, recency_seconds=recency_seconds)
        if sids:
            live_count += 1
            print(f"{mid}  active_sids={len(sids)}")
            print("  sids:", sids[:24] + (["…"] if len(sids) > 24 else []))
        else:
            print(f"{mid}  active_sids=0")

    print(f"\nmarkets_with_active={live_count} / {len(mkts)}")


if __name__ == "__main__":
    # Default CLI run mirrors your common scope window and recency:
    #   python -m engines.odds.providers.db_fallback_provider
    _print_in_scope_active(
        ahead_min=30,
        lookback_min=5,
        inbound_fresh_sec=120,
        max_markets=40,
        recency_seconds=300,
        band_min=1.5,
        band_max=12.0,
    )
