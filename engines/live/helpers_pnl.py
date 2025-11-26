# engines/live/helpers_pnl.py
from __future__ import annotations
import sqlite3

# === PATCH START ===
# 📍 TARGET: engines/live/helpers_pnl.py:live_book_pnl_today
# 🔎 SEARCH: def live_book_pnl_today
# 📆 PATCHED: 2025-11-21

from engines.dal import open_local_readonly_auto

def live_book_pnl_today(db_path: str = "data/autoscalp_gui.db") -> float:
    """
    Compute today's guaranteed (matched parent+child) P&L using simple if-win/if-lose arithmetic.
    Ignores unsettled-only (single-leg) liability.
    """
    con = open_local_readonly_auto()
    con.row_factory = sqlite3.Row

    # Fetch all matched parents/children today
    rows = con.execute("""
        SELECT marketId, selectionId, role, side,
               entry_odds AS odds, entry_stake AS stake
          FROM orders
         WHERE date(opened_at)=date('now','utc')
           AND UPPER(entry_status)='MATCHED'
    """).fetchall()

    # group by (market,selection)
    book: dict[tuple[str,str], dict[str,list[tuple[str,float,float]]]] = {}
    for r in rows:
        key = (r["marketId"], r["selectionId"])
        book.setdefault(key, {"BACK": [], "LAY": []})
        book[key][r["side"].upper()].append((r["role"], float(r["odds"] or 0), float(r["stake"] or 0)))

    total_pnl = 0.0
    for (mid, sid), sides in book.items():
        backs = sum(st for _, _, st in sides.get("BACK", []))
        lays  = sum(st for _, _, st in sides.get("LAY", []))
        odds_back = max((o for _, o, _ in sides.get("BACK", [])), default=0)
        odds_lay  = max((o for _, o, _ in sides.get("LAY", [])), default=0)

        if backs and lays:
            # potential profit if the horse wins
            if_win = (odds_back - 1) * backs - (odds_lay - 1) * lays
            # potential profit if it loses
            if_lose = -backs + lays
            total_pnl += (if_win + if_lose) / 2.0  # midpoint expectation

    con.close()
    return round(total_pnl, 2)

# === PATCH END ===

