"""
DB + system adapters for the Decision Engine (Phase 1).
No external deps beyond stdlib and project modules.
"""
from __future__ import annotations

import os, json, sqlite3
from typing import Optional, Tuple, Dict, Any, List

import engines.config_paths as cp


# Paths
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))

# Time helpers
try:
    from engines.utils.time_compat import now_utc
except Exception:  # extremely defensive
    from datetime import datetime, timezone
    def now_utc():
        return datetime.now(timezone.utc)

# Budget
try:
    from engines.daily_config import fetch_available_budget  # you said this is canonical
except Exception:
    def fetch_available_budget() -> float:
        return 800.0

# Price math helpers (ticks) — tolerate missing functions by falling back to 1‑cent steps
try:
    from engines.price_math import tick_diff as _tick_diff, add_ticks as _add_ticks
except Exception:
    def _tick_diff(a: float, b: float) -> int:
        return int(round((b - a) / 0.01))
    def _add_ticks(p: float, n: int) -> float:
        return round(p + n * 0.01, 2)


# ────────────────────────────────
# Basic SQL helpers
# ────────────────────────────────

def _con(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    return con


def _rg(row: sqlite3.Row, key: str):
    try:
        return row[key]
    except Exception:
        return None

# ────────────────────────────────
# Bets DB lookups (bets.db)
# ────────────────────────────────

def get_market_meta(marketId: str) -> Dict[str, Any]:
    """Return start time + names from bets.bets (best‑effort)."""
    sql = """
        SELECT marketStartTime, event_name, market_name, race_name
        FROM bets
        WHERE marketId = ?
        ORDER BY timestamp DESC
        LIMIT 1
    """
    with _con(cp.bets_db()) as con:
        row = con.execute(sql, [marketId]).fetchone()
    if not row:
        return {"marketStartTime": None, "event_name": None, "market_name": None, "race_name": None}
    return dict(row)


def get_anchor_from_bets(marketId: str, selectionId: int) -> Optional[float]:
    with _con(cp.bets_db()) as con:
        row = con.execute(
            "SELECT anchor_odd FROM bets WHERE marketId=? AND selectionId=? ORDER BY timestamp DESC LIMIT 1",
            [marketId, selectionId],
        ).fetchone()
    return float(row["anchor_odd"]) if row and row["anchor_odd"] is not None else None


# ────────────────────────────────
# Autoscalp (autoscalp_gui.db) lookups
# ────────────────────────────────

def get_or_create_run(mode: str, notes: str = "") -> int:
    """Insert a run if none open for this process; return run id."""
    ts = now_utc().isoformat()
    with _con(cp.autoscalp_db()) as con:
        con.execute(
            "INSERT INTO runs(started_at, mode, notes) VALUES(?,?,?)",
            [ts, mode, notes],
        )
        rid = con.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    return int(rid)


def inbound_candidates(limit_per_market: int = 6) -> List[Dict[str, Any]]:
    """Return minimal set of (marketId, selectionId, horse_name)."""
    sql = """
        SELECT marketId, selectionId, COALESCE(horse_name,'') AS horse_name
        FROM inbound_bets_min
        ORDER BY marketId ASC, selectionId ASC
    """
    with _con(cp.autoscalp_db()) as con:
        rows = con.execute(sql).fetchall()
    # keep simple in Phase 1: we don't group/limit per market yet (OC loop already filters)
    return [dict(r) for r in rows]


def oc_cache_row(marketId: str, selectionId: int) -> Optional[sqlite3.Row]:
    sql = "SELECT * FROM inbound_oc_cache WHERE marketId=? AND selectionId=? LIMIT 1"
    with _con(cp.autoscalp_db()) as con:
        row = con.execute(sql, [marketId, selectionId]).fetchone()
    return row


def current_oc_label(cache_row: sqlite3.Row) -> str:
    """Return the highest OCn present as label (OC0 if none)."""
    if not cache_row:
        return "OC0"
    for n in range(20, 0, -1):
        v = _rg(cache_row, f"oc{n}")
        if v is not None:
            return f"OC{n}"
    return "OC0"


def band_for_label(cache_row: sqlite3.Row, oc_label: str) -> Optional[list]:
    if not cache_row:
        return None
    if oc_label == "OC0":
        return None
    n = int(oc_label[2:])
    raw = _rg(cache_row, f"oc{n}_band_json")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def latest_price(cache_row: sqlite3.Row) -> Optional[float]:
    """Latest lay sampled (prefer highest OCn value; fall back to anchor)."""
    if not cache_row:
        return None
    for n in range(20, 0, -1):
        v = _rg(cache_row, f"oc{n}")
        if v is not None:
            return float(v)
    a = _rg(cache_row, "anchor_odd")
    return float(a) if a is not None else None


def anchor_from_cache(cache_row: sqlite3.Row) -> Optional[float]:
    if not cache_row:
        return None
    a = _rg(cache_row, "anchor_odd")
    return float(a) if a is not None else None


def ensure_story(marketId: str, selectionId: int, start_label: str) -> int:
    ts = now_utc().isoformat()
    sql_ins = (
        "INSERT OR IGNORE INTO stories(marketId, selectionId, story_start_oc, created_at)"
        " VALUES(?,?,?,?)"
    )
    sql_sel = "SELECT id FROM stories WHERE marketId=? AND selectionId=? LIMIT 1"
    with _con(cp.autoscalp_db()) as con:
        con.execute(sql_ins, [marketId, selectionId, start_label, ts])
        row = con.execute(sql_sel, [marketId, selectionId]).fetchone()
    return int(row["id"]) if row else 0


def upsert_chapter(
    story_id: int,
    oc_label: str,
    entry_odds: Optional[float],
    exit_odds: Optional[float],
    band_json: Optional[list],
    tick_pattern: Optional[str],
    direction_bias: Optional[str],
    position_ratio: Optional[float],
    volatility: Optional[float],
    minutes_to_post: Optional[float],
    opened_at: Optional[str],
    closed_at: Optional[str],
) -> None:
    js = json.dumps(band_json) if band_json is not None else None
    with _con(cp.autoscalp_db()) as con:
        con.execute(
            """
            INSERT INTO chapters(
                story_id, oc_label, entry_odds, exit_odds, oc_band_json,
                tick_pattern, direction_bias, position_ratio, volatility,
                minutes_to_post, opened_at, closed_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(story_id, oc_label) DO UPDATE SET
                exit_odds=excluded.exit_odds,
                oc_band_json=excluded.oc_band_json,
                tick_pattern=excluded.tick_pattern,
                direction_bias=excluded.direction_bias,
                position_ratio=excluded.position_ratio,
                volatility=excluded.volatility,
                minutes_to_post=excluded.minutes_to_post,
                closed_at=excluded.closed_at
            """,
            [
                story_id, oc_label, entry_odds, exit_odds, js,
                tick_pattern, direction_bias, position_ratio, volatility,
                minutes_to_post, opened_at, closed_at,
            ],
        )


def record_decision(
    run_id: int,
    marketId: str,
    selectionId: int,
    signal_type: str,
    blueprint_match: Optional[str],
    confidence: float,
    scalp_direction: str,
    proposed_odds: float,
    proposed_stake: float,
    notes: str = "",
) -> int:
    ts = now_utc().isoformat()
    with _con(cp.autoscalp_db()) as con:
        con.execute(
            """
            INSERT INTO decisions(
                run_id, marketId, selectionId, decided_at,
                signal_type, blueprint_match, confidence,
                scalp_direction, proposed_odds, proposed_stake, notes
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                run_id, marketId, selectionId, ts,
                signal_type, blueprint_match, confidence,
                scalp_direction, proposed_odds, proposed_stake, notes,
            ],
        )
        row = con.execute("SELECT last_insert_rowid() AS id").fetchone()
    return int(row["id"]) if row else 0


def record_order(
    run_id: int,
    decision_id: Optional[int],
    customerOrderRef: str,
    marketId: str,
    selectionId: int,
    mode: str,                    # 'SIM' or 'LIVE'
    side: str,                    # 'BACK'|'LAY'
    entry_odds: float,
    entry_stake: float,
    entry_status: str = "queued",
    unrealized_pnl: float = 0.0,
) -> int:
    ts = now_utc().isoformat()
    with _con(cp.autoscalp_db()) as con:
        con.execute(
            """
            INSERT INTO orders(
                run_id, decision_id, customerOrderRef,
                marketId, selectionId, mode, side,
                entry_odds, entry_stake, entry_status,
                unrealized_pnl, opened_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                run_id, decision_id, customerOrderRef,
                marketId, selectionId, mode, side,
                entry_odds, entry_stake, entry_status,
                unrealized_pnl, ts,
            ],
        )
        row = con.execute("SELECT last_insert_rowid() AS id").fetchone()
    return int(row["id"]) if row else 0


# Small helpers exported
add_ticks = _add_ticks
tick_diff = _tick_diff
