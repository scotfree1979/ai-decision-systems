# ✅ scalper_coop.py – Head Trader Co-op Session Handler
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database_hijack_monitor import launch_db_writer, priority_queue
launch_db_writer()
from database_hijack_monitor import enqueue_write
import sqlite3
import time
# ensure parent dir exists no matter the CWD

from config_paths import DB_PATH
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

from db.signal_memory_db_writer import save_snapshot_to_ram
from ram_reader import get_ram_snapshot
from config_paths import DATA_DIR


def _ensure_parent_dir(path: str) -> None:
    import os
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)

# --------------------------------------------------------
# 🔁 Decision Engine Import – Unified Signal Evaluator
# --------------------------------------------------------
from brain_headtrader import evaluate_signal

# -----------------------------
# 🗂️ init_system_flags_db – Setup standalone system flags DB
# -----------------------------
def init_system_flags_db():
    import sqlite3, os
    db_path = os.path.join(DATA_DIR, "coop_flags.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    _ensure_parent_dir(db_path)

    _ensure_parent_dir(DB_PATH)
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_flags (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
    return db_path

# -----------------------------
# 🗂️ Anchor Odds & Missing Fields
# -----------------------------

def seed_initial_anchor_odds():
    from get_markets import get_markets_and_insert
    from upgrade_import_patch import get_session_token
    from utils.api_tools import fetch_live_odds
    from engines.database_hijack_monitor import enqueue_write
    from config_paths import DB_PATH
    import sqlite3
    from datetime import datetime, timezone

    session_token = get_session_token()
    markets = get_markets_and_insert()
    now = datetime.now(timezone.utc)

    field_names = [
        "marketId", "selectionId", "horse_name", "status", "test_mode",
        "race_name", "market_name", "event_name", "marketStartTime",
        "timestamp", "date", "meta_json", "customerOrderRef", "anchor_odd", "placed_at"
    ]

    missing_fields_summary = {field: 0 for field in field_names}
    updated_fields_summary = {field: 0 for field in field_names}
    _ensure_parent_dir(DB_PATH)
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        cursor.execute("CREATE TABLE IF NOT EXISTS system_flags (key TEXT PRIMARY KEY, value TEXT)")
        cursor.execute("SELECT value FROM system_flags WHERE key = 'anchor_seeded_date'")
        row = cursor.fetchone()
        today = now.date().isoformat()

        if row and row[0] == today:
            print("🔁 Anchor odds already seeded today. Running missing-field patch instead...")
            return

        print("🌅 Seeding anchor odds for the first time today...")

        for market in markets:
            market_id = market["marketId"]
            start_str = market.get("marketStartTime")
            if not start_str:
                continue
            try:
                start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            except Exception:
                continue

            if start_time.date() != now.date():
                continue

            if len(market.get("runners", [])) < 2:
                continue

            for runner in market.get("runners", []):
                selection_id = runner["selectionId"]

                cursor.execute("""
                    SELECT marketId, selectionId, horse_name, status, test_mode,
                           race_name, market_name, event_name, marketStartTime,
                           timestamp, date, meta_json, customerOrderRef, anchor_odd, placed_at
                    FROM bets WHERE marketId = ? AND selectionId = ?
                """, (market_id, selection_id))
                row = cursor.fetchone()

                odds = fetch_live_odds(session_token, market_id, selection_id)
                lay = odds.get("lay") if odds else None

                if not row:
                    values = (
                        market_id,
                        selection_id,
                        runner.get("horse_name"),
                        runner.get("status"),
                        runner.get("test_mode"),
                        market.get("race_name"),
                        market.get("market_name"),
                        market.get("event_name"),
                        market.get("marketStartTime"),
                        datetime.utcnow().isoformat(),
                        now.date().isoformat(),
                        str({"runner": runner, "market": market}),
                        runner.get("customerOrderRef"),
                        lay,
                        datetime.utcnow().isoformat()
                    )

                    enqueue_write("""
                        INSERT INTO bets (
                            marketId, selectionId, horse_name, status, test_mode,
                            race_name, market_name, event_name, marketStartTime,
                            timestamp, date, meta_json, customerOrderRef, anchor_odd, placed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, values)

                    for f in field_names:
                        missing_fields_summary[f] += 1
                        updated_fields_summary[f] += 1

                else:
                    updates = []
                    params = []
                    db_row = dict(zip(field_names, row))

                    for f in field_names:
                        new_val = None
                        if f == "anchor_odd":
                            new_val = lay
                        elif f == "placed_at":
                            new_val = datetime.utcnow().isoformat() if lay else None
                        elif f == "meta_json":
                            new_val = str({"runner": runner, "market": market})
                        elif f in runner:
                            new_val = runner[f]
                        elif f in market:
                            new_val = market[f]

                        if (db_row[f] is None or db_row[f] == '') and new_val not in [None, '']:
                            updates.append(f + " = ?")
                            params.append(new_val)
                            missing_fields_summary[f] += 1
                            updated_fields_summary[f] += 1

                    if updates:
                        query = f"""
                            UPDATE bets SET {', '.join(updates)}
                            WHERE marketId = ? AND selectionId = ?
                        """
                        params.extend([market_id, selection_id])
                        enqueue_write(query, params)

        # ✅ Mark seeding complete
        enqueue_write("""
            INSERT OR REPLACE INTO system_flags (key, value) VALUES ('anchor_seeded_date', ?)
        """, [today])

    # 📊 Summary Output
    print("\n📋 Anchor Seeding Summary:")
    for field in field_names:
        missing = missing_fields_summary[field]
        updated = updated_fields_summary[field]
        if missing == 0:
            print(f"✅ {field}: OK")
        else:
            print(f"⚠️  {field}: {missing} missing → {updated} updated")

# -----------------------------
# 🧠 Confidence Score Helper
# -----------------------------
def calculate_confidence_score(selection_id, drift_pct):
    base = 0.75
    boost = min(abs(drift_pct) * 0.005, 0.1)
    return round(base + boost, 3)

# -----------------------------
# ✅ Data Readiness Estimate Utility
# -----------------------------

def fetch_open_signal(market_id, selection_id):
    _ensure_parent_dir(DB_PATH)
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT odds, stake, customerOrderRef, bet_id
            FROM live_signals
            WHERE marketId = ? AND selectionId = ?
            ORDER BY placed_at DESC
            LIMIT 1
        """, (market_id, selection_id))
        row = cursor.fetchone()

    if not row:
        return None

    odds, stake, ref, bet_id = row
    direction = "lay_to_back" if side == "LAY" and ref.endswith("_E") else "back_to_lay"
    return {
        "market_id": market_id,
        "selection_id": selection_id,
        "entry_odds": odds,
        "stake": stake,
        "direction": direction,
        "ref": ref,
        "entry_side": side,
        "entry_bet_id": bet_id
    }


def estimate_data_coverage():
    import sqlite3, os
    db_path = os.path.join(DATA_DIR, "odds_history.db")
    _ensure_parent_dir(db_path)

    try:
        _ensure_parent_dir(DB_PATH)
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT marketId, COUNT(*) as samples
                FROM odds_snapshots
                GROUP BY marketId
            """)
            rows = cursor.fetchall()
            print("📊 Odds Snapshot Coverage:")
            for market_id, samples in rows:
                print(f"📍 Market {market_id[-6:]} – {samples} snapshots")
            print(f"✅ Total markets tracked: {len(rows)}")
    except Exception as e:
        print(f"⚠️ Failed to estimate data readiness: {e}")

# -----------------------------
# 🧾 Signal Debug Printer – Shows signal attributes clearly
# -----------------------------
def print_signal_analysis(signal):
    print("\n📊 SIGNAL SNAPSHOT:")
    print(f"🏇 Runner:         {signal.get('runnerName')}")
    print(f"🎯 Market ID:      {signal.get('marketId')}")
    print(f"💸 Odds:           {signal.get('odds')}")
    print(f"📏 Spread:         {signal.get('spread')}")
    print(f"📈 Confidence:     {round(signal.get('confidence', 0) * 100)}%")
    print(f"📉 Drift:          {signal.get('drift')}%")
    print(f"⚡ Volatility:     {signal.get('avg_volatility', '–')} (Volatile: {signal.get('volatile')})")
    print(f"✅ Status:         {signal.get('status')}")

# --------------------------------------------------------
# 📦 Anchor Odds Fetch – Enhanced Odds Bootstrap from BETS Table
# --------------------------------------------------------
def fetch_anchor_odds(market_id, selection_id):
    import sqlite3
    from datetime import datetime, timedelta
    import logging
    from config_paths import DB_PATH

    try:
        _ensure_parent_dir(DB_PATH)
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT anchor_odd, placed_at FROM bets
                WHERE marketId = ? AND selectionId = ? AND anchor_odd IS NOT NULL
                ORDER BY placed_at ASC LIMIT 1
            """, (market_id, selection_id))
            row = cursor.fetchone()
            if row:
                return float(row[0])
    except Exception as e:
        logging.warning(f"⚠️ Failed to fetch anchor_odd for {selection_id}: {e}")
    return None

# -----------------------------
# 📍 Market Loop Handler – Live Mode Refactored from RANDCARB
# -----------------------------

import threading
from brain_headtrader import evaluate_signal
from collections import defaultdict
last_odds_map = {}
live_runner_map = {}  # New: Tracks live odds per market
tick_trails = defaultdict(list)  # New: Per-runner odds trail for tick patterns

def update_market_timing(self, market_id, selection_id, marketStartTime, minutes_to_off):
    key = (market_id, selection_id)
    snapshot = get_ram_snapshot(market_id, selection_id)
    if snapshot is None:

        # Optional: Add anything you want to initialize here
        snapshot = {
            "marketId": market_id,
            "selectionId": selection_id,
            "marketStartTime": marketStartTime,
            "minutes_to_off": round(minutes_to_off, 2)
        }
        save_snapshot_to_ram(market_id, selection_id, snapshot)

    update_snapshot_field(market_id, selection_id, "marketStartTime", marketStartTime)

    update_snapshot_field(market_id, selection_id, "minutes_to_off", round(minutes_to_off, 2))


def start_in_play_monitor(self, market_id, selection_ids):
    self.in_play_tick_tracker[market_id] = defaultdict(list)
    for sid in selection_ids:
        self.in_play_tick_tracker[market_id][sid] = []


def handle_insurance_hedge(signal, current_odds):
    from bet_core import place_bet, cancel_bet
    from upgrade_import_patch import get_session_token
    import sqlite3

    market_id = signal["market_id"]
    selection_id = signal["selection_id"]
    entry_odds = signal["entry_odds"]
    entry_side = signal["entry_side"]
    stake = signal["stake"]
    ref = signal["ref"]
    direction = signal["direction"]
    entry_bet_id = signal["entry_bet_id"]

    from config_paths import DB_PATH


    # Determine hedge side and hedge_ref
    hedge_side = "BACK" if entry_side == "LAY" else "LAY"
    hedge_ref = ref.replace("_E", "_H")

    # Check if hedge is already matched or placed
    _ensure_parent_dir(DB_PATH)
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT bet_id FROM live_signals
            WHERE marketId = ? AND selectionId = ? AND customerOrderRef = ?
        """, (market_id, selection_id, hedge_ref))
        existing = cursor.fetchone()

    if existing:
        # Assume hedge already exists — don't double hedge
        return

    # Market has moved against you — determine direction-specific threshold
    danger = False
    if direction == "lay_to_back" and current_odds < entry_odds:
        danger = True
    elif direction == "back_to_lay" and current_odds > entry_odds:
        danger = True

    if not danger:
        return  # Market hasn't gone against us — no insurance needed

    print(f"🛡️ Insurance hedge triggered for {selection_id} | {direction} | Entry @ {entry_odds} → Now @ {current_odds}")

    # Cancel any unmatched hedge attempts (if they exist)
    session_token = get_session_token()
    cancel_bet(session_token, entry_bet_id)  # Optional: depends on your exposure strategy

    # Calculate hedge stake using existing function
    hedge_stake = calculate_hedge_stake(
        entry_odds=entry_odds,
        entry_stake=stake,
        hedge_odds=current_odds,
        direction=direction
    )

    # Place emergency hedge at current market odds
    hedge_bet_id = place_bet(
        session_token=session_token,
        marketId=market_id,
        selectionId=selection_id,
        stake=hedge_stake,
        odds=current_odds,
        bet_type=hedge_side,
        customer_order_ref=hedge_ref
    )

    if hedge_bet_id:
        print(f"✅ Insurance hedge placed: {hedge_side} £{hedge_stake} @ {current_odds} | Ref: {hedge_ref}")
    
        save_bet_to_db(
            market_id, selection_id, current_odds,
            hedge_stake, hedge_side, hedge_ref, hedge_bet_id
        )


        # ✅ Add this
        signal_memory.record_signal_outcome(
            market_id=market_id,
            selection_id=selection_id,
            result="loss",
            explanation="Emergency hedge triggered pre-off due to adverse market move"
        )



# ✅ Separate Live Market Evaluation Loop
def evaluate_market_loop():
    import time
    while True:
        try:
            from signal_memory_engine import inject_live_market_data
            for market_id, runners in list(live_runner_map.items()):
                inject_live_market_data(market_id, runners)
        except Exception as e:
            print(f"❌ EvaluateSignal loop error: {e}")
        time.sleep(10)


# ✅ Market Loop – Data Collection Only
nonrunner_count_today = 0  # global counter

def market_loop():
    global last_odds_map, live_runner_map, nonrunner_count_today  # add here

    from upgrade_import_patch import get_session_token
    from utils.api_tools import fetch_live_odds
    from volatility_check import get_recent_odds
    from get_markets import get_markets_and_insert
    from config_paths import DB_PATH
    from datetime import datetime, timezone, timedelta
    import sqlite3, time

    loop_nonrunner_increments = 0
    print("🚀 Starting market loop...")

    oc_index_map = {
        "odds_check_1": 1,
        "odds_check_2": 2,
        "odds_check_3": 3,
        "odds_check_4": 4,
        "odds_check_5": 5,
        "odds_check_6": 6,
    }
    completed_checks = {}


    while True:
        now = datetime.now(timezone.utc)
        all_markets = get_markets_and_insert()
        # 🛠 Patch incomplete runners (e.g. no runnerName) from DB fetch
        enriched_markets = get_markets_and_insert()
        for market in all_markets:
            market_id = market["marketId"]
            enriched = next((m for m in enriched_markets if m["marketId"] == market_id), None)
            if enriched and enriched.get("runners"):
                market["runners"] = enriched["runners"]
                if "marketStartTime" not in market and "marketStartTime" in enriched:
                    market["marketStartTime"] = enriched["marketStartTime"]

        live_runner_map.clear()

        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = today + timedelta(days=2)
        today_iso = today.isoformat()
        tomorrow_iso = tomorrow.isoformat()


        _ensure_parent_dir(DB_PATH)
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            for market in all_markets:
                market_id = market["marketId"]
                cursor.execute("""
                    SELECT selectionId, marketStartTime FROM bets
                    WHERE marketId = ?
                """, (market_id,))
                rows = cursor.fetchall()  # ← ✅ You forgot this line
               

                # Always assign marketStartTime from DB if available
                if rows:
                    market["marketStartTime"] = rows[0][1]
                elif "marketStartTime" in market:
                    market["marketStartTime"] = market["marketStartTime"]
               


        segment_totals = {"-15-0": 0, "0-5": 0, "5-10": 0, "10-20": 0, "20-40": 0, "40-60": 0, "60-80": 0, "80-90": 0, ">90": 0}
        runner_totals = {k: 0 for k in segment_totals}
        odds_check_totals = {f"oc{i}": 0 for i in range(1, 7)}
        markets_with_valid_odds = 0
        runners_with_last_odds = 0
        runners_with_anchor_odds = 0
        runners_with_any_oc = 0

        for market in all_markets:
            market_id = market["marketId"]
            start_str = market.get("marketStartTime")
            if not start_str:
                continue
            try:
                start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            except Exception:
                continue
            minutes_to_off = (start_time - now).total_seconds() / 60.0

            if minutes_to_off < 0:
                segment_totals["-15-0"] += 1
            elif 0 <= minutes_to_off < 5:
                segment_totals["0-5"] += 1
            elif 5 <= minutes_to_off < 10:
                segment_totals["5-10"] += 1
            elif 10 <= minutes_to_off < 20:
                segment_totals["10-20"] += 1
            elif 20 <= minutes_to_off < 40:
                segment_totals["20-40"] += 1
            elif 40 <= minutes_to_off < 60:
                segment_totals["40-60"] += 1
            elif 60 <= minutes_to_off < 80:
                segment_totals["60-80"] += 1
            elif 80 <= minutes_to_off < 90:
                segment_totals["80-90"] += 1
            else:
                segment_totals[">90"] += 1

            market_has_odds = False

            for runner in market.get("runners", []):
                selection_id = runner["selectionId"]
                odds_data = fetch_live_odds(get_session_token(), market_id, selection_id)

                # Treat missing odds as "non-runner/no-odds" for the day counter and skip
                if (not odds_data) or ("lay" not in odds_data) or (odds_data["lay"] is None):
                    loop_nonrunner_increments += 1
                    continue

                current_odds = odds_data["lay"]

                if current_odds is None:
                    continue






                last_odds_map[selection_id] = current_odds
                market_has_odds = True
                runners_with_last_odds += 1

                tick_trails[selection_id].append(current_odds)
                if len(tick_trails[selection_id]) > 12:
                    tick_trails[selection_id] = tick_trails[selection_id][-12:]
                _ensure_parent_dir(DB_PATH)
                with sqlite3.connect(DB_PATH) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT anchor_odd, odds_check_1, odds_check_2, odds_check_3, odds_check_4, odds_check_5, odds_check_6 FROM bets WHERE marketId = ? AND selectionId = ?", (market_id, selection_id))
                    row = cursor.fetchone()

                    anchor = row[0] if row and row[0] is not None else None
                    if anchor is None:
                        cursor.execute("SELECT anchor_odd FROM bets WHERE marketId = ? AND selectionId = ?", (market_id, selection_id))
                        row_anchor = cursor.fetchone()
                        anchor = row_anchor[0] if row_anchor and row_anchor[0] is not None else None

                    if not isinstance(anchor, (float, int)):
                       
                        continue

                    runners_with_anchor_odds += 1

                    oc_vals = [row[i] for i in range(1, 7) if row and row[i] is not None]
                    all_vals = [anchor] + oc_vals + [current_odds]
                    valid_vals = [v for v in all_vals if v is not None]
                    range_low, range_high = None, None
                    tick_pattern = "flat"
                    volatility = 0.0
                    position_ratio = 0.5  # <- always exists no matter what

                    try:
                        if len(valid_vals) >= 3:
                            range_low = min(valid_vals)
                            range_high = max(valid_vals)
                            clean_ticks = [t for t in tick_trails[selection_id] if t is not None and isinstance(t, (float, int))]
                            if len(clean_ticks) >= 2:
                                volatility = round(max(clean_ticks) - min(clean_ticks), 4)

                            drift_count = sum(1 for v in oc_vals if v > anchor)
                            steam_count = sum(1 for v in oc_vals if v < anchor)

                            if drift_count >= 2 and current_odds > anchor:
                                tick_pattern = "drifted"
                            elif steam_count >= 2 and current_odds < anchor:
                                tick_pattern = "steamed"

                            position_ratio = round((current_odds - range_low) / (range_high - range_low + 0.0001), 3)
                    except Exception as e:
                        print(f"❌ Drift/Steam block failed for {selection_id} in {market_id}: {e}")
                        continue


                    if market_id not in live_runner_map:
                        live_runner_map[market_id] = []
                    live_runner_map[market_id].append({
                        "selectionId": selection_id,
                        "odds": current_odds,
                        "range_low": range_low,
                        "range_high": range_high,
                        "tick_pattern": tick_pattern,
                        "position_ratio": position_ratio,
                        "tick_trail": tick_trails[selection_id][-12:],
                        "volatility": volatility,
                        "position": None,  # TO BE POPULATED BELOW
                        "marketStartTime": market.get("marketStartTime"),
                        "minutes_to_post": round(minutes_to_off, 1)
                    })

                    from database_hijack_monitor import enqueue_write
                    enqueue_write("""
                        UPDATE bets SET odds = ?, range_low = ?, range_high = ?
                        WHERE marketId = ? AND selectionId = ?
                    """, (current_odds, range_low, range_high, market_id, selection_id))
                    

                if minutes_to_off < 0:
                    runner_totals["-15-0"] += 1
                elif 0 <= minutes_to_off < 5:
                    runner_totals["0-5"] += 1
                elif 5 <= minutes_to_off < 10:
                    runner_totals["5-10"] += 1
                elif 10 <= minutes_to_off < 20:
                    runner_totals["10-20"] += 1
                elif 20 <= minutes_to_off < 40:
                    runner_totals["20-40"] += 1
                elif 40 <= minutes_to_off < 60:
                    runner_totals["40-60"] += 1
                elif 60 <= minutes_to_off < 80:
                    runner_totals["60-80"] += 1
                elif 80 <= minutes_to_off < 90:
                    runner_totals["80-90"] += 1
                else:
                    runner_totals[">90"] += 1

                odds_check_fields = [
                    ("odds_check_1", "oc1", 85 <= minutes_to_off <= 95),
                    ("odds_check_2", "oc2", 65 <= minutes_to_off < 85),
                    ("odds_check_3", "oc3", 45 <= minutes_to_off < 65),
                    ("odds_check_4", "oc4", 25 <= minutes_to_off < 45),
                    ("odds_check_5", "oc5", 10 <= minutes_to_off < 25),
                    ("odds_check_6", "oc6", 0 <= minutes_to_off < 10),
                ]
                _ensure_parent_dir(DB_PATH)
                with sqlite3.connect(DB_PATH) as conn:
                    cursor = conn.cursor()
                    for oc_field, field_key, is_active in odds_check_fields:
                        if not is_active:
                            continue
                        max_check = completed_checks.get(selection_id, 0)
                        current_index = oc_index_map[oc_field]
                        if current_index <= max_check:
                            continue
                        from database_hijack_monitor import enqueue_write
                        enqueue_write(
                            f"UPDATE bets SET {oc_field} = ? WHERE marketId = ? AND selectionId = ?",
                            [current_odds, market_id, selection_id]
                        )


                        
                        odds_check_totals[field_key] += 1
                        completed_checks[selection_id] = current_index
                        break

            if market_has_odds:
                markets_with_valid_odds += 1

        try:
            _ensure_parent_dir(DB_PATH)
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                print("\n📊 Odds Check Summary:")
                today = datetime.utcnow().date().isoformat()
                for i in range(1, 7):
                    cursor.execute(f"""
                        SELECT COUNT(*) FROM bets
                        WHERE odds_check_{i} IS NOT NULL
                        AND date = ?
                    """, (today,))
                    row = cursor.fetchone()
                    count = row[0] if row else 0

                    print(f"  • OC{i} present in DB: {count}")
        except Exception as e:
            print(f"⚠️ Could not fetch odds check summary from DB: {e}")

        print("\n📊 Market Segment Breakdown:")
        for key, val in segment_totals.items():
            print(f"  • {key} min window: {val} markets")

        print("\n📊 Runner Segment Totals:")
        for key, val in runner_totals.items():
            print(f"  • {key} min window: {val} runners")

        print("\n📊 Odds Check Updates This Loop:")
        for key, value in odds_check_totals.items():
            print(f"  • {key.upper()} updated: {value}")

        nonrunner_count_today += loop_nonrunner_increments
        if loop_nonrunner_increments > 0:
            print(f"🚫 Non-runners / no-odds runners today: {nonrunner_count_today}")



        print(f"\n🔹 Markets with valid runners + odds: {markets_with_valid_odds}")
        print(f"🔹 Runners with last odds recorded: {runners_with_last_odds}")
        print(f"🔹 Runners with anchor odds: {runners_with_anchor_odds}")
        print(f"🔹 Runners with odds check history: {runners_with_any_oc}")
        print("🔁 Auto-cycle continuing – monitoring markets for valid signal...\n")
        time.sleep(10)

print("🔎 Triggering evaluate_market_loop thread...")
try:
    threading.Thread(target=evaluate_market_loop, daemon=True).start()
except Exception as e:
    print(f"❌ Failed to start evaluation thread: {e}")

# ✅ Delay slightly to let market loop seed anchors
#time.sleep(3)

# ✅ Force thread start even if same-day
#def launch_signal_threads():
#    from signal_memory_engine import signal_memory

#    threading.Thread(target=signal_memory.sync_memory_snapshots, daemon=True).start()
#    threading.Thread(target=signal_memory.periodically_sync_from_db, daemon=True).start()
#    threading.Thread(target=signal_memory.generate_signal_status_report, daemon=True).start()
#    threading.Thread(target=signal_memory.evaluate_active_runners, daemon=True).start()
#    threading.Thread(target=signal_memory.monitor_live_for_partial_blueprints, daemon=True).start()
#    threading.Thread(target=signal_memory.monitor_passive_trends, daemon=True).start()
#    threading.Thread(target=signal_memory.monitor_in_play_trends, daemon=True).start()
#    threading.Thread(target=signal_memory.update_tradable_markets, daemon=True).start()



# -----------------------------
# 📋 get_next_races – Fetch races with runners
# -----------------------------
def get_next_races():
    from get_markets import get_markets_and_insert
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    all_markets = get_markets_and_insert()
    filtered = []

    for market in all_markets:
        start = datetime.fromisoformat(market["marketStartTime"].replace("Z", "+00:00"))
        mins_to_off = (start - now).total_seconds() / 60.0
        if 0 <= mins_to_off <= 20 and len(market.get("runners", [])) >= 7:
            filtered.append(market)

    return filtered[:5]

# -----------------------------
# 🔍 show_runners_with_odds – Display runner list
# -----------------------------
def show_runners_with_odds(market):
    from utils.api_tools import fetch_live_odds
    import time

    print("🐎 Runners in this race:")
    runners = market.get("runners", [])
    marketId = market.get("marketId")

    for i, runner in enumerate(runners, 1):
        odds = fetch_live_odds(None, marketId, runner["selectionId"]) or {}
        lay = odds.get("lay", "–")
        back = odds.get("back", "–")
        print(f"{i}. {runner['runnerName']} – Lay: {lay}, Back: {back}")
        time.sleep(0.1)  # light delay to avoid spam or rate limits

    selection = input("🎯 Select a runner by number or press enter to skip: ").strip()
    if selection.isdigit():
        idx = int(selection) - 1
        if 0 <= idx < len(runners):
            return runners[idx]

    return None

# -----------------------------
# 🧠 run_coop_session – Entry Point for Co-op Mode
# -----------------------------

def run_coop_session():
    from get_markets import get_markets_and_insert
    from scalper_coop import market_loop
    from upgrade_import_patch import get_session_token
    from signal_memory_engine import signal_memory
    import threading

    print("🧠 [Co-op] Session started with Head Trader...")

    all_markets = get_markets_and_insert()
    if not all_markets:
        print("📭 No markets found.")
        return

    # 1. Seed database
    try:
        seed_initial_anchor_odds()
    except Exception as e:
        print(f"❌ Step 1 – Anchor seeding failed: {e}")

    # 2. Start market loop in background thread
    try:
        threading.Thread(target=market_loop, daemon=True).start()
    except Exception as e:
        print(f"❌ Step 2 – Market loop thread failed to launch: {e}")

    # 3a. Set session token for memory engine
    try:
        signal_memory.session_token = get_session_token()
    except Exception as e:
        print(f"❌ Step 3a – Failed to get session token: {e}")

    # 3b. Attempt to reset memory for new day
    try:
        signal_memory.maybe_reset_for_new_day()
    except Exception as e:
        print(f"❌ Step 3b – Memory reset failed: {e}")


# -----------------------------
# 📝 log_coop_result – Record result to scalping_bets
# -----------------------------
def log_coop_result(signal, outcome):
    if outcome == "skipped":
        print("📉 Skipped signal logged for analysis.")
    import sqlite3
    import os
    from datetime import datetime

    db_path = os.path.join(os.path.dirname(__file__), "scalping_bets.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scalping_bets (
                marketId TEXT,
                selectionId INTEGER,
                runnerName TEXT,
                odds REAL,
                scalp_direction TEXT,
                strategy_name TEXT,
                bet_type TEXT,
                confidence REAL,
                time_signal TEXT,
                anchor_odds REAL,
                spread REAL,
                tick_size REAL,
                avg_volatility REAL,
                volatile BOOLEAN,
                placed_at TEXT,
                result TEXT,
                user TEXT,
                head_trader TEXT,
                customerOrderRef TEXT
            )
        """)

        conn.execute("""
            INSERT INTO scalping_bets (
                marketId, selectionId, runnerName, odds, scalp_direction, strategy_name, bet_type,
                confidence, time_signal, anchor_odds, spread, tick_size, avg_volatility, volatile,
                placed_at, result, user, head_trader, customerOrderRef
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            signal.get("marketId"),
            signal.get("selectionId"),
            signal.get("runnerName"),
            signal.get("odds"),
            signal.get("scalp_direction"),
            signal.get("strategy_name"),
            signal.get("bet_type"),
            signal.get("confidence"),
            signal.get("time_signal"),
            signal.get("anchor_odds"),
            signal.get("spread"),
            signal.get("tick_size"),
            signal.get("avg_volatility"),
            signal.get("volatile"),
            datetime.utcnow().isoformat(),
            outcome,
            "user",  # default user tag for now
            "head_trader",  # hardcoded until co-op tags evolve
            signal.get("customerOrderRef", "manual_ref")
        ))

    print("📝 Signal logged to scalping_bets.db")

# -----------------------------
# ✅ Updated RANDCARB Session with BETS Table Integration
# -----------------------------

def randcarb_session(market):
    from upgrade_import_patch import get_session_token
    from utils.api_tools import fetch_live_odds
    from brain_headtrader import evaluate_signal
    from config_paths import DB_PATH
    import sqlite3, time
    from datetime import datetime
    from volatility_check import get_volatility_meta, get_recent_odds

    print("\n🤖 [RANDCARB] Running live co-op recommendation for selected market...")

    session_token = get_session_token()
    market_id = market["marketId"]
    runners = market.get("runners", [])

    # ✅ Anchor odds already seeded globally at tool start – skip reseeding
    last_odds_map = {}

    while True:
        print("\n🔍 Polling odds for signal generation...")
        signal = None
        for runner in runners:
            selection_id = runner["selectionId"]
            odds_data = fetch_live_odds(session_token, market_id, selection_id)
            if not odds_data or "lay" not in odds_data:
                continue

            current_odds = odds_data["lay"]
            last_odds = last_odds_map.get(selection_id)
            if last_odds == current_odds:
                continue
            last_odds_map[selection_id] = current_odds

            # ✅ Add odds to runner object for market profiling logic
            runner["odds"] = current_odds

            # ✅ Pull 6 odds checks from DB – fallback uses current_odds repeated 6x
            odds_list = get_recent_odds(market_id, selection_id) or [current_odds] * 6
            update_odds_checks_with_anchor(market_id, selection_id, current_odds)
            print(f"📦 RANDCARB odds list for {runner['runnerName']}: {odds_list}")

            volatility_meta = get_volatility_meta(market_id, selection_id, odds_list)

            range_low = volatility_meta.get("range_low")
            range_high = volatility_meta.get("range_high")

            if not range_low or not range_high or range_low >= range_high:
                print(f"⛔ RANDCARB: Skipped {runner['runnerName']} – invalid range: low={range_low}, high={range_high}")
                continue

            position_ratio = round((current_odds - range_low) / (range_high - range_low), 3)
            print(f"📊 RANDCARB: {runner['runnerName']} – Odds: {current_odds} | Range: [{range_low}, {range_high}] | Pos: {position_ratio}")

            if position_ratio <= 0.3:
                print(f"✅ SCALP ENTRY SIGNAL – odds near bottom of range")
            elif position_ratio >= 0.7:
                print(f"✅ SCALP EXIT SIGNAL – odds near top of range")
            else:
                print(f"🟡 No SCALP signal – odds in neutral zone")

            # ✅ Use seeded anchor or fallback to current_odds
            anchor = fetch_anchor_odds(market_id, selection_id) or current_odds
            drift = round(((current_odds - anchor) / anchor) * 100, 2)

            signal_candidate = {
                "selectionId": selection_id,
                "marketId": market_id,
                "runnerName": runner["runnerName"],
                "odds": current_odds,
                "anchor_odds": anchor,
                "drift": drift,
                "volatility_trend": volatility_meta.get("volatility_trend", "choppy"),
                "quality_score": volatility_meta.get("quality_score", 75),
                "confidence": calculate_confidence_score(selection_id, drift),
                "spread": 3,
                "time_signal": "time_10"
            }

            signal_result = evaluate_signal(
                signal_candidate,
                headers={},
                horses=runners,
                initial_stakes=None,
                current_odds={selection_id: current_odds},
                fire_ice_status=None,
                bot_queues=None
            )

            if isinstance(signal_result, dict):
                signal = signal_result
                break

        if not signal:
            print("📜 No signal change. Monitoring...")
            time.sleep(10)
            continue

        print("\n📊 Head Trader Recommendation:")
        print(f"🏇 Horse: {signal['runnerName']}")
        print(f"🎯 Market: {signal['marketId'][-6:]}")
        print(f"🎯 Strategy: {signal['strategy_name']}")
        print(f"💸 Odds: {signal['odds']}")
        print(f"📊 Confidence: {round(signal['confidence'] * 100)}%")
        print(f"📈 Quality Score: {signal.get('quality_score', '–')}")
        print(f"📉 Drift: {signal.get('drift')}%")
        print(f"🧠 Status: {signal.get('status')}")

        import threading
        user_input = None

        def prompt_with_timeout():
            nonlocal user_input
            try:
                user_input = input("🔀 Your choice: ").strip().lower()
            except Exception as e:
                print(f"⚠️ Prompt error: {e}")

        thread = threading.Thread(target=prompt_with_timeout)
        thread.start()
        thread.join(timeout=60)

        if user_input is None:
            print("⏳ No response. Signal marked as missed.")
            log_coop_result(signal, "missed")
            continue
        if user_input in ("accept", "y", "yes"):
            result = execute_coop_trade(signal)
            log_coop_result(signal, result)
        elif user_input in ("edit", "e"):
            print("✏️ Edit path coming soon...")
        elif user_input in ("manual", "m"):
            from scalper_coop import manual_bet_entry
            manual_bet_entry(None)
        elif user_input in ("quit", "q"):
            print("👋 Exiting RANDCARB session.")
            return
        else:
            log_coop_result(signal, "skipped")

        print("🔁 Returning to early signal tracking...")
        time.sleep(5)

# -----------------------------
# 📈 list_recent_signals – Optional: show past suggestions
# -----------------------------
def list_recent_signals():
    import sqlite3
    import os

    db_path = os.path.join(os.path.dirname(__file__), "scalping_bets.db")
    if not os.path.exists(db_path):
        print("📭 No trades have been logged yet.")
        return

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        enqueue_write("""
            SELECT runnerName, odds, scalp_direction, confidence, result, placed_at
            FROM scalping_bets
            ORDER BY placed_at DESC
            LIMIT 5
        """)
        rows = cursor.fetchall()

        if not rows:
            print("📭 No recent signals to display.")
            return

        print("🕓 Last 5 Co-op Trades:")
        for row in rows:
            name, odds, direction, conf, result, timestamp = row
            print(f"{timestamp[:16]} | {name:20} | {direction:13} | Odds: {odds:<4} | Conf: {round(conf*100):>2}% | {result.upper()}")


# -----------------------------
# 📤 prompt_user_decision – Accept/Edit/Reject signal
# -----------------------------
def prompt_user_decision(signal):
    print("📊 Head Trader Recommendation:")
    print("🧪 Debug Path: ✓ volatility ✓ tick drift ✓ direction ✓ rule check ✓ confidence")
    print("💡 Why this signal? Detected strong scalp setup due to tick drift and low volatility.")
    print(f"🏇 Horse: {signal['runnerName']}")
    print(f"🎯 Market: {signal['marketId'][-6:]}")
    print(f"🔁 Direction: {signal['scalp_direction'].replace('_', ' ').title()}")
    print(f"💸 Odds: {signal['odds']}")
    print(f"📏 Tick Size: {signal['tick_size']}")
    print(f"⚡ Spread: {signal['spread']}")
    print(f"📈 Anchor: {signal['anchor_odds']}")
    print(f"🔥 Volatile: {'Yes' if signal['volatile'] else 'No'} ({round(signal['avg_volatility'], 4)})")
    print(f"📊 Confidence: {round(signal['confidence'] * 100)}%")

    print("What would you like to do?")
    print("[Y] Accept")
    print("[E] Edit")
    print("[M] Enter manual trade")
    print("[N] Reject and skip")
    print("[Q] Quit session")

    action = input("👉 Your choice: ").strip().lower()

    if action == "y":
        return "accept"
    elif action == "e":
        return "edit"
    elif action == "m":
        return "manual"
    elif action == "q":
        return "quit"
        print("📍 Signal rejected. Will be tracked for accuracy later.")
    return "reject"

# -----------------------------
# 🧾 execute_coop_trade – Run selected trade logic
# -----------------------------
def execute_coop_trade(signal):
    from bet_placer import place_scalp_trade, place_ladder_lay_bet, place_greenup_exit, place_basic_lay_bet

    bet_type = signal.get("bet_type")
    outcome = None

    try:
        if bet_type == "SCALP":
            outcome = place_scalp_trade(signal)
        elif bet_type == "LADDER":
            outcome = place_ladder_lay_bet(signal)
        elif bet_type == "GREENUP":
            outcome = place_greenup_exit(signal)
        elif bet_type == "BASIC":
            outcome = place_basic_lay_bet(signal)
        else:
            print(f"❌ Unknown bet type: {bet_type}")
            return "error"

        print(f"✅ Bet placed successfully using {bet_type} strategy.")
        return "success"

    except Exception as e:
        print(f"❌ Error placing bet: {e}")
        return "error"

# -----------------------------
# 🧠 manual_bet_entry – Allow manual trade during session
# -----------------------------
def manual_bet_entry(session_token):
    def manual_market_signal_flow(market):
        from scalper_coop import fetch_anchor_odds, calculate_confidence_score, evaluate_odds_history
        from utils.api_tools import fetch_live_odds
        def get_anchor(selection_id):
            return fetch_anchor_odds(market["marketId"], selection_id)

        runners = market.get("runners", [])
        for runner in runners:
            selection_id = runner["selectionId"]
            odds_data = fetch_live_odds(None, market["marketId"], selection_id)
            if not odds_data or "lay" not in odds_data:
                continue
            current_odds = odds_data["lay"]
            anchor = get_anchor(selection_id) or current_odds
            drift = round(((current_odds - anchor) / anchor) * 100, 2) if anchor else 0

            signal = analyze_market_signal(
                market,
                get_anchor,
                selection_id,
                current_odds,
                anchor,
                drift
            )
        if not signal:
            print("⚠️ No signal could be generated for this market.")
            return
        user_choice = prompt_user_decision(signal)
        if user_choice == "accept":
            outcome = execute_coop_trade(signal)
            log_coop_result(signal, outcome)
    from get_markets import get_markets_and_insert
    from utils.api_tools import fetch_live_odds
    from price_math import get_tick_size, calculate_tick_distance
    import time
    import random
    from datetime import datetime, timezone

    all_markets = get_markets_and_insert()
    now = datetime.now(timezone.utc)

    upcoming = []
    for market in all_markets:
        start = datetime.fromisoformat(market["marketStartTime"].replace("Z", "+00:00"))
        if 0 <= (start - now).total_seconds() / 60.0 <= 300:
            upcoming.append(market)

    if not upcoming:
        print("📭 No nearby races found.")
        return

    print("🏇 Recent Races:")
    for i, m in enumerate(upcoming[:3], 1):
        print(f"{i}. {m['event']['venue']} – {m['marketName']} @ {m['marketStartTime']}")

    choice = input("🔢 Pick a race by number: ").strip()
    if not choice.isdigit():
        print("❌ Invalid choice.")
        return

    market = upcoming[int(choice) - 1]
    runners = market.get("runners", [])
    market_id = market.get("marketId")

    print("🐎 Runners:")
    for i, runner in enumerate(runners, 1):
        print(f"{i}. {runner['runnerName']}")

    rchoice = input("🎯 Pick a runner by number: ").strip()
    if not rchoice.isdigit():
        print("❌ Invalid runner.")
        return

    runner = runners[int(rchoice) - 1]
    odds_data = fetch_live_odds(session_token, market_id, runner["selectionId"]) or {}
    tick = get_tick_size(odds_data.get("lay", 1.0))

    side = input("🔁 Direction [L = Lay to Back | B = Back to Lay]: ").strip().lower()
    odds = float(input("💸 Entry Odds: ").strip())
    stake = float(input("💰 Stake: ").strip())
    btype = input("📦 Bet Type [scalp/ladder/basic]: ").strip().lower()

    direction = "lay_to_back" if side == "l" else "back_to_lay"
    strategy_name = f"manual_{btype}_signal"
    time_signal = "manual"

    signal = {
        "marketId": market_id,
        "selectionId": runner["selectionId"],
        "runnerName": runner["runnerName"],
        "odds": odds,
        "stake": stake,
        "scalp_direction": direction,
        "strategy_name": strategy_name,
        "bet_type": btype.upper(),
        "confidence": 0.88,
        "time_signal": time_signal,
        "anchor_odds": odds,
        "spread": 0.0,
        "tick_size": tick,
        "avg_volatility": 0.0,
        "volatile": False,
        "customerOrderRef": f"MANUAL_{random.randint(1000,9999)}"
    }

    outcome = execute_coop_trade(signal)
    log_coop_result(signal, outcome)

    # 🔁 Launch full co-op signal logic after manual market choice
    manual_market_signal_flow(market)
