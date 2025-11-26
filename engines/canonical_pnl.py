#!/usr/bin/env python3
"""
canonical_pnl.py — unified theoretical P&L engine

Calculates canonical per-order P&L and aggregates by
(letter, venue, country, fav_band, mto_band)
using winner inference from inbound_oc_cache and favourite
ranking from OC3 stage.

Used by: replay_digest.py, dashboard_data.py, mastery_posteriors.py
"""

import sqlite3, datetime, math, os


def to_dt(s):
    """Parse timestamp in ISO or Betfair Z format."""
    if not s:
        return None
    s = str(s).replace("Z", "").replace("T", " ")
    try:
        return datetime.datetime.fromisoformat(s)
    except Exception:
        return None


def fav_rank_from_oc3(con, mid: str) -> dict:
    """Return {selectionId: rank} for each runner in OC3 snapshot."""
    rows = con.execute("""
        SELECT selectionId, odd
          FROM oc_series
         WHERE marketId=? AND stage='OC3' AND odd IS NOT NULL
    """, (mid,)).fetchall()
    ranked = sorted(rows, key=lambda x: float(x[1]))
    return {str(sid): i + 1 for i, (sid, _) in enumerate(ranked)}


def calc_pnl_context(day: str = "yesterday") -> dict:
    """
    Main canonical P&L calculator.

    Returns:
        dict { (letter, venue, country, fav_band, mto_band): pnl }
    """

    root = "data"
    auto_db = os.path.join(root, "autoscalp_gui.db")
    bets_db = os.path.join(root, "bets.db")

    con = sqlite3.connect(auto_db)
    con.execute(f"ATTACH '{bets_db}' AS bets")
    con.row_factory = sqlite3.Row

    # Irish venue mapping for country inference
    IE_VENUES = {
        "Curragh","Leopardstown","Punchestown","Fairyhouse","Naas","Gowran Park",
        "Roscommon","Listowel","Limerick","Thurles","Tipperary","Tramore",
        "Down Royal","Downpatrick","Kilbeggan","Bellewstown","Galway",
        "Wexford","Dundalk","Clonmel"
    }

# === PATCH START ===
# 📍 TARGET: engines/canonical_pnl.py:calc_pnl_context
# 🔎 SEARCH: # all markets updated for the day
# 📆 PATCHED: 2025-10-15Z — anchor canonical day to bets.marketStartTime
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # all markets for the canonical day — anchored to race start time
    if day.lower() in ("today", "now"):
        day_sql = "date('now','utc')"
    elif day.lower() in ("yesterday", "prev", "previous"):
        day_sql = "date('now','-1 day','utc')"
    else:
        # explicit date string (e.g. '2025-10-14')
        day_sql = f"date('{day}','utc')"

    mids = [r[0] for r in con.execute(f"""
        SELECT DISTINCT b.marketId
          FROM bets.bets b
         WHERE date(b.marketStartTime) = {day_sql}
           AND b.marketId IS NOT NULL
    """).fetchall()]
# === PATCH END ===


    agg = {}

    for mid in mids:
        # Winner: lowest OC in inbound cache
        win = con.execute("""
            SELECT selectionId
              FROM inbound_oc_cache
             WHERE marketId=?
             ORDER BY (
               CASE WHEN oc20 IS NOT NULL THEN oc20
                    WHEN oc19 IS NOT NULL THEN oc19
                    WHEN oc18 IS NOT NULL THEN oc18
                    WHEN oc17 IS NOT NULL THEN oc17
                    WHEN oc16 IS NOT NULL THEN oc16
                    WHEN oc15 IS NOT NULL THEN oc15
                    WHEN oc14 IS NOT NULL THEN oc14
                    WHEN oc13 IS NOT NULL THEN oc13
                    WHEN oc12 IS NOT NULL THEN oc12
                    WHEN oc11 IS NOT NULL THEN oc11
                    WHEN oc10 IS NOT NULL THEN oc10
                    WHEN oc9  IS NOT NULL THEN oc9
                    WHEN oc8  IS NOT NULL THEN oc8
                    WHEN oc7  IS NOT NULL THEN oc7
                    WHEN oc6  IS NOT NULL THEN oc6
                    WHEN oc5  IS NOT NULL THEN oc5
                    WHEN oc4  IS NOT NULL THEN oc4
                    WHEN oc3  IS NOT NULL THEN oc3
                    WHEN oc2  IS NOT NULL THEN oc2
                    ELSE oc1 END
             ) ASC
             LIMIT 1
        """, (mid,)).fetchone()
        if not win:
            continue
        win_sid = str(win[0])

        # Favourite ranks from OC3
        fav_rank = fav_rank_from_oc3(con, mid)

        # Orders and market metadata
        rows = con.execute("""
            SELECT SUBSTR(o.source,1,1) AS letter,
                   o.selectionId, o.side, o.entry_odds, o.entry_stake,
                   b.event_name, b.market_name, b.marketStartTime, o.opened_at
              FROM orders o
              LEFT JOIN bets.bets b
                ON o.marketId=b.marketId AND o.selectionId=b.selectionId
             WHERE o.marketId=?
        """, (mid,)).fetchall()

        for r in rows:
            letter = r["letter"]
            sel = str(r["selectionId"])
            side = r["side"]
            odds = r["entry_odds"]
            stake = r["entry_stake"]
            venue = r["event_name"]
            mkt_start = r["marketStartTime"]
            opened_at = r["opened_at"]

            if None in (letter, side, odds, stake):
                continue

            # minutes-to-off
            dt_start = to_dt(mkt_start)
            dt_open = to_dt(opened_at)
            mto = (dt_start - dt_open).total_seconds() / 60 if dt_start and dt_open else None

            # theoretical P&L
            if sel == win_sid:
                pnl = stake * (odds - 1) if side.upper() == "BACK" else -stake * (odds - 1)
            else:
                pnl = -stake if side.upper() == "BACK" else stake

            # favourite bands from OC3
            rank = fav_rank.get(sel)
            fav_band = (
                "Fav1" if rank == 1 else
                "Fav2" if rank == 2 else
                "Fav3-5" if rank and 3 <= rank <= 5 else
                "Field" if rank else "Unranked"
            )
            # time-to-off bands
            mto_band = (
                ">15m" if mto and mto > 15 else
                "5-15m" if mto and 5 <= mto <= 15 else
                "<5m" if mto and mto >= 0 else
                "Unknown"
            )

            venue = venue or "Unknown"
            country = "IE" if any(v.lower() in venue.lower() for v in IE_VENUES) else "GB"

            key = (letter, venue, country, fav_band, mto_band)
            agg[key] = agg.get(key, 0.0) + pnl

    con.close()
    return agg


# standalone execution for quick testing
if __name__ == "__main__":
    result = calc_pnl_context("yesterday")
    total = round(sum(result.values()), 2)
    print(f"Total canonical P&L: £{total:,.2f}")
    for k, v in list(result.items())[:10]:
        print(k, "→", v)
