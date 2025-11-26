# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/decision_logger.py
# 🔎 SEARCH: ^\Z
from __future__ import annotations
import json, sqlite3
from typing import Any, Dict, Iterable, Optional

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/decision_logger.py:_adb
# 🔎 SEARCH: def _adb(
# 📆 PATCHED: 2025-11-21 — replace raw sqlite connect with DAL writer

from engines.config_paths import auto_conn as _auto_conn

def _adb() -> sqlite3.Connection:
    """
    DAL-safe writer for decisions table.
    Always writes to CLOUD autoscalp_gui.db.
    """
    con = _auto_conn(rw=True)
    con.row_factory = sqlite3.Row
    return con
# === PATCH END ===


def _j(obj: Any) -> str:
    try:
        return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    except Exception:
        return "{}"

def log_decision(
    *,
    run_id: int,
    marketId: str,
    selectionId: str,
    decided_at_iso: str,
    scalp_direction: Optional[str],           # "lay_to_back" | "back_to_lay" | None
    placement_outcome: str,                   # "placed" | "not_placed"
    why: str,
    cap_state: Optional[Dict[str, Any]] = None,
    direction_bases: Optional[Iterable[str]] = None,
    order_id: Optional[int] = None,
    signal_type: Optional[str] = None,
    blueprint_match: Optional[str] = None,
    confidence: Optional[float] = None,
    proposed_odds: Optional[float] = None,
    proposed_stake: Optional[float] = None,
    notes: Optional[str] = None,
    extra_meta: Optional[Dict[str, Any]] = None,
) -> int:
    """Writes one row to decisions (your schema) and returns decisions.id."""
    meta = {
        "placement_outcome": placement_outcome,
        "why": why,
        "cap_state": cap_state or {},
        "direction_bases": list(direction_bases or []),
    }
    if isinstance(extra_meta, dict):
        meta.update(extra_meta)
    with _adb() as con:
        cur = con.execute(
            """
            INSERT INTO decisions
              (run_id, marketId, selectionId, decided_at,
               signal_type, blueprint_match, confidence,
               scalp_direction, proposed_odds, proposed_stake,
               notes, meta_json, order_id)
            VALUES
              (?, ?, ?, ?,
               ?, ?, ?,
               ?, ?, ?,
               ?, ?, ?)
            """,
            (
                run_id, marketId, selectionId, decided_at_iso,
                signal_type, blueprint_match, confidence,
                scalp_direction, proposed_odds, proposed_stake,
                notes or "", _j(meta), order_id,
            ),
        )
        return int(cur.lastrowid)
# === PATCH END ===
