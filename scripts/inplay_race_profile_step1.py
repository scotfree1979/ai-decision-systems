#!/usr/bin/env python3
# scripts/inplay_race_profile_step1.py

import sqlite3
from engines.config_paths import auto_conn
from engines.price_math import calculate_tick_distance

# Toggle day here
DAY_SQL = "date('now','utc')"
#DAY_SQL = "date('now','utc','-1 day')"


def first_non_null(*vals):
    for v in vals:
        if v is not None:
            return v
    return None


def any_between_1_and_20(row):
    for i in range(1, 21):
        v = row.get(f"oc{i}")
        if v is not None and 1.0 <= v <= 20.0:
            return True
    return False


def classify_tick_move(ticks):
    if ticks is None or ticks == 0:
        return "FLAT"

    abs_ticks = abs(ticks)

    if abs_ticks <= 3:
        strength = "CONSERVATIVE"
    elif abs_ticks <= 10:
        strength = "NORMAL"
    else:
        strength = "AGGRESSIVE"

    return f"STEAM_{strength}" if ticks < 0 else f"DRIFT_{strength}"


def main():
    con = auto_conn(rw=False)
    con.row_factory = sqlite3.Row

    try:
        con.execute("ATTACH DATABASE 'data/bets.db' AS bets")

        markets = con.execute(f"""
            SELECT DISTINCT marketId, marketStartTime
            FROM bets.bets
            WHERE date(date) = {DAY_SQL}
            ORDER BY marketStartTime ASC
        """).fetchall()

        if not markets:
            print("No markets found")
            return

        print("\n================ OC1 → OC20 SNAPSHOT (FILTERED) ================\n")

        for m in markets:
            market_id = m["marketId"]

            rows = con.execute(f"""
                SELECT
                    b.selectionId,
                    b.anchor_odd AS anchor_odd,

                    oc.oc1, oc.oc2, oc.oc3, oc.oc4, oc.oc5,
                    oc.oc6, oc.oc7, oc.oc8, oc.oc9, oc.oc10,
                    oc.oc11, oc.oc12, oc.oc13, oc.oc14, oc.oc15,
                    oc.oc16, oc.oc17, oc.oc18, oc.oc19, oc.oc20,

                    SUM(CASE WHEN o.role='PARENT'
                              AND UPPER(o.entry_status)='MATCHED'
                             THEN 1 ELSE 0 END) AS parents,

                    SUM(CASE WHEN o.role='CHILD'
                              AND UPPER(o.entry_status)='MATCHED'
                             THEN 1 ELSE 0 END) AS children

                FROM bets.bets b
                LEFT JOIN inbound_oc_cache oc
                       ON oc.marketId=b.marketId
                      AND oc.selectionId=b.selectionId
                LEFT JOIN orders o
                       ON o.marketId=b.marketId
                      AND o.selectionId=b.selectionId
                      AND date(o.opened_at)={DAY_SQL}
                WHERE b.marketId=?
                GROUP BY b.marketId, b.selectionId, b.anchor_odd
            """, (market_id,)).fetchall()

            if not rows:
                continue

            # --------------------------------------------------
            # OPEN parents only (no hedge_of child)
            # --------------------------------------------------
            open_parents = con.execute(f"""
                SELECT o.selectionId, UPPER(o.side) AS side,
                       o.entry_odds AS odds, o.entry_stake AS stake
                FROM orders o
                WHERE o.marketId=?
                  AND o.role='PARENT'
                  AND UPPER(o.entry_status)='MATCHED'
                  AND date(o.opened_at)={DAY_SQL}
                  AND NOT EXISTS (
                      SELECT 1 FROM orders c WHERE c.hedge_of=o.id
                  )
            """, (market_id,)).fetchall()

            open_effects = {}
            for p in open_parents:
                sid = str(p["selectionId"])
                side = p["side"]
                odds = float(p["odds"])
                stake = float(p["stake"])

                if side == "BACK":
                    win, lose = stake * (odds - 1), -stake
                else:
                    win, lose = -(stake * (odds - 1)), stake

                open_effects.setdefault(sid, [0.0, 0.0])
                open_effects[sid][0] += win
                open_effects[sid][1] += lose

            enriched = []

            for r in rows:
                r = dict(r)

                if not (r["parents"] and any_between_1_and_20(r)):
                    continue

                base_px = first_non_null(
                    r["oc1"], r["oc2"], r["oc3"], r["anchor_odd"]
                )
                oc6 = r["oc6"]

                ticks = None
                if base_px is not None and oc6 is not None:
                    ticks = calculate_tick_distance(float(base_px), float(oc6))

                move = classify_tick_move(ticks)
                sel = str(r["selectionId"])

                pnl_if_win = 0.0
                for sid, (win, lose) in open_effects.items():
                    pnl_if_win += win if sid == sel else lose

                enriched.append({
                    "selectionId": sel,
                    "base_px": base_px,
                    "oc6": oc6,
                    "ticks": ticks,
                    "move": move,
                    "parents": r["parents"],
                    "children": r["children"] or 0,
                    "pnl_if_win": pnl_if_win,
                })

            if not enriched:
                continue

            # --------------------------------------------------
            # Ranking columns (EXPLICIT)
            # --------------------------------------------------
            for i, e in enumerate(sorted(enriched, key=lambda x: x["base_px"]), 1):
                e["rank_base_px"] = i

            for i, e in enumerate(sorted(enriched, key=lambda x: x["ticks"] or 9999), 1):
                e["rank_ticks"] = i

            for i, e in enumerate(sorted(enriched, key=lambda x: -x["pnl_if_win"]), 1):
                e["rank_pnl"] = i

            # simple composite (tunable later)
            for e in enriched:
                e["risk_score"] = (
                    e["rank_base_px"]
                    + e["rank_ticks"]
                    + e["rank_pnl"]
                )

            enriched.sort(key=lambda x: x["risk_score"])

            print(f"MARKET {market_id}")
            print("-" * 160)
            print(
                f"{'#':<3} {'selectionId':<12} {'base_px':<8} {'OC6':<8} "
                f"{'ticks':<7} {'move':<20} "
                f"{'r_px':<5} {'r_tk':<5} {'r_pnl':<6} "
                f"{'pnl_if_win':<12}"
            )
            print("-" * 160)

            for i, e in enumerate(enriched, 1):
                print(
                    f"{i:<3} {e['selectionId']:<12} "
                    f"{(f'{e['base_px']:.2f}' if e['base_px'] else '-'): <8} "
                    f"{(f'{e['oc6']:.2f}' if e['oc6'] else '-'): <8} "
                    f"{(e['ticks'] if e['ticks'] is not None else '-'): <7} "
                    f"{e['move']:<20} "
                    f"{e['rank_base_px']:<5} "
                    f"{e['rank_ticks']:<5} "
                    f"{e['rank_pnl']:<6} "
                    f"{e['pnl_if_win']:<12.2f}"
                )

            print()

        print("================ END =================\n")

    finally:
        try:
            con.execute("DETACH DATABASE bets")
        except Exception:
            pass
        con.close()


if __name__ == "__main__":
    main()
