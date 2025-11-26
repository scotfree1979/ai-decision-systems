# engines/live/settlement_writer.py
from __future__ import annotations
import os, sqlite3, json
from datetime import datetime, timezone
from typing import Optional, Dict, Any

# --- DB resolution -----------------------------------------------------

def _candidate_paths() -> list[str]:
    """
    Try the common locations in priority order.
    """
    paths = []
    # 1) next to autoscalp_gui.db
    try:
        from engines.config_paths import autoscalp_db
        base = os.path.dirname(autoscalp_db())
        paths.append(os.path.join(base, "settlements.db"))
    except Exception:
        pass
    # 2) project data/
    paths.append(os.path.join("data", "settlements.db"))
    # 3) engines/live/data (your override)
    paths.append(os.path.join("engines", "live", "data", "settlements.db"))
    # 4) env override
    envp = os.environ.get("SETTLEMENTS_DB")
    if envp:
        paths.insert(0, envp)
    return paths

def _open_settlements() -> tuple[sqlite3.Connection, str]:
    last_err = None
    for p in _candidate_paths():
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            con = sqlite3.connect(p, timeout=15.0, isolation_level=None)  # autocommit
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            con.row_factory = sqlite3.Row
            _ensure_schema(con)
            return con, p
        except Exception as e:
            last_err = e
    raise RuntimeError(f"[SETTLEMENTS] could not open any candidate path; last err={last_err}")

# --- Schema (minimal, matches your file) -------------------------------------

def _ensure_schema(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS st_order_ledger(
            betId TEXT PRIMARY KEY,
            source TEXT, mode TEXT,
            marketId TEXT, selectionId TEXT, side TEXT,
            price REAL, size REAL, status TEXT,
            opened_at TEXT, placed_at TEXT, settled_at TEXT,
            customerOrderRef TEXT, customerStrategyRef TEXT,
            net_pl REAL, commission REAL, json_raw TEXT
        )
    """)
    # Optional helper indexes for daily rollups
    con.execute("CREATE INDEX IF NOT EXISTS idx_sol_day ON st_order_ledger(date(settled_at), mode)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_sol_src_day ON st_order_ledger(source, date(settled_at), mode)")

# --- Writer ------------------------------------------------------------

def _utc_iso(dt: Optional[datetime]=None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def record_settlement(
    *,
    bet_id: str,
    mode: str,
    source: Optional[str],
    market_id: str,
    selection_id: str,
    side: str,              # "BACK"/"LAY"
    price: float,
    size: float,
    status: str,            # e.g. "SETTLED"
    opened_at: Optional[str] = None,
    placed_at: Optional[str] = None,
    settled_at: Optional[str] = None,
    customer_order_ref: Optional[str] = None,
    customer_strategy_ref: Optional[str] = None,
    net_pl: Optional[float] = None,
    commission: Optional[float] = None,
    raw: Optional[Dict[str, Any]] = None
) -> None:
    """
    One row per cleared bet (PRIMARY KEY betId).
    Safe to call repeatedly (UPSERT ignored on same betId).
    """
    con, path = _open_settlements()
    try:
        con.execute("""
            INSERT OR REPLACE INTO st_order_ledger(
                betId, source, mode, marketId, selectionId, side,
                price, size, status,
                opened_at, placed_at, settled_at,
                customerOrderRef, customerStrategyRef,
                net_pl, commission, json_raw
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            str(bet_id),
            (source or "").upper(),
            (mode or "LIVE").upper(),
            str(market_id), str(selection_id), (side or "").upper(),
            float(price) if price is not None else None,
            float(size) if size is not None else None,
            status or "SETTLED",
            opened_at or None,
            placed_at or None,
            settled_at or _utc_iso(),
            customer_order_ref or None,
            customer_strategy_ref or None,
            float(net_pl) if net_pl is not None else 0.0,
            float(commission) if commission is not None else 0.0,
            json.dumps(raw, separators=(",", ":"), ensure_ascii=False) if raw else None
        ))
    finally:
        try: con.close()
        except Exception: pass
