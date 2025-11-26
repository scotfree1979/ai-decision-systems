#!/usr/bin/env python3
"""
Daily AutoScalp Review Script (full version with KPIs & digest)

Usage:
  python3 review_day.py --auto data/autoscalp_gui.db --bets data/bets.db --date YYYY-MM-DD --out ./review_out

If --date is omitted, defaults to yesterday (UTC).
Outputs CSVs into ./review_out/YYYY-MM-DD and prints a console summary with pass/fail ticks.
"""

import argparse, os, sqlite3, csv, statistics
from datetime import datetime, timedelta
from collections import defaultdict

# --- Configurable KPI thresholds ---
MAX_TOTAL_CAP = 12
MAX_BACK_CAP = 4
OC_MIN_PCT = 95.0
PARENT_MATCH_MIN = 85.0
HEDGE_SUCCESS_MIN = 90.0

def connect_db(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA busy_timeout=8000;")
        con.execute("PRAGMA synchronous=NORMAL;")
        con.execute("PRAGMA read_uncommitted=1;")
    except Exception:
        pass
    return con

def q(con, sql, params=()):
    return con.execute(sql, params).fetchall()

def rows_to_csv(rows, path: str):
    if not rows: return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(rows[0].keys())
        for r in rows:
            w.writerow([r[k] for k in r.keys()])

def write_dicts_csv(dict_rows, path: str):
    if not dict_rows: return
    keys = list(dict_rows[0].keys())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(dict_rows)

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", required=True, help="autoscalp_gui.db path")
    ap.add_argument("--bets", required=False, default=None, help="bets.db path (optional)")
    ap.add_argument("--date", required=False, default=None, help="UTC date YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--out", required=False, default="./review_out", help="output folder")
    return ap.parse_args()

def default_date():
    return (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")

# --- Chapter Functions (abbreviated, some placeholders for brevity) ----------

def side_distribution(con, day):
    sql = """
    SELECT UPPER(side) AS side, COUNT(*) AS parents
    FROM orders
    WHERE role='PARENT' AND mode='LIVE' AND date(opened_at)=date(?)
    GROUP BY UPPER(side);
    """
    return q(con, sql, (day,))

def strategy_breakdown(con, day):
    sql = """
    SELECT COALESCE(source,'UNKNOWN') AS strategy,
           SUM(CASE WHEN role='PARENT' AND date(opened_at)=date(?) THEN 1 ELSE 0 END) AS parents,
           SUM(CASE WHEN role='PARENT' AND date(opened_at)=date(?) AND UPPER(entry_status)='MATCHED' THEN 1 ELSE 0 END) AS matched,
           SUM(CASE WHEN role='PARENT' AND date(opened_at)=date(?) AND UPPER(entry_status)='FAILED' THEN 1 ELSE 0 END) AS failed,
           SUM(CASE WHEN role='PARENT' AND date(opened_at)=date(?) AND UPPER(entry_status)='CANCELLED' THEN 1 ELSE 0 END) AS cancelled,
           SUM(CASE WHEN role='PARENT' AND date(opened_at)=date(?) THEN COALESCE(entry_stake,0.0) ELSE 0 END) AS total_stake
    FROM orders
    WHERE mode='LIVE'
    GROUP BY strategy
    ORDER BY strategy;
    """
    return q(con, sql, (day,)*6)

def failures(con, day):
    sql = """
    SELECT TRIM(COALESCE(error,'')) AS error_text, COUNT(*) AS n
    FROM orders
    WHERE role='PARENT' AND mode='LIVE' AND date(opened_at)=date(?) AND UPPER(entry_status)='FAILED'
    GROUP BY TRIM(COALESCE(error,''))
    ORDER BY n DESC;
    """
    return q(con, sql, (day,))

def runner_attempts(con, day):
    sql = """
    SELECT marketId, selectionId, COUNT(*) AS parents
    FROM orders
    WHERE role='PARENT' AND mode='LIVE' AND date(opened_at)=date(?)
    GROUP BY marketId, selectionId;
    """
    return q(con, sql, (day,))

def fetch_orders_for_sweep(con, day):
    sql = """
    SELECT marketId, selectionId, UPPER(side) AS side,
           datetime(opened_at) AS opened_at,
           CASE
             WHEN COALESCE(exit_status,'')='matched' THEN datetime(closed_at)
             WHEN COALESCE(entry_status,'') IN ('cancelled','failed') THEN datetime(closed_at)
             ELSE NULL
           END AS closed_at
    FROM orders
    WHERE mode='LIVE' AND role='PARENT' AND date(opened_at)=date(?);
    """
    return q(con, sql, (day,))

def sweep_peaks(rows, day):
    day_end   = datetime.fromisoformat(day + "T23:59:59")
    peaks = []
    by_runner = defaultdict(list)
    for r in rows:
        mid, sid = str(r["marketId"]), str(r["selectionId"])
        key = (mid, sid)
        try:
            t0 = datetime.fromisoformat(str(r["opened_at"]).replace(" ", "T"))
        except Exception:
            continue
        t1 = None
        if r["closed_at"]:
            try:
                t1 = datetime.fromisoformat(str(r["closed_at"]).replace(" ", "T"))
            except Exception:
                t1 = None
        if t1 is None or t1 < t0:
            t1 = day_end
        side = (r["side"] or "BACK").upper()
        by_runner[key].append((t0, +1, side))
        by_runner[key].append((t1, -1, side))
    for key, evs in by_runner.items():
        evs.sort(key=lambda x: (x[0], 0 if x[1] == +1 else 1))
        total = back = lay = 0
        peak_total = peak_back = peak_lay = 0
        for t, delta, side in evs:
            if side == "BACK":
                back += delta
            else:
                lay += delta
            total += delta
            peak_total = max(peak_total, total)
            peak_back = max(peak_back, back)
            peak_lay = max(peak_lay, lay)
        peaks.append({
            "marketId": key[0], "selectionId": key[1],
            "peak_total": peak_total, "peak_back": peak_back, "peak_lay": peak_lay
        })
    return peaks

def oc_snapshot(con, day, limit=5000):
    try:
        _ = q(con, "SELECT 1 FROM inbound_oc_cache LIMIT 1")
    except Exception:
        return []
    sql = """
    SELECT o.marketId, o.selectionId, o.opened_at,
           i.anchor_odd,
           i.oc1,i.oc2,i.oc3,i.oc4,i.oc5,i.oc6,i.oc7,i.oc8,i.oc9,i.oc10,
           i.oc11,i.oc12,i.oc13,i.oc14,i.oc15,i.oc16,i.oc17,i.oc18,i.oc19,i.oc20
    FROM orders o
    LEFT JOIN inbound_oc_cache i
      ON i.marketId=o.marketId AND i.selectionId=o.selectionId
     AND datetime(i.last_sync_ts) <= datetime(o.opened_at)
    WHERE o.role='PARENT' AND o.mode='LIVE' AND date(o.opened_at)=date(?)
    ORDER BY o.opened_at DESC, datetime(i.last_sync_ts) DESC
    LIMIT ?;
    """
    rows = q(con, sql, (day, limit))
    seen=set(); out=[]
    for r in rows:
        key=(r["marketId"], r["selectionId"], r["opened_at"])
        if key in seen: continue
        seen.add(key)
        oc_vals=[r[f"oc{i}"] for i in range(1,21) if f"oc{i}" in r.keys()]
        oc_count=sum(1 for v in oc_vals if v is not None)
        out.append({
            "marketId":str(r["marketId"]),
            "selectionId":str(r["selectionId"]),
            "opened_at":r["opened_at"],
            "oc_count":oc_count,
            "anchor_odd":r["anchor_odd"]
        })
    return out

# --- Main -------------------------------------------------------------------

def main():
    args = parse_args()
    day = args.date or default_date()
    outdir = os.path.join(os.path.abspath(args.out), day)
    os.makedirs(outdir, exist_ok=True)

    con = connect_db(args.auto)

    print(f"[REVIEW] date={day} auto_db={args.auto}")

    # Side distribution
    sides = side_distribution(con, day)
    rows_to_csv(sides, os.path.join(outdir, "side_distribution.csv"))
    total_parents = sum(r["parents"] for r in sides)
    back = sum(r["parents"] for r in sides if r["side"]=="BACK")
    lay = sum(r["parents"] for r in sides if r["side"]=="LAY")
    pct_back = round(100*back/total_parents,1) if total_parents else 0

    # Strategy breakdown
    strat = strategy_breakdown(con, day)
    rows_to_csv(strat, os.path.join(outdir, "strategy_breakdown.csv"))

    # Failures
    fails = failures(con, day)
    rows_to_csv(fails, os.path.join(outdir, "failures.csv"))
    top_fails = [f"{r['error_text']} ({r['n']})" for r in fails[:3]]

    # Runner attempts & Gini
    attempts = runner_attempts(con, day)
    rows_to_csv(attempts, os.path.join(outdir, "runner_attempts.csv"))
    values = [r["parents"] for r in attempts]
    gini = 0.0
    if values:
        sorted_vals = sorted(values)
        n = len(values)
        cum = 0
        for i, v in enumerate(sorted_vals, 1):
            cum += i * v
        gini = (2*cum)/(n*sum(sorted_vals)) - (n+1)/n

    # Cap compliance
    orders_for_sweep = fetch_orders_for_sweep(con, day)
    peaks = sweep_peaks(orders_for_sweep, day)
    write_dicts_csv(peaks, os.path.join(outdir, "runner_peaks.csv"))
    viol = [p for p in peaks if p["peak_total"]>MAX_TOTAL_CAP or p["peak_back"]>MAX_BACK_CAP]
    write_dicts_csv(viol, os.path.join(outdir, "cap_violations.csv"))

    # OC sufficiency
    oc_rows = oc_snapshot(con, day)
    write_dicts_csv(oc_rows, os.path.join(outdir, "oc_snapshot.csv"))
    oc_low = sum(1 for r in oc_rows if r["oc_count"]<2)
    oc_pct_low = round(100*oc_low/len(oc_rows),1) if oc_rows else 0

    # Console summary
    print("---- SUMMARY ----")
    print(f"Date: {day}")
    print(f"Parents total={total_parents}, BACK={back}, LAY={lay}, %BACK={pct_back}")
    print(f"Gini of attempts per runner={gini:.2f}")
    print(f"Cap violations={len(viol)} {'✅' if len(viol)==0 else '❌'} (thresholds: total>{MAX_TOTAL_CAP} or back>{MAX_BACK_CAP})")
    print(f"OC sufficiency={(100-oc_pct_low):.1f}% {'✅' if (100-oc_pct_low)>=OC_MIN_PCT else '❌'} (OC>=2 at entry)")
    if top_fails:
        print("Top failures:", "; ".join(top_fails))
    else:
        print("No failures recorded.")
    print(f"CSV outputs in {outdir}")

    con.close()

if __name__ == "__main__":
    main()
