#!/usr/bin/env python3
from __future__ import annotations
import sqlite3, json, math, csv, os
from pathlib import Path
from datetime import datetime, timezone

DB = Path("data/autoscalp_gui.db")

OUTDIR = Path("reports") / "audit"
OUTDIR.mkdir(parents=True, exist_ok=True)
OUT_CSV = OUTDIR / "last2_audit_rows.csv"
OUT_SUM = OUTDIR / "last2_audit_summary.txt"

def _q(con, sql, params=()):
    cur = con.execute(sql, params)
    rows = [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()]
    return rows

def tick_step(odds: float) -> float:
    x = float(odds)
    if x < 2.0:  return 0.01
    if x < 3.0:  return 0.02
    if x < 4.0:  return 0.05
    if x < 6.0:  return 0.10
    if x < 10.0: return 0.20
    if x < 20.0: return 0.50
    if x < 30.0: return 1.00
    if x < 50.0: return 2.00
    return 5.00

def ticks_between(a: float, b: float) -> int:
    # Walk from a -> b using the BF ladder (handles band changes correctly)
    aa, bb = float(a), float(b)
    if aa <= 0.0 or bb <= 0.0 or aa == bb:
        return 0
    lo, hi = (aa, bb) if aa < bb else (bb, aa)
    x = lo; ticks = 0; guard = 0
    while x < hi - 1e-9 and guard < 10000:
        s = tick_step(x)
        x = round(x + s, 2 if s <= 0.10 else 3)
        ticks += 1
        guard += 1
    return ticks if bb >= aa else -ticks

def lay_liability(odds: float, stake: float) -> float:
    # exposure of a LAY bet if not hedged
    return max(0.0, (float(odds) - 1.0) * float(stake))

def load_last2_market_ids(con: sqlite3.Connection) -> list[str]:
    rows = _q(con, """
      SELECT marketId, MAX(datetime(COALESCE(opened_at, ts))) AS last_open
        FROM orders
       WHERE date(COALESCE(opened_at, ts)) = date('now','utc','-1 day')

         AND marketId IS NOT NULL
       GROUP BY marketId
       ORDER BY datetime(last_open) DESC
       LIMIT 2
    """)
    return [r["marketId"] for r in rows]

def parent_child_rows(con: sqlite3.Connection, mids: list[str]) -> list[dict]:
    # parents are hedge_of IS NULL; children have hedge_of=parent.id
    rows = _q(con, f"""
      SELECT
        p.id                           AS parent_id,
        p.marketId                     AS marketId,
        p.selectionId                  AS selectionId,
        UPPER(COALESCE(p.side,''))     AS parent_side,
        p.entry_odds                   AS parent_entry_odds,
        p.entry_stake                  AS parent_entry_stake,
        UPPER(COALESCE(p.entry_status,'')) AS parent_entry_status,
        COALESCE(p.entry_matched_stake, 0) AS parent_matched_stake,
        p.exit_odds                    AS parent_exit_odds,
        p.exit_stake                   AS parent_exit_stake,
        UPPER(COALESCE(p.exit_status,''))  AS parent_exit_status,
        p.opened_at                    AS parent_opened_at,
        p.closed_at                    AS parent_closed_at,
        p.realized_pnl                 AS parent_realized_pnl,
        p.source                       AS letter,
        c.id                           AS child_id,
        UPPER(COALESCE(c.side,''))     AS child_side,
        c.entry_odds                   AS child_entry_odds,
        c.entry_stake                  AS child_entry_stake,
        UPPER(COALESCE(c.entry_status,'')) AS child_entry_status,
        COALESCE(c.entry_matched_stake, 0) AS child_matched_stake,
        c.exit_odds                    AS child_exit_odds,
        c.exit_stake                   AS child_exit_stake,
        UPPER(COALESCE(c.exit_status,''))  AS child_exit_status,
        c.opened_at                    AS child_opened_at,
        c.closed_at                    AS child_closed_at,
        d.why                          AS decision_why,
        d.meta_json                    AS decision_meta
      FROM orders p
      LEFT JOIN orders c ON c.hedge_of = p.id
      LEFT JOIN decisions d ON d.order_id = p.id
      WHERE p.hedge_of IS NULL
        AND p.marketId IN ({','.join(['?']*len(mids))})
        AND date(COALESCE(p.opened_at, p.ts)) = date('now','utc','-1 day')

      ORDER BY datetime(COALESCE(p.opened_at, p.ts)) ASC
    """, mids)
    return rows

def main():
    if not DB.exists():
        print(f"[ERR] DB not found: {DB}")
        return
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    mids = load_last2_market_ids(con)
    if not mids:
        print("[WARN] No markets found today in orders.")
        return
    print("[INFO] Last-2 marketIds:", mids)

    rows = parent_child_rows(con, mids)

    # compute derived fields per row
    out_rows = []
    by_mid = {mid: {"parents":0,"parents_100":0,"children_100":0,
                    "open_liab":0.0, "placed":0, "miss_px":0, "router_fail":0, "cap_block":0}
              for mid in mids}

    for r in rows:
        mid = r["marketId"]; sid = str(r["selectionId"] or "")
        p_side = r["parent_side"]; c_side = r["child_side"]
        peo = float(r["parent_entry_odds"] or 0.0)
        pes = float(r["parent_entry_stake"] or 0.0)
        pem = float(r["parent_matched_stake"] or 0.0)
        ceo = float(r["child_entry_odds"]  or 0.0)
        cem = float(r["child_matched_stake"] or 0.0)
        dwhy = (r.get("decision_why") or "").lower()
        meta = {}
        try:
            meta = json.loads(r["decision_meta"] or "{}")
        except Exception:
            meta = {}

        # classification
        parent_100 = pem >= max(0.01, pes * 0.999)
        child_100  = (cem > 0.0) and (r.get("child_entry_status","").upper() in ("PLACED","MATCHED","LIVE")) and \
                     (cem >= max(0.01, float(r.get("child_entry_stake") or 0.0) * 0.999))

        # realized ticks if child has odds
        realized_ticks = None
        if ceo > 0 and peo > 0:
            realized_ticks = ticks_between(peo, ceo) if p_side == "LAY" else ticks_between(peo, ceo)

        # open liability (LAY parent exposure not fully hedged)
        open_liab = 0.0
        if p_side == "LAY":
            exp_parent = lay_liability(peo, pem)
            # child BACK reduces exposure approximately by (child_odds-1)*child_matched
            hedge_red  = lay_liability(ceo, cem) if ceo > 0 and cem > 0 else 0.0
            open_liab = max(0.0, exp_parent - hedge_red)

        # reason tallies
        if "missing px" in dwhy:
            by_mid[mid]["miss_px"] += 1
        if "router_fail" in dwhy:
            by_mid[mid]["router_fail"] += 1
        if "cap_block" in dwhy or "cap_preclaim_failed" in dwhy:
            by_mid[mid]["cap_block"] += 1

        by_mid[mid]["parents"] += 1
        if parent_100: by_mid[mid]["parents_100"] += 1
        if child_100:  by_mid[mid]["children_100"] += 1
        by_mid[mid]["open_liab"] += open_liab
        if r.get("parent_entry_status","").upper() in ("PLACED","MATCHED","LIVE"):
            by_mid[mid]["placed"] += 1

        out_rows.append({
            "marketId": mid,
            "selectionId": sid,
            "letter": r.get("letter"),
            "parent_id": r["parent_id"],
            "parent_side": p_side,
            "parent_entry_odds": peo,
            "parent_entry_stake": pes,
            "parent_matched_stake": pem,
            "child_id": r.get("child_id"),
            "child_side": c_side,
            "child_entry_odds": ceo if ceo>0 else None,
            "child_matched_stake": cem if cem>0 else None,
            "parent_100": parent_100,
            "child_100": child_100,
            "realized_ticks": realized_ticks,
            "decision_why": r.get("decision_why"),
            "open_liability": round(open_liab, 2),
            "parent_realized_pnl": r.get("parent_realized_pnl"),
        })

    # write rows
    with OUT_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()) if out_rows else [])
        if out_rows: w.writeheader()
        w.writerows(out_rows)

    # summaries
    lines = []
    for mid in mids:
        s = by_mid[mid]
        lines.append(f"Market {mid}:")
        lines.append(f"  parents: {s['parents']} | 100% parents: {s['parents_100']}  | 100% children: {s['children_100']}")
        lines.append(f"  placed (parents with PLACED/MATCHED/LIVE): {s['placed']}")
        lines.append(f"  router_fail decisions: {s['router_fail']} | cap blocks: {s['cap_block']} | missing px: {s['miss_px']}")
        lines.append(f"  open liability total (approx, LAY unhedged): £{s['open_liab']:.2f}")
        lines.append("")

    OUT_SUM.write_text("\n".join(lines))
    print(f"[OK] rows → {OUT_CSV}")
    print(f"[OK] summary → {OUT_SUM}")
    print("\n".join(lines))

if __name__ == "__main__":
    main()
