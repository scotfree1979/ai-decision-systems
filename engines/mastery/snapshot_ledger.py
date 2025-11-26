# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📁 FILE: engines/mastery/snapshot_ledger.py
# 📆 PATCHED: 2025-10-15T23:40Z
# ───────────────────────────────────────────────────────────────────────
"""
snapshot_ledger.py — builds mastery performance snapshots for the Dashboard.

Hybrid version: combines mastery_posteriors (historical learning)
with today's live P&L from autoscalp_gui.orders.

Creates and maintains:
  • mastery_snapshot_latest.json
  • mastery_snapshot_7d.json
  • mastery_snapshot_30d.json
  • mastery_snapshot_90d.json
  • ledger_status.json
"""

from __future__ import annotations
import os, json, sqlite3, datetime
from statistics import mean
from engines.config_paths import autoscalp_db

DATA_DIR = os.path.join("data", "posteriors")
os.makedirs(DATA_DIR, exist_ok=True)

# ───────────────────────────────────────────────────────────────────────
def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _adb_ro() -> sqlite3.Connection:
    con = sqlite3.connect(autoscalp_db())
    con.row_factory = sqlite3.Row
    return con

def _fetch_posteriors(con: sqlite3.Connection, days: int):
    return con.execute(f"""
        SELECT *
          FROM mastery_posteriors
         WHERE date(updated_at) >= date('now','utc','-{days} day')
    """).fetchall()

def _fetch_live_orders(con: sqlite3.Connection):
    """Include live realised/unrealised P&L for today."""
    return con.execute("""
        SELECT role, side, entry_odds, entry_stake,
               entry_status, exit_status, net_pl, realized_pnl
          FROM orders
         WHERE date(opened_at)=date('now','utc')
    """).fetchall()

def _safe_avg(values):
    vals = [float(v) for v in values if v is not None]
    return mean(vals) if vals else 0.0

def _trend(today: float, prev: float) -> str:
    if abs(today - prev) < 0.005: return "→"
    return "↑" if today > prev else "↓"

# ───────────────────────────────────────────────────────────────────────
def _aggregate(rows, live_rows):
    """Aggregate from mastery_posteriors + today’s live orders."""
    out = {
        "Distance": {},
        "Favourite Rank": {},
        "Orders": {},
        "Day-of-Week": {}
    }
    if not rows and not live_rows:
        return out

    dist, fav, dow = {}, {}, {}
    stoploss, hedge = [], []
    avg_win, avg_loss = [], []

    for r in rows:
        dist[r["distance_band"]] = dist.get(r["distance_band"], []) + [float(r["net_pnl"] or 0)]
        fav[r["fav_rank_bin"]] = fav.get(r["fav_rank_bin"], []) + [float(r["net_pnl"] or 0)]
        dow[r["day_of_week"]] = dow.get(r["day_of_week"], []) + [float(r["net_pnl"] or 0)]
        if "stoploss_hit" in r.keys(): stoploss.append(float(r["stoploss_hit"] or 0))
        if "hedge_hit" in r.keys(): hedge.append(float(r["hedge_hit"] or 0))
        if float(r["net_pnl"] or 0) > 0:
            avg_win.append(float(r["net_pnl"]))
        elif float(r["net_pnl"] or 0) < 0:
            avg_loss.append(abs(float(r["net_pnl"])))

    # add live pnl to orders group
    live_pnls = [float(r["net_pl"] or r["realized_pnl"] or 0) for r in live_rows]
    live_today = sum(live_pnls)
    if live_today:
        avg_win.append(max(0, live_today))
        avg_loss.append(max(0, -live_today))

    for k,v in dist.items():
        out["Distance"][k or "unk"] = {"today": _safe_avg(v)}
    for k,v in fav.items():
        out["Favourite Rank"][k or "unk"] = {"today": _safe_avg(v)}
    for k,v in dow.items():
        out["Day-of-Week"][k or "unk"] = {"today": _safe_avg(v)}

    out["Orders"] = {
        "Stoploss Discipline": {"today": 1 - _safe_avg(stoploss)},
        "Hedge Efficiency": {"today": _safe_avg(hedge)},
        "Avg Win per Mkt": {"today": _safe_avg(avg_win)},
        "Avg Loss per Mkt": {"today": -_safe_avg(avg_loss)},
        "Live Net PnL": {"today": live_today}
    }
    return out

# ───────────────────────────────────────────────────────────────────────
def _write_json(path: str, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return path

# ───────────────────────────────────────────────────────────────────────
def build(period: str, days: int) -> dict:
    con = _adb_ro()
    rows = _fetch_posteriors(con, days)
    live = _fetch_live_orders(con)
    con.close()

    ag = _aggregate(rows, live)
    pnl_vals = [float(r["net_pnl"] or 0) for r in rows] + \
               [float(r["net_pl"] or r["realized_pnl"] or 0) for r in live]
    pnl_avg = _safe_avg(pnl_vals)

    snap = {
        "meta": {
            "period": period,
            "generated": _now_utc(),
            "source": "rebuild",
            "rows": len(rows),
            "live_rows": len(live)
        },
        "metrics": {"pnl_avg": pnl_avg},
        "groups": ag
    }
    path = os.path.join(DATA_DIR, f"mastery_snapshot_{period}.json")
    _write_json(path, snap)
    return snap

# ───────────────────────────────────────────────────────────────────────
def update() -> dict:
    latest = build("latest", 1)
    seven = build("7d", 7)
    month = build("30d", 30)
    quarter = build("90d", 90)
    ledger = {
        "last_run": _now_utc(),
        "source": "rebuild",
        "snapshots": [
            "mastery_snapshot_latest.json",
            "mastery_snapshot_7d.json",
            "mastery_snapshot_30d.json",
            "mastery_snapshot_90d.json"
        ]
    }
    _write_json(os.path.join(DATA_DIR, "ledger_status.json"), ledger)
    print(f"[snapshot_ledger] updated → {DATA_DIR} | avg={latest['metrics']['pnl_avg']:.2f}")
    return ledger

# ───────────────────────────────────────────────────────────────────────
def read(period: str = "latest") -> dict:
    path = os.path.join(DATA_DIR, f"mastery_snapshot_{period}.json")
    if not os.path.exists(path):
        print(f"[snapshot_ledger] missing {path} → rebuilding")
        update()
    with open(path) as f:
        data = json.load(f)
    if "meta" not in data:
        data["meta"] = {"period": period, "generated": _now_utc(), "source": "cache"}
    else:
        data["meta"]["source"] = "cache"
    return data
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
