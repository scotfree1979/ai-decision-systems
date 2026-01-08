#!/usr/bin/env python3
"""
Risk Price Helper
-----------------
Authoritative current-price helper for matched parents.

RULES (IMPORTANT):
- ALL reads go through auto_conn(rw=False) → rows are TUPLES
- NEVER use dict-style row access
- Positional unpacking ONLY
"""

from __future__ import annotations
import os
import time
import threading
from typing import Dict, List
from datetime import datetime, timezone

from engines.config_paths import auto_conn
from engines.utils.api_tools import fetch_live_odds


# ======================================================================
# CONFIG
# ======================================================================

WRITE_INTERVAL_SEC = 10      # refresh cadence
MIN_REFRESH_AGE_SEC = 5      # skip very recent updates

def _resolve_session_token() -> str | None:
    """
    Canonical session token resolver.
    Mirrors GUI / LIVE behaviour exactly.
    """
    # 1️⃣ upgrade_import_patch (GUI canonical)
    try:
        from engines.upgrade_import_patch import get_session_token  # type: ignore
        tok = (get_session_token() or "").strip()
        if tok:
            return tok
    except Exception:
        pass

    # 2️⃣ ENV (GUI exports this)
    tok = (
        os.environ.get("BETFAIR_SESSION")
        or os.environ.get("BETFAIR_SESSION_TOKEN")
        or ""
    ).strip()
    if tok:
        return tok

    # 3️⃣ Final fallback — secrets DB
    try:
        from engines.session_secrets import load_betfair_creds
        _, tok = load_betfair_creds()
        if tok:
            return tok.strip()
    except Exception:
        pass

    return None

def _bootstrap_session_token_if_needed():
    """
    Standalone safety net.
    If no session token is currently resolvable, prompt once.
    """
    try:
        from engines.upgrade_import_patch import get_session_token, set_session_token  # type: ignore
        tok = (get_session_token() or "").strip()
        if tok:
            return tok
    except Exception:
        pass

    tok = (
        os.environ.get("BETFAIR_SESSION")
        or os.environ.get("BETFAIR_SESSION_TOKEN")
        or ""
    ).strip()
    if tok:
        return tok

    # Standalone fallback (exactly like api_tools)
    tok = input("🔐 Enter your Betfair session token: ").strip()
    if tok:
        os.environ["BETFAIR_SESSION"] = tok
        os.environ["BETFAIR_SESSION_TOKEN"] = tok
        try:
            from engines.upgrade_import_patch import set_session_token  # type: ignore
            set_session_token(tok)
        except Exception:
            pass
    return tok


# ======================================================================
# TIME HELPERS
# ======================================================================

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _age_seconds(ts: str | None) -> float:
    if not ts:
        return 1e9
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - t).total_seconds()
    except Exception:
        return 1e9

def _resolve_session_token() -> str | None:
    """
    Canonical Betfair session token resolver.
    Mirrors GUI precedence.
    """
    # 1) upgrade_import_patch (authoritative in LIVE)
    try:
        from engines.upgrade_import_patch import get_session_token  # type: ignore
        tok = (get_session_token() or "").strip()
        if tok:
            return tok
    except Exception:
        pass

    # 2) ENV (GUI exports both)
    tok = (os.environ.get("BETFAIR_SESSION") or
           os.environ.get("BETFAIR_SESSION_TOKEN") or "").strip()
    if tok:
        return tok

    # 3) secrets DB fallback
    try:
        from engines.session_secrets import load_betfair_creds
        _, tok = load_betfair_creds()
        if tok:
            return tok.strip()
    except Exception:
        pass

    return None


def resolve_current_price(con, market_id, selection_id, side):
    """
    Unified price resolver.
    Early-day safe. Live-safe. DAL-authoritative.
    """

    # 1) DAL first
    row = con.execute("""
        SELECT ltp, back1, lay1, updated_ts
        FROM odds_current
        WHERE marketId = ?
          AND selectionId = ?
        ORDER BY datetime(updated_ts) DESC
        LIMIT 1
    """, (market_id, selection_id)).fetchone()

    if row:
        if side == "LAY":
            return row["lay1"] or row["back1"] or row["ltp"]
        return row["back1"] or row["lay1"] or row["ltp"]

    # 2) FALLBACK: live API (early day only)
    odds = fetch_live_odds(
        session_token=None,
        marketId=market_id,
        selectionId=selection_id,
    )

    if not odds:
        return None

    px = odds.get("lay") if side == "LAY" else odds.get("back")
    px = px or odds.get("back") or odds.get("lay")

    if px is None:
        return None

    # 3) Persist into DAL (seed)
    con.execute("""
        INSERT OR REPLACE INTO odds_current
        (day, marketId, selectionId, updated_ts, ltp, back1, lay1)
        VALUES (date('now','utc'), ?, ?, datetime('now','utc'), ?, ?, ?)
    """, (market_id, selection_id, px, odds.get("back"), odds.get("lay")))

    return px

# ======================================================================
# CORE READ: MATCHED PARENTS
# ======================================================================

def _fetch_matched_parents(con) -> List[dict]:
    """
    Return matched, still-exposed parents as plain dicts.
    """
    rows = con.execute("""
        SELECT
            id,
            marketId,
            selectionId,
            side,
            entry_odds,
            current_px,
            current_px_ts
        FROM orders
        WHERE
            role = 'PARENT'
            AND entry_status = 'MATCHED'
            AND (exit_status IS NULL OR exit_status <> 'MATCHED')
    """).fetchall()

    out = []
    for (
        pid,
        marketId,
        selectionId,
        side,
        entry_odds,
        current_px,
        current_px_ts,
    ) in rows:
        out.append({
            "id": int(pid),
            "marketId": marketId,
            "selectionId": selectionId,
            "side": side,
            "entry_odds": entry_odds,
            "current_px": current_px,
            "current_px_ts": current_px_ts,
        })
    return out


# ======================================================================
# PRICE DERIVATION
# ======================================================================

def _derive_px(side: str, odds: dict) -> float | None:
    if not odds:
        return None

    side = (side or "").upper()
    back = odds.get("back")
    lay  = odds.get("lay")

    if side == "LAY":
        return lay or back
    else:
        return back or lay


# ======================================================================
# WRITE PASS
# ======================================================================

def refresh_parent_prices_once() -> int:
    """
    Single refresh pass.
    Returns number of rows updated.
    """
    con = auto_conn(rw=True)
    updated = 0

    try:
        parents = _fetch_matched_parents(con)

        for p in parents:
            # refresh guard
            if _age_seconds(p["current_px_ts"]) < MIN_REFRESH_AGE_SEC:
                continue

            token = _resolve_session_token()
            if not token:
                continue  # no creds yet; skip safely

            token = _resolve_session_token()
            if not token:
                continue  # no creds yet

            odds = fetch_live_odds(
                session_token=token,
                marketId=p["marketId"],
                selectionId=p["selectionId"],
            )


            px = _derive_px(p["side"], odds)
            if px is None:
                continue

            con.execute("""
                UPDATE orders
                   SET current_px = ?,
                       current_px_ts = ?
                 WHERE id = ?
            """, (
                float(px),
                _utcnow_iso(),
                p["id"],
            ))

            updated += 1

        con.commit()
        return updated

    finally:
        try:
            con.close()
        except Exception:
            pass


# ======================================================================
# BACKGROUND LOOP
# ======================================================================

def start_price_writer_loop() -> None:
    """
    Start background refresh thread.
    """
    def _loop():
        while True:
            try:
                n = refresh_parent_prices_once()
                if n:
                    print(f"[risk_price_helper] updated {n} parent prices")
            except Exception as e:
                print(f"[risk_price_helper] WARN: {e}")
            time.sleep(WRITE_INTERVAL_SEC)

    threading.Thread(
        target=_loop,
        name="RiskPriceHelper",
        daemon=True
    ).start()


# ======================================================================
# READ API FOR BUS
# ======================================================================

def get_parent_price_snapshot(parent_ids: List[int]) -> Dict[int, dict]:
    """
    PURE READ.

    Returns:
    {
        parent_id: {
            marketId,
            selectionId,
            side,
            entry_px,
            current_px,
            delta_abs,
            updated_ts
        }
    }
    """
    if not parent_ids:
        return {}

    con = auto_conn(rw=False)
    out: Dict[int, dict] = {}

    try:
        qmarks = ",".join("?" * len(parent_ids))
        rows = con.execute(f"""
            SELECT
                id,
                marketId,
                selectionId,
                side,
                entry_odds,
                current_px,
                current_px_ts
            FROM orders
            WHERE id IN ({qmarks})
        """, tuple(parent_ids)).fetchall()

        for (
            pid,
            marketId,
            selectionId,
            side,
            entry_odds,
            current_px,
            current_px_ts,
        ) in rows:

            entry_px = float(entry_odds or 0.0)
            cur_px   = float(current_px or 0.0)

            out[int(pid)] = {
                "marketId": marketId,
                "selectionId": selectionId,
                "side": side,
                "entry_px": entry_px,
                "current_px": cur_px,
                "delta_abs": cur_px - entry_px,
                "updated_ts": current_px_ts,
            }

        return out

    finally:
        try:
            con.close()
        except Exception:
            pass


# ======================================================================
# STANDALONE TERMINAL TEST
# ======================================================================

if __name__ == "__main__":
    print("=== RISK PRICE HELPER TEST ===")

    _bootstrap_session_token_if_needed()

    n = refresh_parent_prices_once()
    print(f"updated={n}")


    con = auto_conn(rw=False)
    try:
        rows = con.execute("""
            SELECT id
            FROM orders
            WHERE role='PARENT'
              AND entry_status='MATCHED'
              AND (exit_status IS NULL OR exit_status <> 'MATCHED')
            LIMIT 5
        """).fetchall()
        parent_ids = [int(r[0]) for r in rows]
    finally:
        try:
            con.close()
        except Exception:
            pass

    snap = get_parent_price_snapshot(parent_ids)
    for pid, data in snap.items():
        print(pid, data)
