#!/usr/bin/env python3
import sqlite3, json

def infer_missing_winners(days: int = 90):
    """
    Infer winners for markets that do NOT have official reconciliation.
    Uses two signals:
      1) Profit-based inference (bf_cleared_orders)
      2) BSP (sp) inference — parsed from bf_market_book.resultJson

    Schema verified:
      bf_market_book(resultJson TEXT JSON ARRAY)
      bf_runner_info(marketId, selectionId, ..., status TEXT)
    """

    print(f"[form] inferring missing winners for last {days} days …")
    con = sqlite3.connect("data/settlements.db")
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # --- 1) Profit-based inference ---------------------------------------
    cur.execute(f"""
        WITH ranked AS (
            SELECT marketId, selectionId,
                   RANK() OVER (PARTITION BY marketId ORDER BY SUM(profit) DESC) AS rnk
              FROM bf_cleared_orders
             WHERE date(settledDate) >= date('now','-{days} day')
             GROUP BY marketId, selectionId
        )
        UPDATE bf_runner_info
           SET status='WINNER_INFERRED_PROFIT'
         WHERE (marketId, selectionId) IN (
                 SELECT marketId, selectionId FROM ranked WHERE rnk=1
               )
           AND (status IS NULL OR status='');
    """)
    profit_inferred = cur.rowcount
    print(f"[form] profit inference winners → {profit_inferred:,}")

    # --- 2) BSP inference via resultJson --------------------------------
    # We must read bf_market_book.resultJson because no LTP/BSP stored in table
    print("[form] scanning bf_market_book for BSP winners…")
    rows = cur.execute(
        "SELECT marketId, resultJson FROM bf_market_book "
        "WHERE resultJson IS NOT NULL"
    ).fetchall()

    bsp_pairs = []   # list of (marketId, selectionId)

    for r in rows:
        mid = r["marketId"]
        try:
            arr = json.loads(r["resultJson"])
            if not isinstance(arr, list):
                continue

            # find runner with lowest SP (if numeric)
            best_sid = None
            best_sp = None

            for runner in arr:
                sid = runner.get("selectionId")
                sp_raw = runner.get("sp")

                # skip nonsensical values
                try:
                    sp = float(sp_raw)
                except Exception:
                    continue

                # sp==NaN, skip
                if sp != sp:  # NaN check
                    continue

                if best_sp is None or sp < best_sp:
                    best_sp = sp
                    best_sid = str(sid)

            if best_sid is not None:
                bsp_pairs.append((mid, best_sid))

        except Exception as e:
            print(f"[form] JSON warn market {mid}: {e}")

    # apply BSP inference: lowest SP => likely winner
    if bsp_pairs:
        cur.executemany(
            """
            UPDATE bf_runner_info
               SET status='WINNER_INFERRED_BSP'
             WHERE marketId=? AND selectionId=?
               AND (status IS NULL OR status='')
            """,
            bsp_pairs
        )
        print(f"[form] bsp inference winners → {cur.rowcount:,}")
    else:
        print("[form] no BSP candidates found.")

    # --- 3) Unknowns ------------------------------------------------------
    cur.execute("""
        UPDATE bf_runner_info
           SET status='UNKNOWN_WINNER'
         WHERE status IS NULL OR status='';
    """)
    con.commit()
    con.close()
    print("[form] ✅ inference pass complete.")
    return profit_inferred
