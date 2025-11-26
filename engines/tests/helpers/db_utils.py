from __future__ import annotations
import os, sqlite3

# ─────────────────────────────────────────────────────────────────────────────
# Schema inspection helpers
# ─────────────────────────────────────────────────────────────────────────────

def cols(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return the set of column names for a table (empty set if table missing)."""
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()

def has_table(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())

def _ensure_column(conn: sqlite3.Connection, table: str, col: str, decl: str) -> None:
    """Add a column if missing (idempotent)."""
    if col not in cols(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

# ─────────────────────────────────────────────────────────────────────────────
# Orders schema management
# ─────────────────────────────────────────────────────────────────────────────

def ensure_orders_schema(conn: sqlite3.Connection) -> None:
    """
    Bring AUTO_DB.orders up to the minimal superset required by tests/GUI.
    - If the table doesn't exist, create it with the full set.
    - If it exists, add any missing columns (opened_at, closed_at, entry_status, net_pl).
    NOTE: We do NOT drop or alter existing columns (e.g., run_id, source) — we keep them.
    """
    if not has_table(conn, "orders"):
        conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          mode TEXT, side TEXT,
          entry_odds REAL, entry_stake REAL, entry_status TEXT,
          marketId TEXT, selectionId TEXT,
          opened_at TEXT, closed_at TEXT,
          net_pl REAL DEFAULT 0.0
        )
        """)
        conn.commit()
        return

    # Add missing columns on an existing table (no-op if already present)
    _ensure_column(conn, "orders", "opened_at", "TEXT")
    _ensure_column(conn, "orders", "closed_at", "TEXT")
    _ensure_column(conn, "orders", "entry_status", "TEXT")
    _ensure_column(conn, "orders", "net_pl", "REAL DEFAULT 0.0")
    conn.commit()

# ─────────────────────────────────────────────────────────────────────────────
# DB open helper (AUTO_DB)
# ─────────────────────────────────────────────────────────────────────────────

def open_auto_db(autoscalp_db_path: str, timeout: float = 8.0) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(autoscalp_db_path), exist_ok=True)
    conn = sqlite3.connect(autoscalp_db_path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    return conn

# ─────────────────────────────────────────────────────────────────────────────
# Schema-aware insert (handles run_id/source if present; opened_at if present)
# ─────────────────────────────────────────────────────────────────────────────

def insert_order(conn: sqlite3.Connection, **kw) -> int:
    """
    Insert an order row in a schema-aware way:
      - Always supplies core fields if the columns exist.
      - If 'run_id' exists, supplies it (default 'TEST').
      - If 'source' exists, supplies it (default 'TEST').
      - If 'opened_at' exists, sets it to UTC now; otherwise omits.
    Defaults:
      mode='TEST', side='LAY', entry_odds=6.0, entry_stake=2.0,
      entry_status='queued', marketId='M1', selectionId='S1'.
    Returns: newly inserted order id (int).
    """
    present = cols(conn, "orders")

    # Core columns we aim to write (only included if present in table)
    desired_core = ["mode", "side", "entry_odds", "entry_stake",
                    "entry_status", "marketId", "selectionId"]
    params = {
        "mode": kw.get("mode", "TEST"),
        "side": kw.get("side", "LAY"),
        "entry_odds": kw.get("entry_odds", 6.0),
        "entry_stake": kw.get("entry_stake", 2.0),
        "entry_status": kw.get("entry_status", "queued"),
        "marketId": kw.get("marketId", "M1"),
        "selectionId": kw.get("selectionId", "S1"),
    }

    cols_to_use = [c for c in desired_core if c in present]

    # Optional columns common in your live schema
    extra_cols = []
    if "run_id" in present:
        extra_cols.append("run_id")
        params["run_id"] = kw.get("run_id", "TEST")
    if "source" in present:
        extra_cols.append("source")
        params["source"] = kw.get("source", "TEST")

    # opened_at (timestamp) if column exists
    include_opened = "opened_at" in present

    # Build SQL
    all_cols = cols_to_use + extra_cols
    placeholders = ", ".join([f":{c}" for c in all_cols])

    if include_opened:
        sql = f"""
            INSERT INTO orders ({", ".join(all_cols)}, opened_at)
            VALUES ({placeholders}, datetime('now','utc'))
        """
    else:
        sql = f"""
            INSERT INTO orders ({", ".join(all_cols)})
            VALUES ({placeholders})
        """

    # Execute
    conn.execute(sql, params)
    oid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.commit()
    return oid
