#!/usr/bin/env python3
# engines/v7_shims/v7_order_shim.py
"""
AutoScalp v7 — Unified Order Shim
=================================

Creates a normalised live view (`v_orders_v7`) of all orders with
new v7 fields: subtype (AA, SS…), trade_index, cap_scope, and day.

Used by:
    - micro_scalper_router
    - cap_ok_v7 (CAP bridge)
    - analytics + dashboards

This shim never modifies existing data. It only reads and derives.
"""

import sqlite3, datetime
from engines.config_paths import autoscalp_db

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(autoscalp_db())
    con.row_factory = sqlite3.Row
    return con

def _has_view(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute("SELECT 1 FROM sqlite_master WHERE type='view' AND name=?", (name,)).fetchone()
    return bool(row)

# ─────────────────────────────────────────────────────────────────────────────
# View Creator
# ─────────────────────────────────────────────────────────────────────────────
# === PATCH START ===
# 📍 TARGET: engines/v7_shims/v7_order_shim.py:create_v7_order_view
# 📆 PATCHED: 2025-10-29Z — strict schema version (matches actual orders columns)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def create_v7_order_view(con: sqlite3.Connection) -> None:
    """
    Creates the v_orders_v7 view using the *actual orders schema*.
    No status field. Compatible with your production table.
    """
    con.execute("DROP VIEW IF EXISTS v_orders_v7")
    con.execute("""
        CREATE VIEW v_orders_v7 AS
        WITH base AS (
            SELECT
                id,
                marketId,
                selectionId,
                COALESCE(source,'') AS letter_raw,
                UPPER(SUBSTR(COALESCE(source,''),1,1)) AS letter,
                CASE
                    WHEN LENGTH(COALESCE(source,''))>=2
                         THEN UPPER(SUBSTR(source,1,2))
                    ELSE UPPER(SUBSTR(COALESCE(source,''),1,1))
                END AS subtype,
                CAST(
                    CASE
                        WHEN source GLOB '*[0-9]*'
                             THEN SUBSTR(source, -1)
                        ELSE '0'
                    END AS INTEGER
                ) AS trade_index,
                COALESCE(mode,'LIVE') AS mode,
                COALESCE(entry_status,'') AS entry_status,
                COALESCE(exit_status,'')  AS exit_status,
                COALESCE(entry_stake,0.0) AS entry_stake,
                COALESCE(entry_odds,0.0)  AS entry_odds,
                COALESCE(net_pl,0.0) AS net_pl,
                CASE
                    WHEN UPPER(COALESCE(side,''))='LAY'
                        THEN ABS((entry_odds-1.0)*entry_stake)
                    ELSE ABS(entry_stake)
                END AS open_liability,
                COALESCE(run_id,'LIVE') AS run_id,
                date(opened_at) AS day,
                (marketId || '|' || selectionId || '|' || UPPER(SUBSTR(COALESCE(source,''),1,1))) AS cap_scope
            FROM orders
        )
        SELECT * FROM base
    """)
    con.commit()
    print("[v7-shim] created view v_orders_v7 ✅ (strict schema match)")
# === PATCH END ===


# ─────────────────────────────────────────────────────────────────────────────
# Backfill Helper
# ─────────────────────────────────────────────────────────────────────────────
def create_trade_index_backfill(con: sqlite3.Connection):
    """
    Optionally creates a materialized table mapping (marketId, selectionId, letter → trade_index).
    Useful for analytics if needed later.
    """
    con.execute("""
        CREATE TABLE IF NOT EXISTS v7_trade_index (
            marketId TEXT,
            selectionId TEXT,
            letter TEXT,
            subtype TEXT,
            trade_index INTEGER,
            updated_at TEXT DEFAULT (datetime('now','utc')),
            PRIMARY KEY (marketId, selectionId, letter, trade_index)
        )
    """)
    con.commit()

# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────
def ensure_v7_shim():
    """
    Main entrypoint: safely create v_orders_v7 if missing.
    """
    con = _connect()
    try:
        if not _has_view(con, "v_orders_v7"):
            create_v7_order_view(con)
            create_trade_index_backfill(con)
        else:
            print("[v7-shim] v_orders_v7 already exists ✅")
    finally:
        con.close()

if __name__ == "__main__":
    ensure_v7_shim()
