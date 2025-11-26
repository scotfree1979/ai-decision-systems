#!/usr/bin/env python3
"""
scripts/build_story_training_orders.py
───────────────────────────────────────────────
Phase 6.5 – Orders → Narratives Bridge
Connects story_training_view with live orders
and playbooks_settled to show what the system
actually did during each narrative.
"""

import sqlite3, time
from engines.config_paths import mastery_v7_db, autoscalp_db

def main(day="2025-10-17"):
    print(f"[ORDERS] bridging narratives with live orders for {day}…")

    con = sqlite3.connect(mastery_v7_db()); con.row_factory = sqlite3.Row
    cur = con.cursor()

    # -------------------------------------------------------------------
    # 1️⃣ Create destination table
    # -------------------------------------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS story_training_orders (
            day TEXT,
            marketId TEXT,
            selectionId TEXT,
            label TEXT,
            narrative TEXT,
            conf REAL,
            drift_speed REAL,
            momentum_class TEXT,
            race_pattern TEXT,
            success_flag INTEGER,
            open_parents INTEGER,
            matched_children INTEGER,
            unhedged_liability REAL,
            net_pl REAL,
            open_loss_flag INTEGER,
            order_summary TEXT,
            created_at TEXT DEFAULT (datetime('now','utc')),
            PRIMARY KEY(day, marketId, selectionId)
        )
    """)
    cur.execute("DELETE FROM story_training_orders WHERE day=?", (day,))

    # -------------------------------------------------------------------
    # 2️⃣ Attach AutoScalp GUI DB for orders & playbooks
    # -------------------------------------------------------------------
    cur.execute(f"ATTACH DATABASE '{autoscalp_db()}' AS auto")

    # -------------------------------------------------------------------
    # 3️⃣ Aggregate orders and playbooks
    # -------------------------------------------------------------------
    # Orders aggregation → open parents, matched children, dangling liability
    orders_sql = """
        SELECT marketId, selectionId,
               SUM(CASE WHEN role='PARENT' AND UPPER(COALESCE(exit_status,''))<>'MATCHED' THEN 1 ELSE 0 END) AS open_parents,
               SUM(CASE WHEN role='CHILD'  AND UPPER(COALESCE(entry_status,''))='MATCHED' THEN 1 ELSE 0 END) AS matched_children,
               SUM(CASE WHEN role='PARENT' AND side='LAY'
                         AND UPPER(COALESCE(exit_status,''))<>'MATCHED'
                        THEN entry_stake*(entry_odds-1.0)
                        ELSE 0 END) AS unhedged_liability
          FROM auto.orders
         WHERE mode='LIVE'
         GROUP BY marketId, selectionId
    """

    # Realised PnL from playbooks_settled
    playbooks_sql = """
        SELECT marketId, selectionId, SUM(net_pl) AS net_pl
          FROM auto.playbooks_settled
         WHERE date(day)=date(?)
         GROUP BY marketId, selectionId
    """

    # -------------------------------------------------------------------
    # 4️⃣ Build bridge into story_training_view
    # -------------------------------------------------------------------
    cur.execute("""
        INSERT INTO story_training_orders
        (day, marketId, selectionId, label, narrative, conf,
         drift_speed, momentum_class, race_pattern, success_flag,
         open_parents, matched_children, unhedged_liability, net_pl,
         open_loss_flag, order_summary)
        SELECT
            s.day, s.marketId, s.selectionId, s.label,
            s.narrative, s.conf, s.drift_speed, s.momentum_class,
            s.race_pattern, s.success_flag,
            COALESCE(o.open_parents,0),
            COALESCE(o.matched_children,0),
            COALESCE(o.unhedged_liability,0.0),
            COALESCE(p.net_pl,0.0),
            CASE
                WHEN COALESCE(o.unhedged_liability,0.0)>0
                     AND s.success_flag=1 THEN 1 ELSE 0 END AS open_loss_flag,
            printf('%d parents, %d hedges, £%.2f liability, £%.2f pnl',
                   COALESCE(o.open_parents,0),
                   COALESCE(o.matched_children,0),
                   COALESCE(o.unhedged_liability,0.0),
                   COALESCE(p.net_pl,0.0)
            ) AS order_summary
        FROM story_training_view AS s
        LEFT JOIN (%s) AS o USING (marketId, selectionId)
        LEFT JOIN (%s) AS p USING (marketId, selectionId)
        WHERE s.day=?
    """ % (orders_sql, playbooks_sql), (day,))
    con.commit()

    # -------------------------------------------------------------------
    # 5️⃣ Show summary of linkage quality
    # -------------------------------------------------------------------
    rows = con.execute("""
        SELECT label,
               COUNT(*) AS n,
               SUM(open_loss_flag) AS open_loss,
               ROUND(AVG(unhedged_liability),2) AS avg_liab,
               ROUND(AVG(net_pl),2) AS avg_pnl
          FROM story_training_orders
         WHERE day=?
         GROUP BY label ORDER BY n DESC
    """, (day,)).fetchall()

    print(f"[ORDERS] ✅ integrated {sum(r['n'] for r in rows)} narrative-orders rows.")
    for r in rows:
        print(f"  {r['label']:<12} → {r['n']:>5} rows | "
              f"open_loss={r['open_loss']:>3} | liab={r['avg_liab']:>6.2f} | pnl={r['avg_pnl']:>6.2f}")
    con.close()
    print(f"[ORDERS] done in {time.time():.2f}s.")

if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else "2025-10-17")
