#!/usr/bin/env python3
"""
Rebuild Mastery Dashboard Canonical Views
========================================

LOCKED BLUEPRINT VERSION:
- Phase 7.9.13 Cockpit
- Overview / Strategy / Flow / Trends

This script:
• Creates or replaces READ-ONLY canonical views
• Does NOT mutate execution state
• Uses parent_closed as source of truth
• Is safe to run repeatedly

DBs:
- autoscalp_gui.db  (orders, book_state)
- bets.db           (bets, markets)
"""

import sqlite3
from datetime import datetime, timezone

from engines.config_paths import open_auto_db, connect_db

UTC_NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")

# -----------------------------------------------------------------------------
# CONNECTIONS
# -----------------------------------------------------------------------------

auto = open_auto_db(rw=True)
auto.row_factory = sqlite3.Row
bets = connect_db(ro=True)
bets.row_factory = sqlite3.Row

cur = auto.cursor()

print("\n=== Rebuilding Mastery Dashboard Views ===\n")

# -----------------------------------------------------------------------------
# TAB 1 — OVERVIEW
# -----------------------------------------------------------------------------

print("[1] Overview views")

cur.executescript("""
DROP VIEW IF EXISTS execution_summary_live;
CREATE VIEW execution_summary_live AS
SELECT
    COUNT(*) FILTER (WHERE role='PARENT' AND parent_closed=0)                       AS open_parents,
    SUM(CASE WHEN role='PARENT' AND parent_closed=0 AND entry_status='MATCHED'
             THEN CASE WHEN side='LAY'
                       THEN entry_stake*(entry_odds-1)
                       ELSE entry_stake END
        ELSE 0 END)                                                                 AS matched_exposure,
    SUM(CASE WHEN role='PARENT' AND parent_closed=1
             THEN CASE WHEN side='LAY'
                       THEN entry_stake*(entry_odds-1)
                       ELSE entry_stake END
        ELSE 0 END)                                                                 AS released_exposure,
    MAX(ts)                                                                         AS last_activity
FROM orders
WHERE mode='LIVE';
""")

cur.executescript("""
DROP VIEW IF EXISTS engine_exposure_live;
CREATE VIEW engine_exposure_live AS
SELECT
    engine,
    COUNT(*) FILTER (WHERE role='PARENT')                                           AS parents_total,
    COUNT(*) FILTER (WHERE role='PARENT' AND parent_closed=0)                       AS open_parents,
    COUNT(*) FILTER (WHERE role='PARENT' AND parent_closed=1)                       AS closed_parents,
    SUM(CASE WHEN role='PARENT' AND parent_closed=0 AND entry_status='MATCHED'
             THEN CASE WHEN side='LAY'
                       THEN entry_stake*(entry_odds-1)
                       ELSE entry_stake END
        ELSE 0 END)                                                                 AS open_exposure,
    SUM(COALESCE(net_pl,0))                                                         AS net_pnl,
    MAX(ts)                                                                         AS last_trade
FROM orders
WHERE mode='LIVE'
GROUP BY engine;
""")

cur.executescript("""
DROP VIEW IF EXISTS parent_lifecycle_counts;
CREATE VIEW parent_lifecycle_counts AS
SELECT
    CASE
        WHEN role='PARENT' AND parent_closed=1                 THEN 'CLOSED'
        WHEN role='PARENT' AND entry_status='QUEUED'           THEN 'QUEUED'
        WHEN role='PARENT' AND entry_status='PLACED'           THEN 'PLACED'
        WHEN role='PARENT' AND entry_status='MATCHED'          THEN 'MATCHED'
        WHEN role='CHILD'  AND entry_status='QUEUED'           THEN 'CHILD_QUEUED'
        WHEN role='CHILD'  AND entry_status='PLACED'           THEN 'CHILD_PLACED'
        WHEN role='CHILD'  AND entry_status='MATCHED'          THEN 'CHILD_MATCHED'
        ELSE 'OTHER'
    END AS state,
    COUNT(*) AS count
FROM orders
WHERE mode='LIVE'
GROUP BY state;
""")

# -----------------------------------------------------------------------------
# TAB 2 — STRATEGY
# -----------------------------------------------------------------------------

print("[2] Strategy views")

cur.executescript("""
DROP VIEW IF EXISTS engine_execution_today;
CREATE VIEW engine_execution_today AS
SELECT
    engine,
    COUNT(*) FILTER (WHERE role='PARENT')                       AS parents,
    COUNT(*) FILTER (WHERE role='PARENT' AND parent_closed=0)   AS open_parents,
    COUNT(*) FILTER (WHERE role='PARENT' AND parent_closed=1)   AS closed_parents,
    SUM(COALESCE(net_pl,0))                                     AS net_pnl,
    MAX(ts)                                                     AS last_trade
FROM orders
WHERE mode='LIVE'
  AND date(ts)=date('now','utc')
GROUP BY engine;
""")

cur.executescript("""
DROP VIEW IF EXISTS letter_intelligence_today;
CREATE VIEW letter_intelligence_today AS
SELECT
    UPPER(SUBSTR(source,1,1))                                   AS letter,
    COUNT(*) FILTER (WHERE role='PARENT')                       AS parents,
    COUNT(*) FILTER (WHERE role='CHILD')                        AS children,
    SUM(COALESCE(net_pl,0))                                     AS total_pnl
FROM orders
WHERE mode='LIVE'
  AND date(ts)=date('now','utc')
GROUP BY letter;
""")

cur.executescript("""
DROP VIEW IF EXISTS engine_letter_matrix;
CREATE VIEW engine_letter_matrix AS
SELECT
    engine,
    UPPER(SUBSTR(source,1,1))                                   AS letter,
    COUNT(*) FILTER (WHERE role='PARENT')                       AS parents,
    SUM(COALESCE(net_pl,0))                                     AS net_pnl
FROM orders
WHERE mode='LIVE'
GROUP BY engine, letter;
""")

# -----------------------------------------------------------------------------
# TAB 3 — FLOW (LIVE MECHANICS)
# -----------------------------------------------------------------------------

print("[3] Flow views")

cur.executescript("""
DROP VIEW IF EXISTS parent_child_linkage_live;
CREATE VIEW parent_child_linkage_live AS
SELECT
    p.customerOrderRef             AS parent_ref,
    p.engine,
    UPPER(SUBSTR(p.source,1,1))    AS letter,
    p.entry_status                 AS parent_status,
    c.entry_status                 AS child_status,
    p.parent_closed,
    CASE WHEN p.side='LAY'
         THEN p.entry_stake*(p.entry_odds-1)
         ELSE p.entry_stake END    AS exposure,
    p.marketId
FROM orders p
LEFT JOIN orders c ON c.hedge_of = p.id AND c.role='CHILD'
WHERE p.role='PARENT'
  AND p.mode='LIVE';
""")

# -----------------------------------------------------------------------------
# TAB 4 — TRENDS
# -----------------------------------------------------------------------------

print("[4] Trend views")

cur.executescript("""
DROP VIEW IF EXISTS pnl_trend;
CREATE VIEW pnl_trend AS
SELECT
    date(ts)               AS day,
    SUM(COALESCE(net_pl,0)) AS net_pnl,
    COUNT(*) FILTER (WHERE role='PARENT') AS trades
FROM orders
GROUP BY day;
""")

cur.executescript("""
DROP VIEW IF EXISTS exposure_trend;
CREATE VIEW exposure_trend AS
SELECT
    date(ts) AS day,
    MAX(CASE WHEN role='PARENT' AND parent_closed=0
             THEN CASE WHEN side='LAY'
                       THEN entry_stake*(entry_odds-1)
                       ELSE entry_stake END
        ELSE 0 END) AS peak_exposure
FROM orders
GROUP BY day;
""")

# -----------------------------------------------------------------------------
# FINALISE
# -----------------------------------------------------------------------------

auto.commit()
auto.close()
bets.close()

print("\n✅ Mastery dashboard views rebuilt successfully")
print(f"   Timestamp: {UTC_NOW}\n")
