#!/usr/bin/env python3
"""
UNIFIED SYNTHETIC PIPELINE TEST
--------------------------------
Runs the *entire* DecideOnce → Placement → Router → Overwatcher → MSC → Risk
pipeline using **synthetic market + synthetic oc/odds** fully aligned
to the correct schema.

This test requires:
- local autoscalp_gui.db
- correct orders, decisions, oc_series, inbound_oc_cache, odds_current schemas
- AUTOSCALP_DISABLE_API=1 so no live odds are fetched
"""

import os, sys, sqlite3, datetime, json
from pprint import pprint

# ============================================================
#    FORCE TEST MODE — NO LIVE ODDS, NO API CALLS
# ============================================================
os.environ["AUTOSCALP_DISABLE_API"] = "1"
os.environ["BETFAIR_APP_KEY"] = "DUMMY"
os.environ["BETFAIR_SESSION_TOKEN"] = "DUMMY"

ROOT = "/Users/malachikelly/Dev/analytics_beta_dev"
GUI_DB = os.path.join(ROOT, "data", "autoscalp_gui.db")

sys.path.append(ROOT)

# Core engines
from engines.decision_engine.orchestrator import decide_once
from engines.live.overwatcher import enforce_stop_losses as overwatcher_tick
from engines.live.stoploss_engine import StopLossEngine
from engines.micro_scalper_v7.exploratory_engine import ExploratoryEngine
from engines.micro_scalper_v7.risk_engine import RiskEngine
from engines.decision_engine.decide_once.scope import build_and_maintain_scope

# ============================================================
# DB HELPERS
# ============================================================
def db():
    con = sqlite3.connect(GUI_DB, timeout=5, isolation_level=None)
    con.row_factory = sqlite3.Row
    return con

def now():   return datetime.datetime.now(datetime.timezone.utc).isoformat()
def today(): return datetime.date.today().isoformat()

# ============================================================
# CLEANUP
# ============================================================
def cleanup(mid):
    con = db()
    con.execute("DELETE FROM market_data WHERE marketId=?", (mid,))
    con.execute("DELETE FROM markets_schedule WHERE marketId=?", (mid,))
    con.execute("DELETE FROM odds_current WHERE marketId=?", (mid,))
    con.execute("DELETE FROM inbound_oc_cache WHERE marketId=?", (mid,))
    con.execute("DELETE FROM oc_series WHERE marketId=?", (mid,))
    con.execute("DELETE FROM decisions WHERE marketId=?", (mid,))
    con.execute("DELETE FROM plan_ledger WHERE marketId=?", (mid,))
    con.execute("DELETE FROM orders WHERE marketId=?", (mid,))
    con.execute("DELETE FROM events WHERE message LIKE '%TEST%'")
    con.commit()
    con.close()

# ============================================================
# SYNTHETIC INSERTS — FULLY SCHEMA-CORRECT
# ============================================================
def inject_test_market(mid, sid):
    con = db()
    ts = now()
    day = today()

    # market_data (schema confirmed)
    con.execute("""
        INSERT INTO market_data(
            marketId, selectionId, runnerName, trainerName, jockeyName,
            age, stallDraw, officialRating, weightValue, venue,
            eventName, marketName, marketStartTime, distance, going, raw_json
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        mid, sid, "Synthetic Horse", "Trainer", "Jockey",
        "5", "3", "80", "9-4", "TestTrack",
        "Test Event", "Test Market", ts,
        "6f", "GOOD", "{}"
    ))

    # markets_schedule (schema confirmed)
    con.execute("""
        INSERT INTO markets_schedule(
            marketId, venue, course, event_name, market_name,
            off_at_utc, country_code, off_ts, source, eventId, marketTypeCode
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        mid, "Track", "Course", "Event", "Market",
        (datetime.datetime.now(datetime.timezone.utc) +
         datetime.timedelta(minutes=10)).isoformat(),
        "UK", "", "TEST", "EVT", "WIN"
    ))

    # odds_current (schema verified)
    con.execute("""
        INSERT INTO odds_current(
            day, marketId, selectionId, updated_ts,
            ltp, back1, lay1, fav_rank_now,
            mto_minutes, slope_ppm, tick_vel_1s_up, tick_vel_3s_up
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        day, mid, sid, ts,
        3.02, 3.05, 3.10, 4,
        10, 50, 1, 3
    ))

    # inbound_oc_cache (schema verified)
    con.execute("""
        INSERT INTO inbound_oc_cache(
            marketId, selectionId,
            oc1, anchor_odd, oc1_band_json,
            last_sync_ts
        )
        VALUES (?,?,?,?,?,?)
    """, (
        mid, sid,
        3.02, 3.00, "[]",
        ts
    ))

    # oc_series (schema verified)
    for stage, odd in [("OC0",3.02),("OC1",3.04),("OC2",3.10)]:
        con.execute("""
            INSERT INTO oc_series(
                marketId, selectionId, stage, snapshot_ts,
                odd, band_low, band_high, band_json, meta_json, source
            )
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (mid, sid, stage, ts, odd, odd-0.05, odd+0.05, "{}", "{}", "TEST"))

    con.commit()
    con.close()

# ============================================================
# FULL SYNTHETIC PIPELINE
# ============================================================
def run_full_pipeline():
    MID = "1.TEST"
    SID = "101"

    print("Cleaning old test rows…")
    cleanup(MID)

    print("Injecting synthetic test market…")
    inject_test_market(MID, SID)

    print("Building scope…")
    build_and_maintain_scope()

    print("Running DecideOnce → Legacy parent…")
    run_id = "TEST-RUN"
    decide_once(run_id, source_override="TEST")

    con = db()
    parent = con.execute("""
        SELECT id, entry_status, side, entry_odds
        FROM orders
        WHERE marketId=? AND role='PARENT'
        ORDER BY id DESC LIMIT 1
    """, (MID,)).fetchone()
    con.close()

    print("\nLegacy parent:")
    pprint(dict(parent) if parent else parent)
    if not parent:
        print("\n❌ No parent placed — STOP.")
        return

    parent_id = parent["id"]

    # simulate match
    print("\nSimulating Legacy parent MATCH…")
    con = db()
    con.execute("""
        UPDATE orders
           SET entry_status='MATCHED',
               entry_bet_id='BF-PARENT',
               entry_matched_odds=entry_odds,
               entry_matched_stake=entry_stake
         WHERE id=?
    """, (parent_id,))
    con.commit()
    con.close()

    print("Running Overwatcher tick (Legacy child)…")
    overwatcher_tick()

    con = db()
    child = con.execute("""
        SELECT id, role, entry_status, hedge_of, entry_odds
        FROM orders
        WHERE hedge_of=?
        ORDER BY id DESC LIMIT 1
    """, (parent_id,)).fetchone()
    con.close()

    print("\nLegacy child:")
    pprint(dict(child) if child else child)

    print("\nSimulating Legacy child match…")
    if child:
        con = db()
        con.execute("""
            UPDATE orders
               SET exit_status='MATCHED',
                   exit_bet_id='BF-LEGACY-CHILD'
             WHERE id=?
        """, (child["id"],))
        con.commit()
        con.close()

    print("\nRunning Exploratory MSC parent/child…")
    msc = ExploratoryEngine()
    msc.tick()
    msc.tick()

    print("\nRunning RiskEngine parent/child…")
    risk = RiskEngine(parent_id)
    risk.tick()
    risk.tick()

    print("\nTriggering StopLoss Engine…")
    sl = StopLossEngine()
    result = sl.evaluate({
        "entry_odds": 3.02,
        "direction": "LAY",
        "baseline_ticks": 3,
        "equity": 10
    })
    print("StopLoss:", result)
    overwatcher_tick()

    # final summary
    con = db()
    print("\n=== FINAL ORDERS ===")
    orders = con.execute("""
        SELECT id, role, source, hedge_of, entry_status, exit_status,
               entry_odds, entry_stake
          FROM orders
         WHERE marketId=?
         ORDER BY id
    """, (MID,)).fetchall()
    pprint([dict(r) for r in orders])

    print("\n=== EVENTS ===")
    events = con.execute("""
        SELECT ts, source, message
          FROM events
         WHERE message LIKE '%TEST%'
    """).fetchall()
    pprint([dict(r) for r in events])

    con.close()
    print("\n=== DONE ===")

# ============================================================
# ENTRY
# ============================================================
if __name__ == "__main__":
    run_full_pipeline()
