#!/usr/bin/env python3
"""
Daily KPI Report — AutoScalp
Run:
    python3 scripts/daily_kpi_report.py
"""

from datetime import datetime, timezone
import sqlite3
from collections import defaultdict

from engines.config_paths import autoscalp_db, connect_db

DAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def rowdicts(cur):
    cols = [c[0] for c in cur.description]
    for r in cur.fetchall():
        yield dict(zip(cols, r))


def print_section(title):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def main():
    gui_db = autoscalp_db()
    bets_db = connect_db(ro=True).execute("PRAGMA database_list").fetchone()[2]

    con = sqlite3.connect(gui_db)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # ------------------------------------------------------------------
    print_section(f"GLOBAL SUMMARY — {DAY}")

    bank = cur.execute("""
        SELECT start_balance, current_balance
        FROM internal_bank
        WHERE day = ?
    """, (DAY,)).fetchone()

    if bank:
        start, current = bank
        print(f"Start Bank : £{start:.2f}")
        print(f"End Bank   : £{current:.2f}")
        print(f"Net P&L    : £{current - start:.2f}")
    else:
        print("No internal_bank row for today")

    # ------------------------------------------------------------------
    print_section("PARENTS & CHILDREN — BY ENGINE")

    rows = cur.execute("""
        SELECT
            engine,
            role,
            entry_status,
            exit_status,
            COUNT(*) AS n
        FROM orders
        WHERE date(opened_at) = date(?)
        GROUP BY engine, role, entry_status, exit_status
        ORDER BY engine, role
    """, (DAY,))

    engine_stats = defaultdict(list)
    for r in rowdicts(rows):
        engine_stats[r["engine"]].append(r)

    for engine, items in engine_stats.items():
        print(f"\nENGINE: {engine}")
        for r in items:
            print(
                f"  {r['role']:<6} "
                f"entry={r['entry_status'] or '-':<10} "
                f"exit={r['exit_status'] or '-':<10} "
                f"count={r['n']}"
            )

    # ------------------------------------------------------------------
    print_section("EXPOSURE — RESERVED vs RELEASED")

    rows = cur.execute("""
        SELECT
            engine,
            SUM(required_exposure) AS reserved,
            SUM(COALESCE(exposure_released_amount, 0)) AS released
        FROM orders
        WHERE role = 'PARENT'
          AND date(opened_at) = date(?)
        GROUP BY engine
    """, (DAY,))

    for r in rowdicts(rows):
        reserved = r["reserved"] or 0.0
        released = r["released"] or 0.0
        leak = reserved - released
        print(
            f"{r['engine']:<16} "
            f"reserved=£{reserved:7.2f} "
            f"released=£{released:7.2f} "
            f"open=£{leak:7.2f}"
        )

    # ------------------------------------------------------------------
    print_section("HEDGE COMPLETENESS")

    rows = cur.execute("""
        SELECT
            COUNT(*) AS parents,
            SUM(CASE WHEN EXISTS (
                SELECT 1 FROM orders c
                WHERE c.hedge_of = p.id
                  AND c.role = 'CHILD'
            ) THEN 1 ELSE 0 END) AS with_child
        FROM orders p
        WHERE p.role = 'PARENT'
          AND p.entry_status = 'MATCHED'
          AND date(p.opened_at) = date(?)
    """, (DAY,)).fetchone()

    if rows:
        parents, with_child = rows
        print(f"Matched Parents      : {parents}")
        print(f"With Hedge Child     : {with_child}")
        print(f"Unhedged Parents     : {parents - with_child}")

    # ------------------------------------------------------------------
    print_section("STOP-LOSS ACTIVITY")

    rows = cur.execute("""
        SELECT
            engine,
            COUNT(*) AS n
        FROM orders
        WHERE role = 'PARENT'
          AND stop_loss_triggered = 1
          AND date(opened_at) = date(?)
        GROUP BY engine
    """, (DAY,))

    found = False
    for r in rowdicts(rows):
        found = True
        print(f"{r['engine']:<16} stoploss={r['n']}")
    if not found:
        print("No stop-losses triggered")

    # =============================================================================
    # EXTENSIONS — ADVANCED KPI ANALYSIS
    # =============================================================================

    print("\n" + "="*80)
    print("MANUAL / UNATTRIBUTED P&L — BETFAIR TRUTH")
    print("="*80)

    try:
        bcur = bdb.cursor()

        rows = bcur.execute("""
            SELECT
                c.betId,
                c.marketId,
                c.selectionId,
                c.profit,
                c.commission,
                date(c.settledDate) AS day
            FROM bf_cleared_orders_cache c
            LEFT JOIN orders o1 ON o1.bf_bet_id = c.betId
            LEFT JOIN orders o2 ON o2.child_bf_bet_id = c.betId
            WHERE date(c.settledDate) = date(?)
              AND o1.id IS NULL
              AND o2.id IS NULL
        """, (DAY,)).fetchall()

        if not rows:
            print("No manual / unattributed Betfair trades today")
        else:
            total_profit = sum(r[3] or 0 for r in rows)
            total_comm   = sum(r[4] or 0 for r in rows)
            net = total_profit - total_comm

            print(f"Unattributed trades : {len(rows)}")
            print(f"Gross profit        : £{total_profit:,.2f}")
            print(f"Commission          : £{total_comm:,.2f}")
            print(f"Net P&L             : £{net:,.2f}")

            print("\nSample rows:")
            for r in rows[:10]:
                print(f"  betId={r[0]} market={r[1]} sel={r[2]} pnl=£{r[3]:.2f}")

    except Exception as e:
        print(f"[WARN] Manual P&L section failed: {e}")

    # -----------------------------------------------------------------------------

    print("\n" + "="*80)
    print("RISK CYCLE DEPTH — HOW FAR PARENTS PROGRESSED")
    print("="*80)

    try:
        cur = con.cursor()
        rows = cur.execute("""
            SELECT
                engine,
                COUNT(*)                                  AS parents,
                SUM(CASE WHEN entry_status='MATCHED' THEN 1 ELSE 0 END) AS matched,
                SUM(CASE WHEN exit_status='MATCHED'  THEN 1 ELSE 0 END) AS completed,
                AVG(bus_stop)                            AS avg_bus_stop,
                MAX(bus_stop)                            AS max_bus_stop
            FROM orders
            WHERE role='PARENT'
              AND date(opened_at)=date(?)
            GROUP BY engine
            ORDER BY engine
        """, (DAY,)).fetchall()

        for r in rows:
            print(
                f"{r[0]:<16} parents={r[1]:4} "
                f"matched={r[2]:4} completed={r[3]:4} "
                f"avg_bus={r[4] or 0:.1f} max_bus={r[5] or 0}"
            )

    except Exception as e:
        print(f"[WARN] Risk cycle depth failed: {e}")

    # -----------------------------------------------------------------------------

    print("\n" + "="*80)
    print("RISK ENGINE FIRE RATE (MATCHED WINDOW)")
    print("="*80)

    try:
        rows = cur.execute("""
            SELECT
                engine,
                COUNT(*) AS fires
            FROM orders
            WHERE role='PARENT'
              AND entry_status='MATCHED'
              AND date(opened_at)=date(?)
            GROUP BY engine
            ORDER BY engine
        """, (DAY,)).fetchall()

        for r in rows:
            print(f"{r[0]:<16} fires={r[1]}")

    except Exception as e:
        print(f"[WARN] Risk fire rate failed: {e}")

    # -----------------------------------------------------------------------------

    print("\n" + "="*80)
    print("OPPORTUNITY CONVERSION (PRE-OFF + IN-PLAY)")
    print("="*80)

    try:
        rows = cur.execute("""
            SELECT
                COUNT(*)                         AS opportunities,
                SUM(taken)                       AS taken,
                SUM(conversion)                  AS converted
            FROM indicators_opportunities
            WHERE day=?
        """, (DAY,)).fetchone()

        if rows and rows[0]:
            opp, taken, conv = rows
            print(f"Opportunities : {opp}")
            print(f"Taken         : {taken}")
            print(f"Converted     : {conv}")
            print(f"Conversion %  : {(conv / opp * 100):.2f}%")
        else:
            print("No opportunity data for today")

    except Exception as e:
        print(f"[WARN] Opportunity conversion failed: {e}")

    # -----------------------------------------------------------------------------

    print("\n" + "="*80)
    print("MANUAL HEDGE DETECTION (ENGINE GAP)")
    print("="*80)

    try:
        rows = cur.execute("""
            SELECT
                COUNT(*)
            FROM orders
            WHERE role='CHILD'
              AND engine IS NULL
              AND date(opened_at)=date(?)
        """, (DAY,)).fetchone()

        manual_hedges = rows[0] if rows else 0
        print(f"Manual hedge orders detected: {manual_hedges}")

    except Exception as e:
        print(f"[WARN] Manual hedge detection failed: {e}")


    # ------------------------------------------------------------------
    print_section("IN-PLAY vs PRE-OFF PARENTS")

    rows = cur.execute("""
        SELECT
            CASE
              WHEN bus_stop >= 6 THEN 'IN_PLAY'
              ELSE 'PRE_OFF'
            END AS phase,
            COUNT(*) AS n
        FROM orders
        WHERE role = 'PARENT'
          AND date(opened_at) = date(?)
        GROUP BY phase
    """, (DAY,))

    for r in rowdicts(rows):
        print(f"{r['phase']:<8} parents={r['n']}")

    # ------------------------------------------------------------------
    print_section("MANUAL / NON-ENGINE ORDERS")

    rows = cur.execute("""
        SELECT COUNT(*) AS n
        FROM orders
        WHERE engine IS NULL
          AND date(opened_at) = date(?)
    """, (DAY,)).fetchone()

    if rows:
        print(f"Manual / external orders: {rows['n']}")

    # ------------------------------------------------------------------
    print_section("SETTLED P&L — COMPLETED PAIRS (BY ENGINE)")

    rows = cur.execute("""
        SELECT
            o.engine                              AS engine,
            COUNT(*)                              AS trades,
            SUM(ps.net_pl)                        AS net_pnl,
            SUM(ps.liability_released)            AS liability_released
        FROM playbooks_settled ps
        JOIN orders o
          ON o.id = ps.child_id
        WHERE ps.day = ?
        GROUP BY o.engine
        ORDER BY o.engine
    """, (DAY,))

    found = False
    for r in rowdicts(rows):
        found = True
        print(
            f"{r['engine']:<16} "
            f"trades={r['trades']:<4} "
            f"net=£{(r['net_pnl'] or 0):7.2f} "
            f"released=£{(r['liability_released'] or 0):7.2f}"
        )

    if not found:
        print("No settled trade pairs yet today")


if __name__ == "__main__":
    main()
