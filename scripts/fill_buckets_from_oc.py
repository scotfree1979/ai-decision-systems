#!/usr/bin/env python3
"""
scripts/fill_buckets_from_oc.py
────────────────────────────────────────────────────────────────────────
Schema-verified integration — 2025-11-07Z
Builds Mastery v7 → bucket_map with odds-movement archetypes.

Reads:
  • bets.db:bets (anchor_odd, OC0_band)
  • autoscalp_gui.db:inbound_oc_cache (oc1..oc20 + ocX_band_json)
Writes:
  • mastery_v7.db:bucket_map (extended schema with drift & band stats)
"""

import os, sys, sqlite3, time, math, json

# ── ensure repo root on sys.path ───────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engines.config_paths import mastery_v7_db, autoscalp_db, bets_db


# ── helpers ────────────────────────────────────────────────────────────
def _connect(path: str, ro=False):
    uri = f"file:{path}?mode=ro" if ro else path
    con = sqlite3.connect(uri, uri=ro, timeout=10)
    con.row_factory = sqlite3.Row
    if not ro:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA busy_timeout=8000")
    return con

# === PATCH START ===
# 📍 TARGET: scripts/fill_buckets_from_oc.py
# 🔎 SEARCH: oc_val = _safe_float(r.get(f"oc{i}"))
# 📆 PATCHED: 2025-11-07Z — fix sqlite3.Row access (.get → [] with default)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _safe_row_value(row, key, default=None):
    """Safe accessor for sqlite3.Row that behaves like dict.get()."""
    try:
        return row[key]
    except Exception:
        return default





def _safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


def _band_parse(raw: str | None):
    if not raw:
        return (None, None)
    try:
        obj = json.loads(raw)
        return (_safe_float(obj.get("low")), _safe_float(obj.get("high")))
    except Exception:
        return (None, None)


def _ensure_bucket_schema(con):
    """Extend bucket_map table if missing any new columns."""
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bucket_map(
            marketId TEXT,
            selectionId TEXT,
            fav_rank INTEGER,
            winner_flag INTEGER,
            bucket_name TEXT,
            entered_oc INTEGER,
            left_oc INTEGER,
            created_at TEXT DEFAULT (datetime('now','utc'))
        )
    """)
    # check and add extra fields if missing
    cols = {r[1] for r in cur.execute("PRAGMA table_info(bucket_map)")}
    add_cols = {
        "drift_ratio": "REAL",
        "band_change": "REAL",
        "reversals": "INTEGER",
        "monotone_flag": "TEXT",
        "avg_volatility": "REAL",
        "story_hint": "TEXT",
    }
    for name, ddl in add_cols.items():
        if name not in cols:
            cur.execute(f"ALTER TABLE bucket_map ADD COLUMN {name} {ddl}")
    con.commit()


# ── main ───────────────────────────────────────────────────────────────
def main(limit_markets=None):
    t0 = time.time()
    bdb, gui_db, mdb = bets_db(), autoscalp_db(), mastery_v7_db()
    print(f"[BUCKETS] anchors={bdb}")
    print(f"[BUCKETS] inbound={gui_db}")
    print(f"[BUCKETS] writing={mdb}")

    con_b = _connect(bdb, ro=True)
    con_g = _connect(gui_db, ro=True)
    con_m = _connect(mdb)
    _ensure_bucket_schema(con_m)
    cur_m = con_m.cursor()

    # pull markets to process
    mids = [r["marketId"] for r in con_g.execute(
        "SELECT DISTINCT marketId FROM inbound_oc_cache ORDER BY marketId"
    ).fetchall()]
    if limit_markets:
        mids = mids[:limit_markets]

    inserted = 0

    for mid in mids:
        # anchor layer
        anchors = {}
        for r in con_b.execute("""
            SELECT selectionId, COALESCE(anchor_odd, OC0) AS anchor_odd, OC0_band
              FROM bets WHERE marketId=?
        """, (mid,)).fetchall():
            low0, high0 = _band_parse(r["OC0_band"])
            anchors[str(r["selectionId"])] = {
                "anchor": _safe_float(r["anchor_odd"]),
                "low0": low0,
                "high0": high0,
            }

        # inbound wide table (oc1..oc20)
        rows = con_g.execute("""
            SELECT * FROM inbound_oc_cache WHERE marketId=?;
        """, (mid,)).fetchall()
        if not rows:
            continue

        for r in rows:
            sid = str(r["selectionId"])
            oc_vals, low_bands, high_bands = [], [], []

            for i in range(1, 21):
                oc_val = _safe_float(_safe_row_value(r, f"oc{i}"))
                band_json_raw = _safe_row_value(r, f"oc{i}_band_json")
                low, high = _band_parse(band_json_raw)
                if oc_val is None:
                    continue
                oc_vals.append(oc_val)
                low_bands.append(low or oc_val)
                high_bands.append(high or oc_val)
            # === PATCH END ===

            if len(oc_vals) < 2:
                continue

            base = anchors.get(sid, {}).get("anchor") or oc_vals[0]
            low0 = anchors.get(sid, {}).get("low0") or low_bands[0]
            high0 = anchors.get(sid, {}).get("high0") or high_bands[0]

            oc_last = oc_vals[-1]
            drift_ratio = (oc_last - base) / base if base else 0.0
            base_width = (high0 - low0) if (high0 and low0) else (high_bands[0] - low_bands[0])
            last_width = (high_bands[-1] - low_bands[-1])
            band_change = (last_width - base_width) / base_width if base_width else 0.0

            reversals = sum(
                1 for i in range(2, len(oc_vals))
                if (oc_vals[i] - oc_vals[i-1]) * (oc_vals[i-1] - oc_vals[i-2]) < 0
            )

            monotone_flag = "UP" if all(oc_vals[i] >= oc_vals[i-1] for i in range(1, len(oc_vals))) \
                else "DOWN" if all(oc_vals[i] <= oc_vals[i-1] for i in range(1, len(oc_vals))) \
                else "MIXED"

            widths = [(high_bands[i] - low_bands[i]) for i in range(len(high_bands))]
            avg_volatility = sum(abs(w - base_width) for w in widths) / len(widths) / base_width if base_width else 0

            # early categorization (story hint)
            if drift_ratio < -0.1:
                story_hint = "steamer: odds collapsed pre-off"
            elif drift_ratio > 0.1:
                story_hint = "drifter: odds lengthened"
            elif band_change < -0.15:
                story_hint = "confidence surge: band tightened"
            elif band_change > 0.15:
                story_hint = "uncertainty: band widened"
            elif reversals >= 2:
                story_hint = "volatile: multiple reversals"
            else:
                story_hint = "stable trend"

            cur_m.execute("""
                INSERT INTO bucket_map(
                    marketId, selectionId, fav_rank, winner_flag,
                    bucket_name, entered_oc, left_oc,
                    drift_ratio, band_change, reversals,
                    monotone_flag, avg_volatility, story_hint
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                mid, sid, None, 0, "AUTO", 1, len(oc_vals),
                drift_ratio, band_change, reversals,
                monotone_flag, avg_volatility, story_hint
            ))
            inserted += 1

    con_m.commit()
    con_b.close(); con_g.close(); con_m.close()
    print(f"[BUCKETS] ✅ inserted {inserted:,} rows in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
