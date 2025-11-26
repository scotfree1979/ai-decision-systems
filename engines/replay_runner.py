#!/usr/bin/env python3
# === PATCH START ===
# 📍 TARGET: engines/replay_runner.py
# 🔎 SEARCH: ^#!/usr/bin/env python3
# ⛏️ ACTION: insert repo-root import shim after shebang

#!/usr/bin/env python3
#!/usr/bin/env python3


import os, sys, time, sqlite3, argparse, json
from datetime import datetime, timezone, timedelta


# --- ensure repo root is importable ---
_here = os.path.dirname(os.path.abspath(__file__))          # .../engines
_root = os.path.abspath(os.path.join(_here, ".."))          # .../analytics_beta_dev
if _root not in sys.path:
    sys.path.insert(0, _root)

from gui.dashboard_data import ACTIVE_LETTERS  # ← add this import near the top
# engines/replay_runner.py
"""
Standalone Replay Runner
- Replays historical markets at compressed speed
- Feeds ticks into Mastery
- Updates mastery_feed, mastery_events, bank_state
"""


# --- Imports from project ---
from engines.config_paths import autoscalp_db, bets_db, connect_db
from engines.live import bank_state
from engines.mastery import mastery_policy
from engines.mastery.event_sink import on_decision

TAG_WEIGHTS = {...}
LETTER_PRIORITY_PRE = [...]
LETTER_PRIORITY_IP = [...]


# --- GLOBALS (make visible to all functions) ---
TAG_WEIGHTS = {
    "stoploss_or_fail": 2.0,
    "unpaired_parent": 2.0,
    "parent_cancelled": 1.5,
    "parent_loss": 2.0,
    "child_cancelled": 1.5,
    "child_loss": 2.0,
    "market_loss": 5.0,
    "high_odds": 3.0,
    "tick1": 1.0,
    "tick2": 1.5,
    "tick3": 2.0,
    "steam_loss": 2.0,
    "drift_loss": 2.0,
    "late_entry": 2.5,
    "early_entry": 1.5,
    "overexposed": 3.0,
    "underexposed": 1.0,
    "no_exit": 2.0,
    "partial_fill": 2.0,
    "inplay_entry": 3.0,
    "volatility_spike": 2.5,
    "long_hold": 2.0,
    "multi_parent": 2.0,
    "class_band_loss": 2.0,
    "fav_loss": 2.5,
    "field_loss": 1.5,
    "distance_loss": 2.0,
    "race_loss": 2.0,
}

LETTER_PRIORITY_PRE = ["P", "S", "B", "G", "X", "R", "F", "L", "A"]
LETTER_PRIORITY_IP  = ["P", "I", "T", "C", "E", "K"]
# === PATCH END ===


# --- Replay Data Loaders ------------------------------------------------------

def _load_runners_for_day(day: str):
    """
    Return list of runners for a given day from bets.db.
    Each row: {mid, sid, horse, start_time}.
    """
    con = sqlite3.connect(bets_db()); con.row_factory = sqlite3.Row
    try:
        rows = con.execute("""
            SELECT marketId, selectionId, horse_name, marketStartTime
            FROM bets
            WHERE date(marketStartTime)=date(?)
            ORDER BY marketId, selectionId
        """, (day,)).fetchall()
        return [
            {
                "mid": str(r["marketId"]),
                "sid": str(r["selectionId"]),
                "horse": r["horse_name"],
                "start_time": r["marketStartTime"],
            }
            for r in rows
        ]
    finally:
        con.close()

# === PATCH START ===
# 📍 TARGET: engines/replay_runner.py
# 🔎 SEARCH: def _derive_fav_rank
# 📆 PATCHED: 2025-10-02
# ───────────────────────────────────────────────────────────────

import calendar

def _derive_day_of_week(day_iso: str) -> str:
    """Return short day-of-week label (Mon..Sun)."""
    try:
        y, m, d = map(int, day_iso.split("-"))
        return calendar.day_name[datetime(y, m, d).weekday()][:3]
    except Exception:
        return "unk"


# === PATCH START ===
# 📍 TARGET: engines/replay_runner.py
# 📆 PATCHED: 2025-10-19T00:45Z — derive segment_key directly from market_name
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _derive_surface_type(mid: str) -> str:
    """
    Unified classifier for race segmentation using market_name.
    Produces keys like:
      flat_sprint_handicap
      jumps_stayer_novice
      flat_middle_listed
    """
    import re, sqlite3
    try:
        con = sqlite3.connect(bets_db()); con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT market_name FROM bets WHERE marketId=? LIMIT 1", (mid,)
        ).fetchone()
        con.close()
        n = (row["market_name"] or "").lower().strip() if row else ""
    except Exception:
        return "unknown"

    # --- Surface ---
    surface = "jumps" if any(x in n for x in ("hurdle", "hrd", "chase", "chs", "nhf")) else "flat"

    # --- Distance ---
    distance = "unknown"
    m = re.search(r"(\d+m\d*f|\d+m|\d+f)", n)
    d = m.group(1) if m else ""
    if any(x in d for x in ("5f", "6f", "7f")):
        distance = "sprint"
    elif any(x in d for x in ("1m", "8f", "9f", "10f", "11f", "12f", "13f", "14f")):
        distance = "middle"
    elif any(x in d for x in ("1m6f", "2m", "2m1f", "2m2f")):
        distance = "stayer"
    elif any(x in d for x in ("3m", "4m")):
        distance = "long_stayer"

    # --- Race type ---
    if "hcap" in n:
        rtype = "handicap"
    elif "nursery" in n:
        rtype = "nursery"
    elif "mdn" in n:
        rtype = "maiden"
    elif "nov" in n:
        rtype = "novice"
    elif "listed" in n:
        rtype = "listed"
    else:
        rtype = "other"

    return f"{surface}_{distance}_{rtype}"
# === PATCH END ===



def _load_oc_series_for_runner(mid: str, sid: str, day: str):
    con = sqlite3.connect(autoscalp_db()); con.row_factory = sqlite3.Row
    try:
        rows = con.execute("""
            SELECT snapshot_ts, odd
            FROM oc_series
            WHERE marketId=? AND selectionId=? AND date(snapshot_ts)=date(?)
            ORDER BY datetime(snapshot_ts) ASC
        """, (mid, sid, day)).fetchall()
        return [(r["snapshot_ts"], float(r["odd"] or 0.0)) for r in rows]
    finally:
        con.close()



# Helpers ---------------------------------------------------------

def _load_days(limit=None):
    con = sqlite3.connect(bets_db()); con.row_factory = sqlite3.Row
    try:
        q = "SELECT DISTINCT date(snapshot_ts) AS d FROM oc_series ORDER BY d DESC"
        rows = con.execute(q).fetchall()
        days = [str(r["d"]) for r in rows if r["d"]]
        if not days:
            # fallback: use settlement days
            con2 = sqlite3.connect("data/settlements.db"); con2.row_factory = sqlite3.Row
            rows2 = con2.execute("SELECT DISTINCT date(settledDate) AS d FROM bf_cleared_orders ORDER BY d DESC").fetchall()
            days = [str(r["d"]) for r in rows2 if r["d"]]
            con2.close()
        return days[:limit] if limit else days
    finally:
        con.close()


def _load_markets_for_day(day):
    """Return list of marketIds for a given day."""
    con = sqlite3.connect(bets_db()); con.row_factory = sqlite3.Row
    try:
        q = "SELECT DISTINCT marketId FROM oc_series WHERE date(snapshot_ts)=? ORDER BY marketId"
        return [str(r["marketId"]) for r in con.execute(q, (day,)).fetchall()]
    finally:
        con.close()

def _load_ticks(mid, sid, day):
    """Return tick timeline (ts, odd) for a runner on given day."""
    con = sqlite3.connect(bets_db()); con.row_factory = sqlite3.Row
    try:
        q = """
          SELECT snapshot_ts, odd
          FROM oc_series
          WHERE marketId=? AND selectionId=? AND date(snapshot_ts)=?
          ORDER BY datetime(snapshot_ts) ASC
        """
        return [(r["snapshot_ts"], float(r["odd"])) for r in con.execute(q,(mid,sid,day)).fetchall()]
    finally:
        con.close()

def _settlement_for_market(mid, day):
    con = sqlite3.connect("data/settlements.db"); con.row_factory = sqlite3.Row
    try:
        q = """
          SELECT SUM(profit) as pnl
          FROM bf_cleared_orders
          WHERE marketId=? AND date(settledDate)=?
        """
        r = con.execute(q,(mid,day)).fetchone()
        return float(r["pnl"] or 0.0)
    finally:
        con.close()

# === PATCH START ===
# 📍 TARGET: engines/replay_runner.py
# 🔎 SEARCH: def _settlement_for_market
# 📆 PATCHED: 2025-10-02
# ───────────────────────────────────────────────────────────────

def get_market_pnl(mid: str, day: str) -> dict[str, float]:
    """
    Return {selectionId: realised PnL} for given market/day
    using bf_cleared_orders in settlements.db.
    """
    con = sqlite3.connect("data/settlements.db"); con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT selectionId, SUM(profit) AS pnl
          FROM bf_cleared_orders
         WHERE marketId=? AND date(settledDate)=?
         GROUP BY selectionId
    """, (mid, day)).fetchall()
    con.close()
    return {str(r["selectionId"]): float(r["pnl"] or 0.0) for r in rows}


def _generate_variants(entry_px: float, *, stake: float = 2.0):
    """
    Generate candidate scalping strategies for a runner.
    Expands ticks (1–5), stop widths (1–3), both directions.
    """
    variants = []
    tick_targets = [1, 2, 3, 5]
    stop_widths  = [1, 2, 3]
    directions   = ["LAY->BACK", "BACK->LAY"]

    for t in tick_targets:
        for s in stop_widths:
            for d in directions:
                variants.append({
                    "ticks": t,
                    "stop": s,
                    "dir": d,
                    "stake": stake
                })
    return variants
# === PATCH END ===




# ───────────────────────────────────────────────────────────────
# 📍 TARGET: engines/replay_runner.py
# 🔎 SEARCH: def _simulate_variant
# 📆 PATCHED: 2025-10-01
# ───────────────────────────────────────────────────────────────

def _tick_size(price: float) -> float:
    """Return the Betfair tick size for a given price."""
    if price < 2.0: return 0.01
    if price < 3.0: return 0.02
    if price < 4.0: return 0.05
    if price < 6.0: return 0.1
    if price < 10.0: return 0.2
    if price < 20.0: return 0.5
    if price < 30.0: return 1.0
    if price < 50.0: return 2.0
    if price < 100.0: return 5.0
    if price <= 1000.0: return 10.0
    return 10.0

def ticks_between(p1: float, p2: float) -> int:
    """Return the number of ticks between two prices, based on the ladder."""
    step = _tick_size(min(p1, p2))
    try:
        return int(round(abs(p2 - p1) / step))
    except Exception:
        return 0


def _simulate_variant(ticks: list[tuple[str,float]], variant: dict, entry_px: float, stake: float = 2.0):
    """
    Given an odds timeline (ticks) and a variant (target ticks, stop width, direction),
    simulate whether hedge target or stop-loss would have fired first.
    Returns (success, pnl, stoploss_hit, hedge_hit, realized_ticks).
    """
    if not ticks: 
        return (False, 0.0, False, False, 0)

    target_ticks = int(variant["ticks"])
    stop_ticks   = int(variant["stop"])
    direction    = variant["dir"]

    px = float(entry_px)
    step = _tick_size(px)

    if direction == "LAY->BACK":
        hedge_px = px + target_ticks * step
        stop_px  = px - stop_ticks   * step
    else:  # BACK->LAY
        hedge_px = px - target_ticks * step
        stop_px  = px + stop_ticks   * step

    hit_hedge, hit_stop = False, False
    exit_px = px

    for ts, odd in ticks:
        odd = float(odd)
        if direction == "LAY->BACK":
            if odd >= hedge_px:
                hit_hedge, exit_px = True, odd
                break
            if odd <= stop_px:
                hit_stop, exit_px = True, odd
                break
        else:  # BACK->LAY
            if odd <= hedge_px:
                hit_hedge, exit_px = True, odd
                break
            if odd >= stop_px:
                hit_stop, exit_px = True, odd
                break

    realized_ticks = ticks_between(px, exit_px)
    pnl = 0.0
    if hit_hedge:
        pnl = +0.1 * stake * realized_ticks
    elif hit_stop:
        pnl = -0.1 * stake * realized_ticks

    return (hit_hedge, pnl, hit_stop, hit_hedge, realized_ticks)




# Core replay -----------------------------------------------------

# === PATCH START ===
# 📍 TARGET: engines/replay_runner.py
# 🔎 SEARCH: def run_replay
# ⛏️ ACTION: replace whole function with this enriched version
# 📆 PATCHED: 2025-10-01T03:00Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _derive_distance_band(px: float) -> str:
    """Rough banding of entry odds → 'sub2','2-4','4-8','8-12','12+'."""
    try:
        if px < 2.0: return "sub2"
        if px < 4.0: return "2-4"
        if px < 8.0: return "4-8"
        if px < 12.0: return "8-12"
        return "12+"
    except Exception:
        return "unknown"

def _derive_code(direction: str) -> str:
    """Direction to code (LAY->BACK=DRIFT, BACK->LAY=STEAM)."""
    d = (direction or "").upper()
    if d.startswith("LAY"): return "DRIFT"
    if d.startswith("BACK"): return "STEAM"
    return "FLAT"

def _derive_tto_window(start_iso: str, tick_ts: str) -> str:
    """Map minutes-to-off into windows."""
    try:
        from datetime import datetime, timezone
        s = datetime.fromisoformat(start_iso.replace("Z","+00:00")).astimezone(timezone.utc)
        t = datetime.fromisoformat(tick_ts.replace("Z","+00:00")).astimezone(timezone.utc)
        mto = (s - t).total_seconds()/60.0
        if mto > 60: return "60+"
        if mto > 30: return "30-60"
        if mto > 10: return "10-30"
        if mto > 0:  return "0-10"
        return "IP"
    except Exception:
        return "unknown"

def _derive_fav_rank(mid: str, sid: str, day: str) -> str:
    """Rank runner within market at first tick of the day."""
    con = sqlite3.connect(autoscalp_db()); con.row_factory = sqlite3.Row
    try:
        rows = con.execute("""
            SELECT selectionId, odd
            FROM oc_series
            WHERE marketId=? AND date(snapshot_ts)=date(?)
              AND odd IS NOT NULL
            GROUP BY selectionId
            HAVING MIN(datetime(snapshot_ts))
            ORDER BY odd ASC
        """, (mid, day)).fetchall()
        ordered = [str(r["selectionId"]) for r in rows]
        rank = ordered.index(str(sid))+1 if str(sid) in ordered else 99
        if rank == 1: return "fav"
        if rank == 2: return "2nd"
        if rank <= 4: return "top4"
        return "field"
    except Exception:
        return "unknown"
    finally:
        con.close()


# ───────────────────────────────────────────────────────────────
# 📍 TARGET: engines/replay_runner.py
# 🔎 SEARCH: def run_replay(days:int=1, speed_x:float=60.0, epochs:int=1):
# 📆 PATCHED: 2025-10-02
# ───────────────────────────────────────────────────────────────

def run_replay(days:int=1, speed_x:float=60.0, epochs:int=1, start_balance:float=1000.0):
    """
    Replay past market days at compressed speed.
    Bins outcomes by distance_band, code, tto_window, fav_rank_bin.
    start_balance: seed for bank_state in £
    """

    # --- PATCH START: fix bank_state reset call ---
    bank_state.reset_for_replay(start_balance)
    print(f"[replay] Bank start balance: {start_balance:.2f}")
    # --- PATCH END ---


    all_days = _load_days(limit=days)
    print(f"[replay] Found {len(all_days)} unique days to replay.")

    outcomes = []
    grand_total_variants = 0
    grand_total_epics = 0
    grand_total_stories = 0
    grand_total_chapters = 0

    try:
        for ep in range(epochs):
            print(f"\n[replay] ===== Epoch {ep+1}/{epochs} =====")

            for di, day in enumerate(all_days, 1):
                # --- Count hierarchy ---
                con = sqlite3.connect(autoscalp_db())
                con.row_factory = sqlite3.Row
                epics = con.execute(
                    "SELECT DISTINCT marketId FROM oc_series WHERE date(snapshot_ts)=date(?)",
                    (day,)
                ).fetchall()
                con.close()
                epic_count = len(epics)


                story_count, chapter_count = 0, 0
                for e in epics:
                    mid = str(e["marketId"])
                    con = sqlite3.connect(autoscalp_db()); con.row_factory = sqlite3.Row
                    stories = con.execute("SELECT DISTINCT selectionId FROM oc_series WHERE marketId=? AND date(snapshot_ts)=date(?)",(mid,day)).fetchall()
                    con.close()
                    story_count += len(stories)
                    for s in stories:
                        sid = str(s["selectionId"])
                        con = sqlite3.connect(autoscalp_db()); con.row_factory = sqlite3.Row
                        chap = con.execute("SELECT COUNT(*) AS n FROM oc_series WHERE marketId=? AND selectionId=? AND date(snapshot_ts)=date(?)",(mid,sid,day)).fetchone()
                        con.close()
                        chapter_count += int(chap["n"] or 0)

                print(f"[replay] Day {di}/{len(all_days)} {day} → {epic_count} epics, {story_count} stories, {chapter_count} chapters")
                grand_total_epics += epic_count
                grand_total_stories += story_count
                grand_total_chapters += chapter_count

                # --- Simulation loop ---
                total_variants, total_sids = 0, 0
                runners = _load_runners_for_day(day)
                for runner in runners:
                    mid, sid, start_iso = runner["mid"], runner["sid"], runner["start_time"]
                    ticks = _load_oc_series_for_runner(mid, sid, day)
                    if not ticks: continue

                    entry_px = ticks[0][1]
                    if not entry_px or entry_px<=0.0 or entry_px>=1000.0: continue

                    variants = _generate_variants(entry_px)
                    total_sids += 1

                    for var in variants:
                        for letter in ACTIVE_LETTERS:   # loop through all strategies
                      
                            success, pnl, stoploss_hit, hedge_hit, realized_ticks = _simulate_variant(ticks, var, entry_px)

                            fav_bin = _derive_fav_rank(mid, sid, day)
                            # Enrich class_band from bets if available
                            class_band = "mid"
                            try:
                                bcon = sqlite3.connect(bets_db()); bcon.row_factory = sqlite3.Row
                                brow = bcon.execute(
                                    "SELECT race_name, market_name FROM bets WHERE marketId=? LIMIT 1",
                                    (mid,)
                                ).fetchone()
                                bcon.close()
                                txt = (brow["race_name"] or "") + " " + (brow["market_name"] or "")
                                txt_low = txt.lower()
                                if "class 1" in txt_low: class_band = "class1"
                                elif "class 2" in txt_low: class_band = "class2"
                                elif "class 3" in txt_low: class_band = "class3"
                                elif "class 4" in txt_low: class_band = "class4"
                                elif "class 5" in txt_low: class_band = "class5"
                                elif "class 6" in txt_low: class_band = "class6"
                            except Exception:
                                pass

# ───────────────────────────────────────────────────────────────
# 📍 TARGET: engines/replay_runner.py
# 🔎 SEARCH: outcomes.append({
# 📆 PATCHED: 2025-10-01
# ───────────────────────────────────────────────────────────────

                            success, pnl, stoploss_hit, hedge_hit, realized_ticks = _simulate_variant(ticks, var, entry_px)

                            # … inside outcomes.append:
# === PATCH START ===
# 📍 TARGET: engines/replay_runner.py
# 🔎 SEARCH: market_pnls = get_market_pnl(day, mid)
# 📆 PATCHED: 2025-10-18T08:45Z — correct arg order + remove redundant overwrite
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                            # ✅ fetch canonical P&L for this market/day (args were reversed before)
                            market_pnls = get_market_pnl(mid, day)
                            settled_pnl = market_pnls.get(sid, 0.0)

                            # ✅ Hard stop for high odds
                            if entry_px >= 500.0:
                                continue

                            # ✅ Market-level liability management (cap ~20% balance)
                            market_liability_cap = 0.2 * start_balance
                            if abs(settled_pnl) > market_liability_cap:
                                continue

                            outcomes.append({
                                "day": day, "mid": mid, "sid": sid,
                                "letter": letter,
                                "band": "ACTIVE",
                                "epic_stories": story_count,
                                "distance_band": _derive_distance_band(entry_px),
                                "code": _derive_code(var["dir"]),
                                "tto_window": _derive_tto_window(start_iso, ticks[0][0]),
                                "fav_rank_bin": fav_bin,
                                "class_band": class_band,
                                "day_of_week": _derive_day_of_week(day),
# === PATCH START ===
# 📍 TARGET: engines/replay_runner.py
# 🔎 SEARCH: "surface_type": _derive_surface_type(race_name),
# 📆 PATCHED: 2025-10-19T03:10Z — fix NameError: race_name undefined
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                                "surface_type": _derive_surface_type(mid),
# === PATCH END ===

                                # ✅ canonical settlement value for this runner
                                "pnl": settled_pnl,
                                "target_ticks": var["ticks"],
                                "realized_ticks": realized_ticks,
                                "success": success,
                                "stoploss": stoploss_hit,
                                "hedge": hedge_hit,
                                "entry_px": entry_px
                            })
# === PATCH END ===




                            total_variants += 1

                    # update bank with actual settlement
                    pnl_settle = _settlement_for_market(mid, day)
                    bank_state.apply_settlement(pnl_settle)

                print(f"[replay] Day {di}/{len(all_days)} {day} → {total_sids} runners simulated, {total_variants} variants tested")
                grand_total_variants += total_variants
    except KeyboardInterrupt:
        print("[replay] STOP requested — wrapping up…")

    # --- Wrap-up ---
    _wrap_up_posteriors(outcomes)
    os.makedirs(os.path.join("data","posteriors"), exist_ok=True)
    try:
        from engines.mastery import posteriors
        snap_path = posteriors.emit_snapshot()
        print(f"[replay] Snapshot written → {snap_path}")
    except Exception as e:
        print(f"[replay] snapshot warn: {e}")

    print(f"[replay] Completed {epochs} epochs → {len(all_days)} days, {grand_total_epics} epics, {grand_total_stories} stories, {grand_total_chapters} chapters, {grand_total_variants} variants simulated")
# === PATCH END ===


# ───────────────────────────────────────────────────────────────
# 📍 TARGET: engines/replay_runner.py
# 🔎 SEARCH: # === NEW: per-letter summary ===
# 📆 PATCHED: 2025-10-01
# ───────────────────────────────────────────────────────────────

    # === NEW: per-letter summary ===
    try:
        from collections import defaultdict
        letter_stats = defaultdict(lambda: {"n":0,"wins":0,"losses":0,"pnl":0.0,"weighted_pnl":0.0})
        for o in outcomes:
            L = o.get("letter","?")
            letter_stats[L]["n"] += 1
            if o.get("success"): 
                letter_stats[L]["wins"] += 1
            else:
                letter_stats[L]["losses"] += 1
            letter_stats[L]["pnl"] += float(o.get("pnl") or 0.0)
            letter_stats[L]["weighted_pnl"] += float(o.get("pnl") or 0.0) * float(o.get("weight",1.0))

        if letter_stats:
            print("\n=== Per-Letter Performance ===")
            for L, st in sorted(letter_stats.items()):
                n = st["n"]
                w = st["wins"]
                l = st["losses"]
                pnl = st["pnl"]
                wpnl = st["weighted_pnl"]
                winrate = 100.0*w/n if n else 0.0
                print(f"  Letter {L:<3} {n:6d} trades | "
                      f"Wins {w:5d} / Losses {l:5d} | "
                      f"Win% {winrate:5.1f}% | "
                      f"Net P&L £{pnl:8.2f} | Weighted P&L £{wpnl:8.2f}")

            # --- NEW: persist per-letter report into data/reports ---
            try:
                os.makedirs("data/reports", exist_ok=True)
                ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                out_path = os.path.join("data/reports", f"replay_letters_{ts}.txt")
                with open(out_path, "w") as f:
                    f.write("=== Per-Letter Performance ===\n")
                    for L, st in sorted(letter_stats.items()):
                        n = st["n"]; w = st["wins"]; l = st["losses"]
                        pnl = st["pnl"]; wpnl = st["weighted_pnl"]
                        winrate = 100.0*w/n if n else 0.0
                        f.write(f"Letter {L:<3} {n:6d} trades | "
                                f"Wins {w:5d} / Losses {l:5d} | "
                                f"Win% {winrate:5.1f}% | "
                                f"Net P&L £{pnl:8.2f} | Weighted P&L £{wpnl:8.2f}\n")
                print(f"[replay] Letter summary saved → {out_path}")
            except Exception as e:
                print(f"[replay] letter summary persist warn: {e}")
    except Exception as e:
        print(f"[replay] letter summary warn: {e}")

# ───────────────────────────────────────────────────────────────
# 📍 TARGET: engines/replay_runner.py
# 🔎 SEARCH: print(f"[replay] race-type persist warn: {e}")
# 📆 PATCHED: 2025-10-01T22:10Z
# ───────────────────────────────────────────────────────────────

    # === NEW: Worst Race Types (persist into loss_tags) ===
    try:
        bcon = sqlite3.connect(bets_db()); bcon.row_factory = sqlite3.Row
        race_pnl = {}
        race_map = {}
        for o in outcomes:
            mid = o.get("mid")
            pnl = float(o.get("pnl",0.0))
            if not mid:
                continue
            row = bcon.execute("SELECT market_name FROM bets WHERE marketId=? LIMIT 1", (mid,)).fetchone()
            if not row:
                continue
            mname = row["market_name"] or "unknown"
            race_pnl.setdefault(mid, 0.0)
            race_map[mid] = mname
            race_pnl[mid] += pnl
        bcon.close()

        if race_pnl:
            print("\n=== Worst Race Types (by Net P&L) ===")
            for mid, pnl in sorted(race_pnl.items(), key=lambda x: x[1]):
                mname = race_map.get(mid,"unknown")
                print(f"  {mname:<40} Net P&L £{pnl:8.2f}")

            # persist losers into loss_tags
            con3 = sqlite3.connect(autoscalp_db()); cur = con3.cursor()
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            for mid, pnl in race_pnl.items():
                if pnl < 0:
                    tag = f"race_loss:{race_map.get(mid,'unknown')}"
                    cur.execute("""
                        INSERT INTO loss_tags(day, marketId, selectionId, tag, created_at)
                        VALUES (?,?,?,?,datetime('now','utc'))
                        ON CONFLICT(day, marketId, selectionId, tag) DO NOTHING
                    """, (today, mid, 0, tag))
            con3.commit(); con3.close()
    except Exception as e:
        print(f"[replay] race-type persist warn: {e}")

    print(f"[replay] Completed {epochs} epochs → "
          f"{len(all_days)} unique days, "
          f"{grand_total_epics} epics, {grand_total_stories} stories, "
          f"{grand_total_chapters} chapters, {grand_total_variants} variants simulated")




# 📍 TARGET: engines/replay_runner.py
# 🔎 SEARCH: print(f"[replay] Completed {epochs} epochs →
# 📆 PATCHED: 2025-10-01T09:35Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


    # === NEW: human-readable learning summary ===
    try:
        
        
        con = sqlite3.connect(autoscalp_db()); con.row_factory = sqlite3.Row

        # Grab yesterday’s error tags for digest
        tags = con.execute("""
            SELECT tag, COUNT(*) AS n
              FROM loss_tags
             WHERE day IN (SELECT MAX(day) FROM loss_tags)
             GROUP BY tag
        """).fetchall()

        con.close()

        print("\n=== Replay Learning Report ===")
        print(f"Days replayed: {', '.join(all_days)}")
        print(f"Epochs: {epochs}")
        print(f"Total variants tested: {grand_total_variants:,}")
        print(f"Total outcomes: {grand_total_chapters:,} chapters → {grand_total_stories:,} runners")

        if tags:
            print("\nError Tag Reduction (latest day)")
            for r in tags:
                print(f"  {r['tag']:<15} {r['n']} instances")

        # Opportunities (if available)
        try:
            con = sqlite3.connect(autoscalp_db()); con.row_factory = sqlite3.Row
            opps = con.execute("""
                SELECT SUM(opportunities) AS opps,
                       SUM(taken) AS taken,
                       SUM(conversion) AS conv
                  FROM indicators_opportunities
                 WHERE day IN (SELECT MAX(day) FROM indicators_opportunities)
            """).fetchone()
            con.close()
            if opps and opps["opps"]:
                taken_pct = 100.0 * (opps["taken"] or 0) / (opps["opps"] or 1)
                conv_pct  = 100.0 * (opps["conv"] or 0) / (opps["taken"] or 1)
                print("\nOpportunities")
                print(f"  Total observed: {int(opps['opps'] or 0)}")
                print(f"  Taken: {int(opps['taken'] or 0)} ({taken_pct:.1f}%)")
                print(f"  Conversion: {conv_pct:.1f}%")
        except Exception:
            pass

   
# ───────────────────────────────────────────────────────────────
# 📍 TARGET: engines/replay_runner.py
# 🔎 SEARCH: print("=== End of Report ===")
# 📆 PATCHED: 2025-10-01
# ───────────────────────────────────────────────────────────────

        # --- NEW: print tag weights applied in this run ---
        try:
            print("\n🔹 Tag Weight Configuration (hybrid model: max + 0.5 per extra tag)")
            for t, w in sorted(TAG_WEIGHTS.items()):
                print(f"  {t:<20} x{w}")
            print("  (multi-tag trades get +0.5 per extra tag on top of max)")
        except Exception as e:
            print(f"[replay] tag weight print warn: {e}")

# ───────────────────────────────────────────────────────────────
# 📍 TARGET: engines/replay_runner.py
# 🔎 SEARCH: print("=== End of Report ===")
# 📆 PATCHED: 2025-10-01T23:00Z
# ───────────────────────────────────────────────────────────────

        # --- NEW: summarize distance/race losses (from loss_tags) ---
        try:
            con = sqlite3.connect(autoscalp_db()); con.row_factory = sqlite3.Row
            drows = con.execute("""
                SELECT tag, COUNT(*) AS n
                  FROM loss_tags
                 WHERE day IN (SELECT MAX(day) FROM loss_tags)
                   AND (tag LIKE 'distance_loss%' OR tag LIKE 'race_loss%')
                 GROUP BY tag
            """).fetchall()
            con.close()
            if drows:
                print("\n🔹 Distance / Race Loss Summary")
                for r in drows:
                    print(f"  {r['tag']:<30} {r['n']}")
        except Exception as e:
            print(f"[replay] distance/race summary warn: {e}")

        print("\n=== Letter Priority Map ===")
        print("Pre-off:", " → ".join(LETTER_PRIORITY_PRE))
        print("In-play:", " → ".join(LETTER_PRIORITY_IP))

# ───────────────────────────────────────────────────────────────
# 📍 TARGET: engines/replay_runner.py
# 🔎 SEARCH: print("=== End of Report ===")
# 📆 PATCHED: 2025-10-02
# ───────────────────────────────────────────────────────────────

        # --- NEW: print full tag weight table (34 tags) ---
        try:
            print("\n🔹 Tag Weights (problem penalties & success rewards)")
            for t, w in sorted(TAG_WEIGHTS.items(), key=lambda x: x[0]):
                print(f"  {t:<20} x{w:.2f}")
            print("  (multi-tag trades = max weight +0.5 per extra tag)")
        except Exception as e:
            print(f"[replay] tag weight print warn: {e}")

        print("=== End of Report ===")




    except Exception as e:
        print(f"[replay] summary warn: {e}")



    # --- Wrap-up ---
    _wrap_up_posteriors(outcomes)
    os.makedirs(os.path.join("data","posteriors"), exist_ok=True)

    try:
        from engines.mastery import posteriors
        snap_path = posteriors.emit_snapshot()
        print(f"[replay] Snapshot written → {snap_path}")
    except Exception as e:
        print(f"[replay] snapshot warn: {e}")

    print(f"[replay] Completed {epochs} epochs → "
          f"{len(all_days)} unique days, "
          f"{grand_total_epics} epics, {grand_total_stories} stories, "
          f"{grand_total_chapters} chapters, {grand_total_variants} variants simulated")

# === PATCH START ===
# 📍 TARGET: engines/replay_runner.py:_wrap_up_posteriors
# 📆 PATCHED: 2025-10-19T01:15Z — align surface segmentation with mastery_outcomes_raw schema
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _wrap_up_posteriors(outcomes):
    """
    Append outcomes to mastery_outcomes_raw only.
    Consolidation into mastery_posteriors is handled by
    replay_report_to_posteriors.py afterwards.
    """
    import os, sqlite3
    from datetime import datetime, timezone
# === PATCH START ===
# 📍 TARGET: engines/replay_runner.py:_wrap_up_posteriors
# 📆 PATCHED: 2025-10-19T15:10Z — block REPLAY inserts into mastery_outcomes_raw
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Prevent replay data from contaminating live mastery outcomes
    if os.environ.get("AUTOSCALP_MODE","").upper() == "REPLAY":
        print("[replay] skipping mastery_outcomes_raw insert for REPLAY mode")
        return
# === PATCH END ===

    print(f"[replay] Appending {len(outcomes)} outcomes → mastery_outcomes_raw")

    adb = sqlite3.connect(autoscalp_db()); adb.row_factory = sqlite3.Row
    cur = adb.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS mastery_outcomes_raw(
        day TEXT,
        marketId TEXT,
        selectionId TEXT,
        letter TEXT,
        distance_band TEXT,
        fav_rank TEXT,
        day_of_week TEXT,
        surface TEXT,
        class_band TEXT,
        target_ticks INTEGER,
        realized_ticks INTEGER,
        success INTEGER,
        stoploss_hit INTEGER,
        hedge_hit INTEGER,
        entry_px REAL,
        pnl REAL,
        weight REAL,
        priority INTEGER,
        created_at TEXT,
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT,
        confidence REAL
    );
    """)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur.execute("BEGIN")
    for o in outcomes:
        cur.execute("""
            INSERT INTO mastery_outcomes_raw(
                day, marketId, selectionId, letter, distance_band,
                fav_rank, day_of_week, surface, class_band,
                target_ticks, realized_ticks, success,
                stoploss_hit, hedge_hit, entry_px, pnl,
                weight, priority, created_at, source, confidence
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            o.get("day"),
            o.get("mid"),
            o.get("sid"),
            o.get("letter"),
            o.get("distance_band"),
            o.get("fav_rank_bin"),
            o.get("day_of_week"),
            o.get("surface_type"),   # ← holds the full flat/jumps classification
            o.get("class_band"),
            int(o.get("target_ticks", 0)),
            int(o.get("realized_ticks", 0)),
            int(o.get("success", 0)),
            int(o.get("stoploss", 0)),
            int(o.get("hedge", 0)),
            float(o.get("entry_px", 0.0)),
            float(o.get("pnl", 0.0)),
            float(o.get("weight", 1.0)),
            int(o.get("priority", 99)),
            now,
            "REPLAY",                              # source tag
            float(o.get("confidence", 0.5))        # confidence support
        ))
    cur.execute("COMMIT")
    adb.close()

    print(f"[replay] Outcomes appended OK → mastery_outcomes_raw")
    print("[replay] Next step: run replay_report_to_posteriors.py to consolidate into mastery_posteriors")
# === PATCH END ===


# CLI -------------------------------------------------------------

if __name__=="__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1, help="How many unique past days to replay")
    ap.add_argument("--speed", type=float, default=60.0, help="Replay speed multiplier")
    ap.add_argument("--epochs", type=int, default=1, help="How many times to repeat the available days")
    ap.add_argument("--start-balance", type=float, default=1000.0,
                    help="Starting balance to seed replay bank (£)")
    args = ap.parse_args()
    run_replay(days=args.days, speed_x=args.speed, epochs=args.epochs,
               start_balance=args.start_balance)