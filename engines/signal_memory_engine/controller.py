# --- Core Reader Access ---
from signal_memory_engine.reader_access import (
    get_active_runner_odds,
    get_market_start_time,
)

# --- RAM Readers / Writers ---
from signal_memory_engine.ram_reader import (
    get_static_snapshot as ram_get_static_snapshot,
    get_bets_row,
    build_final_runner_snapshot,
    load_oc_snapshots_from_db,
)
from signal_memory_engine.ram_writer import (
    write_oc_snapshot_to_db,
    persist_oc_snapshots_to_db,
    save_snapshot_to_ram,      # ✅ keep here, remove db.signal_memory_db_writer version
    save_snapshot_to_bets,
)

# --- Tracking / Matching / Ladder Logic ---
from signal_memory_engine.track_unmatched_signal import track_unmatched_signal
from signal_memory_engine.ladder import (
    _attempt_next_ladder_rung,
    place_ladder_bet,
    should_continue_ladder,
    detect_trend_reversal,
)

# --- Playbook Management ---
from signal_memory_engine.playbook import (
    start_playbook,
    finalise_playbook,
    save_playbook_to_db,
    record_matched_bet,
)

# --- Analysis / Signal Evaluation ---
from signal_memory_engine.analyze_runner import analyse_runner
from signal_memory_engine.evaluate_signal import evaluate_signal_for_runner
from signal_memory_engine.blueprint_partial import (
    match_blueprint_partial,
    attempt_partial_blueprint_match,
)
from signal_memory_engine.signal_blueprint_match import (
    detect_blueprint_match,
    predict_next_segment,
)

# --- Monitoring Threads ---
from signal_memory_engine.monitor_passive_trends import monitor_passive_trends
from signal_memory_engine.monitor_in_play_trends import monitor_in_play_trends
from signal_memory_engine.monitor_pre_off_trends import monitor_pre_off_trends
from signal_memory_engine.monitor_live_for_partial_blueprints import monitor_live_for_partial_blueprints

# --- Session Management ---
from signal_memory_engine.session import (
    maybe_reset_for_new_day,
    start_new_session,
    setup_daily_logger,
)

# --- PnL / Reporting / Health ---
from signal_memory_engine.resolve_runner_pnl import resolve_runner_pnl
from signal_memory_engine.pnl import (
    track_live_pattern_pnl,
    load_pattern_pnl_from_playbooks,
    refresh_pattern_pnl_from_api,
)
from signal_memory_engine.reporting import generate_signal_status_report
from signal_memory_engine.health import assess_signal_health

# --- Odds / Enrichment / Exit / Risk ---
from signal_memory_engine.flush_inplay_odds import flush_inplay_odds_to_db
from signal_memory_engine.enrich import enrich
from signal_memory_engine.exit_early import should_force_exit_early
from signal_memory_engine.afford import can_afford_bet
from signal_memory_engine.faults import record_trade_fault

# --- Standard Lib ---
from collections import defaultdict
from datetime import datetime, date
import threading
import time
import sys
import json


def load_known_blueprint_patterns():
    today_file = f"blueprint_signals_{date.today().isoformat()}.json"
    try:
        with open(today_file, "r") as f:
            data = json.load(f)
        return data
    except Exception as e:
        print(f"⚠️ Failed to load today's blueprint file ({today_file}): {e}")
        return {}

def load_yesterday_blueprint_patterns():
    from datetime import date, timedelta
    return _load_blueprint_file(date.today() - timedelta(days=1))
def _load_blueprint_file(d):
    filename = f"blueprint_signals_{d.isoformat()}.json"
    try:
        with open(filename, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ Failed to load blueprint file ({filename}): {e}")
        return {}


class SignalMemoryEngine:
    def __init__(self):

        self.debug_signal_counter = 0
        self._track_signal_counter = defaultdict(int)
        self._last_track_reported = 0
        self.live_inplay_odds = defaultdict(lambda: defaultdict(list))
        self.live_markets = defaultdict(list)
        self.active_tradable_markets = []
        self.active_blueprints_today = set()
        self.unmatched_lock = threading.Lock()
        self.unmatched_signals = {}
        self.active_ladders = {}
        self.trade_log = []
        self.clean_exit_log = []
        self.live_playbooks = defaultdict(dict)
        self.live_pattern_pnl = defaultdict(lambda: {"trades": 0, "net_pnl": 0.0})
        self.open_bets = defaultdict(list)
        self.session_active = True
        self.session_date = datetime.utcnow().date()
        self.starting_balance = 500.0
        self.current_balance = 500.0
        self.current_stake = 10.0
        self.halt_triggered = False
        self.passive_tick_tracker = defaultdict(lambda: defaultdict(list))
        self.in_play_tick_tracker = defaultdict(lambda: defaultdict(list))
        self._debug_logger = None
        self._debug_log_path = None
        self.counter = {
            "scalps_fired": 0,
            "exploratory_fired": 0,
            "blueprint_fired": 0,
            "playbook_complete": 0
        }
        self.signal_eval_per_market = defaultdict(int)
        self._last_signal_eval_total = 0
      
        self.KNOWN_BLUEPRINT_PATTERNS = load_known_blueprint_patterns()

        from signal_memory_engine.analyze_runner import analyse_runner
        from signal_memory_engine.evaluate_signal import evaluate_signal_for_runner
        from signal_memory_engine.blueprint_partial import attempt_partial_blueprint_match
        from signal_memory_engine.ram_reader import build_final_runner_snapshot

        self.build_final_runner_snapshot = build_final_runner_snapshot  # ⚠️ NO __get__(self)

        # ✅ Bind instance methods that require access to self
        self.analyse_runner = analyse_runner.__get__(self)
        self.evaluate_signal_for_runner = evaluate_signal_for_runner.__get__(self)
        self.attempt_partial_blueprint_match = attempt_partial_blueprint_match.__get__(self)
        self.predict_next_segment = self.predict_next_segment  # ✅ This line is actually optional
        from signal_memory_engine.track_unmatched_signal import track_unmatched_signal
        self.track_unmatched_signal = track_unmatched_signal.__get__(self)
        from signal_memory_engine.afford import get_adaptive_stake
        self.get_adaptive_stake = get_adaptive_stake.__get__(self)

        from signal_memory_engine.matching_engine import (
            evaluate_matching_opportunity,
            evaluate_exploratory_scalp,
            evaluate_partial_match_scalp,
            evaluate_full_blueprint_match,
            create_signal_dict,
       
        )

        self.evaluate_matching_opportunity = evaluate_matching_opportunity.__get__(self)
        self.evaluate_exploratory_scalp = evaluate_exploratory_scalp.__get__(self)
        self.evaluate_partial_match_scalp = evaluate_partial_match_scalp.__get__(self)
        self.evaluate_full_blueprint_match = evaluate_full_blueprint_match.__get__(self)
        self.create_signal_dict = create_signal_dict.__get__(self)
     





    def scan_all_runners_for_scalps(self):
        import time
        from signal_memory_engine.matching_engine import evaluate_matching_opportunity

        from signal_memory_engine.bet_router import fire_initial_scalp
        import uuid

        print("📡 Starting fallback scalp scanner...")

        while True:
            try:
                for market_id, runners in self.live_markets.items():
                    for selection_id in runners:
                        story = self.get_runner_story(market_id, selection_id)
                        chapters = story.get("chapters", [])

                        if len(chapters) < 2:
                            continue  # Need at least 2 chapters to match against blueprints

                        signal = self.evaluate_matching_opportunity(market_id, selection_id)
                        if not signal:
                            continue

                        stake = self.get_adaptive_stake()
                        signal["stake"] = stake
                        order_ref = f"auto_{uuid.uuid4().hex[:6]}"
                        response = fire_initial_scalp(signal, order_ref)

                        if response:
                            print(f"🚀 Auto scalp fired for {selection_id} at {signal['odds']}")

            except Exception as e:
                print(f"⚠️ Error in scalp scanner: {e}")

            time.sleep(60)

       


    def calculate_minutes_to_post(self, market_start_time_iso: str) -> float:
        try:
            market_time_obj = datetime.fromisoformat(market_start_time_iso)
            diff_minutes = (market_time_obj - datetime.utcnow()).total_seconds() / 60
            return round(diff_minutes, 1)
        except Exception:
            return 999

    def repair_oc_snapshots(self):
        import sqlite3
        import json
        import time
        from datetime import datetime
        from config_paths import DB_PATH
        from signal_memory_engine.ram_writer import save_snapshot_to_bets, persist_oc_snapshots_to_db

        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT marketId, selectionId, anchor_odd, odds_check_1, odds_check_2, odds_check_3,
                       odds_check_4, odds_check_5, odds_check_6
                FROM bets
            """)
            rows = cursor.fetchall()

            for row in rows:
                market_id, selection_id, anchor, *checks = row

                oc_snapshots = {}
                snapshot = {
                    "marketId": market_id,
                    "selectionId": selection_id,
                    "oc_snapshots": oc_snapshots
                }

                # Move anchor to OC0
                if anchor is not None:
                    oc_snapshots["OC0"] = {"odds": anchor}
                    oc_snapshots["OC0_band"] = [anchor]

                    chapter = {
                        "marketId": market_id,
                        "selectionId": selection_id,
                        "oc_label": "OC0",
                        "oc_band": [anchor],
                        "odds": anchor,
                        "range_low": anchor,
                        "range_high": anchor,
                        "tick_pattern": "flat",
                        "volatility": 0.0,
                        "position_ratio": 0.5,
                        "direction_bias": "flat",
                        "minutes_to_post": 999,
                        "timestamp": time.time(),
                        "confidence": 0.0,
                        "blueprint_match": None
                    }

                    persist_oc_snapshots_to_db(market_id, selection_id, chapter)

                # Move odds checks to OC1–OC6
                from signal_memory_engine.ram_writer import persist_oc_label_and_band

                # Move anchor to OC0
                if anchor is not None:
                    persist_oc_label_and_band(market_id, selection_id, "OC0", anchor)

                # Move odds checks to OC1–OC6
                for i, value in enumerate(checks, start=1):
                    if value is not None:
                        label = f"OC{i}"
                        persist_oc_label_and_band(market_id, selection_id, label, value)


            conn.close()
            print("✅ OC Snapshot Repair completed successfully and saved to bets + runner_ram_snapshots.")

        except Exception as e:
            print(f"❌ OC Snapshot Repair failed: {e}")

    def get_runner_story(self, market_id, selection_id):
        import sqlite3
        import json
        from config_paths import DB_PATH

        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT snapshot FROM runner_ram_snapshots
                WHERE marketId = ? AND selectionId = ?
            """, (market_id, selection_id))
            rows = cursor.fetchall()
            conn.close()

            chapters = []
            for row in rows:
                try:
                    snap = json.loads(row[0])
                    chapters.append(snap)
                except:
                    continue

            def oc_sort_key(chapter):
                label = chapter.get("oc_label", "")
                try:
                    if label.startswith("OC"):
                        return int(label[2:])
                    return 999
                except:
                    return 999

            chapters = sorted(chapters, key=oc_sort_key)
            story_sequence = [c.get("oc_label") for c in chapters if c.get("oc_label")]

            return {
                "story_sequence": story_sequence,
                "chapters": chapters
            }

        except Exception as e:
            print(f"❌ Failed to load runner story: {e}")
            return {"story_sequence": [], "chapters": []}

    def start_story_builder_loop(self):
        import json
        import time
        from signal_memory_engine.ram_writer import build_and_save_runner_chapter
        from engines.database_hijack_monitor import enqueue_write
        from config_paths import DB_PATH

        print("📖 StoryBuilder thread launched...")
        seen_chapters = set()

        def _load_and_process():
            import sqlite3
            try:
                with sqlite3.connect(DB_PATH) as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT market_id, selection_id, snapshot
                        FROM runner_ram_snapshots
                        WHERE snapshot IS NOT NULL
                    """)
                    rows = cursor.fetchall()

                for market_id, selection_id, raw in rows:
                    try:
                        snap = json.loads(raw)
                    except:
                        continue

                    oc_snaps = snap.get("oc_snapshots", {})
                    for label in list(oc_snaps.keys()):
                        if not label.startswith("OC") or not label.endswith("_band"):
                            continue
                        oc_label = label.replace("_band", "")
                        key = (market_id, selection_id, oc_label)

                        if key in seen_chapters:
                            continue

                        seen_chapters.add(key)

                        # ✅ DB-safe call wrapped via enqueue_write
                        enqueue_write(
                            lambda m=market_id, s=selection_id, l=oc_label, snap=snap:
                                build_and_save_runner_chapter(m, s, l, snap)
                        )

            except Exception as e:
                print(f"❌ StoryBuilder loop crashed: {e}")

        while True:
            _load_and_process()
            time.sleep(10)

    def evaluate_and_execute_scalp(self, market_id, selection_id):
        import uuid
        import sqlite3
        import sys
        import os
        sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
        from daily_config import fetch_available_budget
      
        from signal_memory_engine.analyze_runner import analyze_runner
        from signal_memory_engine.blueprint_partial import attempt_partial_blueprint_match
        from signal_memory_engine.signal_blueprint_match import detect_blueprint_match
        from signal_memory_engine.bet_router import fire_initial_scalp
        from price_math import calculate_tick_distance

        try:
            # Step 1: Load current runner story from RAM snapshots
            story = self.get_runner_story(market_id, selection_id)
            chapters = story.get("chapters", [])
            oc_labels = story.get("story_sequence", [])
            if not chapters:
                return

            # Step 2: Determine match type + confidence
            signal = self.evaluate_matching_opportunity(market_id, selection_id)
            if not signal:
                return

            match_type = signal.get("signal_type", "exploratory")
            confidence = signal.get("confidence", 0.0)
            blueprint_match = signal.get("blueprint_match")


            # Step 3: Adjust stake dynamically
            stake = self.get_adaptive_stake()

            # Step 4: Estimate proposed liability
            odds = signal.get("odds")
            if not isinstance(odds, (int, float)) or not isinstance(stake, (int, float)):
                return
            proposed_liability = max(0, (odds - 1) * stake)

            # Step 5: Check market-level liability headroom
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT odds, stake FROM bets
                WHERE marketId = ? AND status = 'unmatched' AND side = 'LAY'
            """, (market_id,))
            existing = cursor.fetchall()
            conn.close()

            current_liabilities = [(o - 1) * s for o, s in existing if o and s]
            current_market_liability = max(current_liabilities) if current_liabilities else 0
            added_liability = max(proposed_liability - current_market_liability, 0)

            # Step 6: Budget check
            available_budget = fetch_available_budget()
            if added_liability > available_budget:
                print(f"[{oc_labels[-1]}] 🔒 Budget block: £{available_budget:.2f} < needed £{added_liability:.2f}")
                return

            # Step 7: Confidence-based pacing
            if confidence < 0.52:
                print(f"[{oc_labels[-1]}] ❄️ Too early: confidence={confidence:.2f}")
                return

            # Step 8: Trigger scalp
            order_ref = f"{match_type[:1]}_{uuid.uuid4().hex[:6]}"
            signal["stake"] = stake
            response = fire_initial_scalp(signal, order_ref)

            if response:
                print(f"[{oc_labels[-1]}] ✅ {match_type.capitalize()} scalp placed at £{stake} (conf={confidence:.2f})")
            else:
                print(f"[{oc_labels[-1]}] ❌ Scalp failed to place (conf={confidence:.2f})")

        except Exception as e:
            print(f"❌ Error in evaluate_and_execute_scalp: {e}")



    def record_oc0_and_band(self):
        import sqlite3
        import time
        import json
        from datetime import datetime
        from config_paths import DB_PATH
        from signal_memory_engine.ram_writer import (
            persist_oc_label_and_band,
            save_chapter_to_runner_story
        )
        from signal_memory_engine.reader_access import (
            get_active_runner_odds,
            get_market_start_time
        )
        from price_math import calculate_tick_distance

        print("🟡 OC0–OC20 Band Tracker launched...")

        active_bands = {}

        while True:
            try:
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT marketId, selectionId, anchor_odd, range_low, range_high,
                           position_ratio, volatility
                    FROM bets
                """)
                rows = cursor.fetchall()
                now = time.time()

                for row in rows:
                    market_id, selection_id, anchor, range_low, range_high, pos_ratio, vol = row
                    key = (market_id, selection_id)

                    market_start = get_market_start_time(market_id)
                    if not market_start:
                        continue

                    minutes_to_post = self.calculate_minutes_to_post(market_start)
                    oc_label = self.get_current_oc_label(minutes_to_post)
                    if not oc_label:
                        continue

                    current_odds = get_active_runner_odds(market_id, selection_id)
                    if not isinstance(current_odds, (int, float)):
                        continue

                    # ✅ Always update OCX and OCX_band columns
                    persist_oc_label_and_band(market_id, selection_id, oc_label, current_odds)

                    # === Band tracking ===
                    if key not in active_bands:
                        active_bands[key] = {"label": oc_label, "band": [current_odds]}
                        continue

                    prev = active_bands[key]
                    prev_label = prev["label"]

                    if oc_label == prev_label:
                        prev["band"].append(current_odds)
                        continue

                    # ✅ Finalize chapter for completed OCX band
                    finalized_band = prev["band"]
                    entry = finalized_band[0]
                    exit = finalized_band[-1]
                    tick_pattern = self.classify_tick_pattern(entry, exit, finalized_band)

                    chapter = {
                        "marketId": market_id,
                        "selectionId": selection_id,
                        "oc_label": prev_label,
                        "odds": current_odds,
                        "oc_band": finalized_band,
                        "range_low": range_low,
                        "range_high": range_high,
                        "tick_pattern": tick_pattern,
                        "volatility": vol,
                        "position_ratio": pos_ratio,
                        "direction_bias": "drifted" if exit > entry else "steamed" if exit < entry else "flat",
                        "minutes_to_post": minutes_to_post,
                        "timestamp": now,
                        "confidence": 0.65,
                        "blueprint_match": None
                    }

                    # ✅ Save chapter snapshot to structured DB column
                    save_chapter_to_runner_story(market_id, selection_id, chapter)

                    # ✅ Begin tracking next OC band
                    active_bands[key] = {"label": oc_label, "band": [current_odds]}

                conn.close()
                time.sleep(2)

            except Exception as e:
                print(f"❌ OC Band Tracker crashed: {e}")

    def classify_tick_pattern(self, entry, exit, series):
        if not isinstance(series, list) or len(series) < 2:
            return "flat"
        deltas = [series[i+1] - series[i] for i in range(len(series)-1)]
        if all(d > 0 for d in deltas):
            return "drifted"
        if all(d < 0 for d in deltas):
            return "steamed"
        if any(abs(d) >= 2 for d in deltas):  # tune threshold if your “tick” scale differs
            return "volatile"
        if any(d != 0 for d in deltas):
            return "pingpong"
        return "flat"


    def sync_all_runner_odds(self):
        cycle_counter = 0

        print("📡 Starting live odds sync every 2s...")
        while True:
            try:
                for market_id in self.live_markets:
                    for selection_id in self.live_markets[market_id]:
                        odds = get_active_runner_odds(market_id, selection_id)
                        if not odds:
                            continue

                        snapshot = self.get_static_snapshot().get((market_id, selection_id), {}).get("snapshot", {})
                        if not snapshot:
                            continue

                        market_start_time = get_market_start_time(market_id)
                        if market_start_time:
                            snapshot["minutes_to_post"] = self.calculate_minutes_to_post(market_start_time)
                            current_label = self.get_current_oc_label(snapshot["minutes_to_post"])
                            prev_label = snapshot.get("last_oc_triggered")

                            if current_label and current_label != prev_label:
                                snapshot["last_oc_triggered"] = current_label
                                story = self.get_runner_story(market_id, selection_id)
                                chapters = story.get("chapters", [])
                                if chapters:
                                    signal = self.evaluate_matching_opportunity(market_id, selection_id)
                                    if signal:
                                        from signal_memory_engine.bet_router import fire_initial_scalp
                                        order_ref = f"oc_{current_label}_{selection_id}"[-15:]
                                        response = fire_initial_scalp(signal, order_ref)
                                        if response:
                                            print(f"🎯 OC Trigger Scalp Placed: {selection_id} @ {signal.get('odds')} ({signal.get('confidence')})")

                        self.update_tick_trail(snapshot, odds)
                        self.update_odds_history(snapshot)
                        self.update_oc_snapshots(snapshot, market_id, selection_id)
                        self.update_direction_bias(snapshot)

                        save_snapshot_to_ram(market_id, selection_id, snapshot)

                cycle_counter += 1

                if cycle_counter % 30 == 0:
                    self.repair_oc_snapshots()

                if cycle_counter % 5 == 0:
                    self.run_snapshot_ingestion_cycle()

                time.sleep(2)

            except Exception as e:
                print(f"❌ Live odds sync error: {e}")

    def can_place_more_bets(self, market_id, selection_id) -> bool:
        # TODO: implement real cap logic (e.g., count unmatched on runner)
        return True



    def update_tick_trail(self, snapshot, odds):
        trail = snapshot.get("tick_trail", [])
        trail.append(odds)
        if len(trail) > 60:
            trail = trail[-60:]
        snapshot["tick_trail"] = trail

    def update_odds_history(self, snapshot):
        snapshot["odds_history"] = snapshot.get("tick_trail", [])

    def update_oc_snapshots(self, snapshot, market_id, selection_id):
        label = self.get_current_oc_label(snapshot.get("minutes_to_post", 999))
        if not label:
            return

        oc_snaps = snapshot.get("oc_snapshots", {})
        current_odds = snapshot.get("tick_trail", [])[-1] if snapshot.get("tick_trail") else None

        # ✅ Save single point value (OC1, OC2, etc.)
        if label not in oc_snaps:
            oc_snaps[label] = {
                "odds": current_odds,
                "timestamp": time.time()
            }

        # ✅ Append to band history
        band_key = f"{label}_band"
        band = oc_snaps.get(band_key, [])
        band.append(current_odds)
        if len(band) > 600:  # cap for safety
            band = band[-600:]
        oc_snaps[band_key] = band

        # ✅ Update DB with OC1 / OC1_band etc.
        from signal_memory_engine.ram_writer import persist_oc_label_and_band
        persist_oc_label_and_band(market_id, selection_id, label, current_odds)



        snapshot["oc_snapshots"] = oc_snaps
        from signal_memory_engine.ram_writer import persist_oc_snapshots_to_db
        persist_oc_snapshots_to_db(market_id, selection_id, snapshot)

        save_snapshot_to_ram(market_id, selection_id, snapshot)


    def update_direction_bias(self, snapshot):
        trail = snapshot.get("tick_trail", [])
        if len(trail) < 3:
            snapshot["direction_bias"] = "flat"
            return

        direction = "drifted" if trail[-1] > trail[0] else "steamed" if trail[-1] < trail[0] else "flat"
        snapshot["direction_bias"] = direction

    def get_current_oc_label(self, minutes_to_post: float) -> str:
        try:
            if minutes_to_post >= 80:
                return None  # Too early

            if minutes_to_post > 60:
                return "OC1"
            elif minutes_to_post > 40:
                return "OC2"
            elif minutes_to_post > 20:
                return "OC3"
            elif minutes_to_post > 10:
                return "OC4"
            elif minutes_to_post > 5:
                return "OC5"
            elif minutes_to_post > 0:
                return "OC6"
            elif -0.5 <= minutes_to_post <= 0.5:
                return "OC7"
            else:
                # Post-off OC windows
                minute_offset = int(abs(minutes_to_post)) + 8  # OC8 = 1min after, OC9 = 2min after, etc.
                return f"OC{minute_offset}"
        except:
            return None

   
    def predict_next_segment(self, segment_key):
        """
        Predicts the most likely next OC segment key based on current sequence and known blueprints.
        """
        if not segment_key or not isinstance(segment_key, str):
            return None

        matching = [k for k in self.KNOWN_BLUEPRINT_PATTERNS if k.startswith(segment_key)]
        if not matching:
            return None

        next_parts = set()
        for m in matching:
            parts = m.split("→")
            seg_parts = segment_key.split("→")
            if len(parts) > len(seg_parts):
                next_parts.add(parts[len(seg_parts)])

        if not next_parts:
            return None

        return max(next_parts, key=lambda x: len(x))  # simple heuristic: return the longest next segment


    def get_adaptive_stake(self):
        try:
            # 🧠 Find the last unmatched signal if available
            if not self.unmatched_signals:
                print("⚠️ No unmatched signals available. Returning default stake.")
                return self.current_stake

            # Get the most recent unmatched signal (latest timestamp across all keys)
            latest = None
            latest_key = None
            for unmatched_key, signals in self.unmatched_signals.items():
                for s in signals:
                    if not latest or s["timestamp"] > latest["timestamp"]:
                        latest = s
                        latest_key = unmatched_key

            signal = latest or {}
            market_id = latest_key[0] if latest_key else None
            selection_id = latest_key[1] if latest_key else None

            confidence = signal.get("confidence", 0.0)
            blueprint_match = signal.get("blueprint_match", None)
            range_low = signal.get("range_low", 0)
            range_high = signal.get("range_high", 999)
            tick_pattern = signal.get("tick_pattern", "flat")
            forced = signal.get("forced", False)

            base_stake = 10.00

            if forced:
                print(f"🔒 Forced signal: £10 stake.")
                return base_stake

            odds = signal.get("odds", 0)
            if not isinstance(odds, (float, int)) or odds < 1.01:
                
                print(f"⚠️ Invalid odds {odds}. Using £2 fallback.")
                return 2.00

            if market_id and selection_id:
                if not self.can_place_more_bets(market_id, selection_id):
                    print(f"⚠️ Max bets on runner {selection_id}. Using £4 reduced stake.")
                    return 4.00

            if confidence < 0.4:
                print(f"📉 Low confidence {confidence}. Using £4 stake.")
                return 4.00

            if not blueprint_match:
                print(f"❌ No blueprint match. Using £6 stake.")
                return 6.00

            # 🧠 Live intraday P&L-based adjustment
            if blueprint_match in self.live_pattern_pnl:
                stats = self.live_pattern_pnl.get(blueprint_match, {})
                trades = stats.get("trades", 0)
                pnl = stats.get("net_pnl", 0.0)

            # 🧪 Let cold blueprints run as normal
            if trades < 3:
                print(f"🧪 Cold blueprint {blueprint_match} – No stake penalty (trades={trades})")
                return base_stake

            # 🪙 If confidence or profit is low, reduce stake only (no blocking)
            if pnl < 5 or confidence < 0.68:
                print(f"⚠️ Reduced stake for {blueprint_match}: PnL £{pnl:.2f}, conf {confidence:.2f}")
                return 4.00

            # 🚀 Promote profitable blueprints (optional)
            if trades >= 10 and pnl >= 40 and confidence >= 0.75:
                print(f"📈 Pattern {blueprint_match} promoted: {trades} trades, £{pnl:.2f} PnL, {confidence:.2f} confidence. Stake: £14")
                return 14.00

            if pnl < -20:
                print(f"🔻 Live pattern {blueprint_match} down £{pnl:.2f}. Capping stake at £2.")
                return 2.00
            elif pnl > 30:
                print(f"🚀 Live pattern {blueprint_match} up £{pnl:.2f}. Boosting stake to £12.")
                return 12.00




            if odds > 12.0:
                print(f"💥 High odds {odds}. Using £6 stake.")
                return 6.00

            range_size = range_high - range_low if range_high and range_low else 999
            if range_size <= 1.5 and tick_pattern in ["came_in", "reversed"]:
                print(f"🚀 Tight range and aggressive pattern. Boosted stake to £12.")
                return 12.00

            print(f"✅ Standard case. Using base stake £10.")
            return base_stake

        except Exception as e:
            print(f"💥 Error in get_adaptive_stake: {e}")
            return 2.00
        

        print("✅ Bound methods: analyse_runner, evaluate_signal_for_runner, attempt_partial_blueprint_match, predict_next_segment, build_final_runner_snapshot")

    def evaluate_signal_for_runner(self, market_id, selection_id):
        from signal_memory_engine.ram_reader import get_ram_snapshot

        snapshot = get_ram_snapshot(market_id, selection_id).get("snapshot", {})
        if not snapshot:
            return

        oc_snapshots = snapshot.get("oc_snapshots", {})
        oc_band_data = {}
        for label in oc_snapshots:
            if label.endswith("_band"):
                oc_band_data[label] = oc_snapshots[label]

        if oc_band_data:
            print(f"🧪 OC Band Data Detected: {[f'{k} ({len(v)})' for k, v in oc_band_data.items()]}")

        return

    def get_static_snapshot(self):
        return ram_get_static_snapshot()



    def maybe_reset_for_new_day(self):
        return maybe_reset_for_new_day(self)

    def bootstrap_initial_market_list(self):
        time.sleep(1)  # give everything 1s to settle
        self.run_snapshot_ingestion_cycle()



    def start_monitoring_threads(self):
        threading.Thread(target=monitor_passive_trends, args=(self,), daemon=True).start()
        threading.Thread(target=monitor_pre_off_trends, args=(self,), daemon=True).start()
        threading.Thread(target=monitor_in_play_trends, args=(self,), daemon=True).start()
        threading.Thread(target=monitor_live_for_partial_blueprints, args=(self,), daemon=True).start()
        threading.Thread(target=self.bootstrap_initial_market_list, daemon=True).start()
        threading.Thread(target=generate_signal_status_report, args=(self,), daemon=True).start()
        threading.Thread(target=self.sync_all_runner_odds, daemon=True).start()
        threading.Thread(target=lambda: (time.sleep(10), self.record_oc0_and_band()), daemon=True).start()
        threading.Thread(target=self.scan_all_runners_for_scalps, daemon=True).start()
        from signal_memory_engine.snapshot_evaluator import tick_and_record_oc_data
        threading.Thread(target=lambda: (time.sleep(12), tick_and_record_oc_data()), daemon=True).start()

        # ✅ Activate OC Band Tracker in background
        from signal_memory_engine.oc_band_tracker import run_oc_band_tracker
        threading.Thread(target=run_oc_band_tracker, daemon=True).start()
        threading.Thread(target=self.start_story_builder_loop, daemon=True).start()
        # ✅ START THE DATABASE WRITER LOOP
        if not getattr(sys, '_db_writer_started', False):
            from engines.database_hijack_monitor import launch_db_writer
            launch_db_writer()
            sys._db_writer_started = True

        print("🚀 SignalMemoryEngine fully initialized and monitoring started.")


    def check_and_register_new_ladders(self):
        import sqlite3
        from config_paths import DB_PATH
        from price_math import get_tick_size
        import sys
        import os
        sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        from engines.database_hijack_monitor import enqueue_write

        try:
            def _exec():
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT customerOrderRef, marketId, selectionId, odds, stake, side
                    FROM bets
                    WHERE status = 'matched'
                """)
                rows = cursor.fetchall()
                conn.close()

                for ref, market_id, selection_id, odds, stake, side in rows:
                    key = (market_id, selection_id)
                    if key not in self.active_ladders:
                        direction = "lay_to_back" if side.upper() == "LAY" else "back_to_lay"
                        self.active_ladders[key] = {
                            "rung": 1,
                            "current_odds": odds,
                            "tick_size": get_tick_size(odds),
                            "direction": direction,
                            "stake": stake,
                            "status": "active"
                        }
                        print(f"📦 Ladder activated: {ref} {selection_id} @ {odds} [{direction}]")

            enqueue_write(_exec)
        except Exception as e:
            print(f"❌ Ladder DB check failed: {e}")
            return

        for ref, market_id, selection_id, odds, stake, side in rows:
            key = (market_id, selection_id)
            if key not in self.active_ladders:
                direction = "lay_to_back" if side.upper() == "LAY" else "back_to_lay"
                self.active_ladders[key] = {
                    "rung": 1,
                    "current_odds": odds,
                    "tick_size": get_tick_size(odds),
                    "direction": direction,
                    "stake": stake,
                    "status": "active"
                }
                print(f"📦 Ladder activated: {ref} {selection_id} @ {odds} [{direction}]")
    
 

    def run_snapshot_ingestion_cycle(self):
        try:
            snapshot_data = self.get_static_snapshot()
         

            for market_id, runner_ids in self.live_markets.items():
                enriched = []
                for sid in runner_ids:
                    snap = snapshot_data.get((market_id, sid), {}).get("snapshot", {})
                    if snap:
                        enriched.append({**snap, "marketId": market_id, "selectionId": sid})
                if enriched:
                    self.ingest_market_snapshot(market_id, enriched)

        except Exception as e:
            print(f"❌ Snapshot ingestion cycle failed: {e}")


    def print_mini_oc_snapshot(self, runner_map):
        from collections import defaultdict

        market_runners = defaultdict(list)
        oc_presence = defaultdict(int)
        top6_under8_total = 0
        total_markets = 0
        total_runners = 0

        for runner in runner_map.values():
            market_id = runner.get("marketId")
            if not market_id:
                continue
            market_runners[market_id].append(runner)

        total_markets = len(market_runners)

        for market_id, runners in sorted(market_runners.items()):
            total_runners += len(runners)

            sorted_runners = sorted(
                [r for r in runners if isinstance(r.get("odds"), (int, float))],
                key=lambda r: r["odds"]
            )
            top6 = sorted_runners[:6]

            for r in top6:
                if r["odds"] < 8.0:
                    top6_under8_total += 1

                for i in range(1, 7):
                    if r.get(f"oc{i}"):
                        oc_presence[f"OC{i}"] += 1

        print("\n📊 MINI OC SNAPSHOT REPORT:")
        print(f"• Total markets evaluated: {total_markets}")
        print(f"• Total runners evaluated: {total_runners}")
        print(f"• Total top-6 runners with odds < 8.0: {top6_under8_total}\n")

        print("📊 OC Coverage Snapshot:")
        for i in range(1, 7):
            print(f"• OC{i} present in runners: {oc_presence[f'OC{i}']}")

    def ingest_market_snapshot(self, market_id, runners):
        """
        🔁 Called every 10s from evaluate_market_loop().
        Runners = live_runner_map[market_id], already has all enriched fields.
        """
        from signal_memory_engine.analyze_runner import analyse_runner
        from signal_memory_engine.evaluate_signal import evaluate_signal_for_runner
      
        from signal_memory_engine.ram_reader import get_ram_snapshot
        from signal_memory_engine.signal_blueprint_match import detect_blueprint_match
        import uuid

        self.analyse_runner(market_id, runners)


        if market_id not in self.live_markets:
            self.live_markets[market_id] = [r["selectionId"] for r in runners]

        for r in runners:
            selection_id = r["selectionId"]
            odds = r.get("odds")

            if not isinstance(odds, (int, float)):
                continue

            if odds <= 12.0:
                tier = "active"
            elif odds <= 50.0:
                tier = "passive"
            else:
                tier = "ignored"

            if tier == "passive":
                continue

            key = (market_id, selection_id)
            snapshot = get_ram_snapshot(*key).get("snapshot", {})

            if not snapshot:
                continue

            self.evaluate_signal_for_runner(market_id, selection_id)

            odds_history = snapshot.get("odds_history") or snapshot.get("tick_trail") or []
            if len(odds_history) < 3:
                continue
            snapshot["odds_history"] = odds_history


            tick_pattern = snapshot.get("tick_pattern", "flat")
            oc_snaps = snapshot.get("oc_snapshots", {})
            anchor = snapshot.get("anchor_odd")
            range_low = snapshot.get("range_low")
            range_high = snapshot.get("range_high")
            confidence = 0.72

            if not all([anchor, range_low, range_high]):
                continue

            if tick_pattern not in ["drifted", "steamed"]:
                continue

            direction = "lay_to_back" if tick_pattern == "drifted" else "back_to_lay"

            signal = {
                "marketId": market_id,
                "selectionId": selection_id,
                "odds": odds_history[-1],
                "range_low": range_low,
                "range_high": range_high,
                "tick_pattern": tick_pattern,
                "position_ratio": snapshot.get("position_ratio", 0.5),
                "volatility": snapshot.get("volatility", 0.0),
                "scalp_direction": direction,
                "signal_type": "autonomous_scan",
                "confidence": confidence,
                "minutes_to_post": snapshot.get("minutes_to_post", 999),
            }

            snapshot["snapshot"] = self.build_final_runner_snapshot(market_id, selection_id)
            save_snapshot_to_ram(market_id, selection_id, snapshot)
            # ✅ Save to bets for persistence
            from signal_memory_engine.ram_writer import save_snapshot_to_bets
            save_snapshot_to_bets(market_id, selection_id, snapshot)


            print("🧪 SIGNAL DEBUG: Reached tracking block...")
            print(f"    • marketId: {market_id}")
            print(f"    • selectionId: {selection_id}")
            print(f"    • signal_type: {signal.get('signal_type')}")
            print(f"    • confidence: {signal.get('confidence')}")
            print(f"    • tick_pattern: {signal.get('tick_pattern')}")
            print(f"    • anchor: {anchor} | range: {range_low}-{range_high}")
            print(f"    • odds_history: {snapshot.get('odds_history', [])}")
            print(f"    • OC Snapshots: {list(oc_snaps.keys())}")

            self.track_unmatched_signal(signal)

            from signal_memory_engine.utils.bet_router import fire_initial_scalp

            if 0.55 < confidence < 0.72 and (market_id, selection_id) not in self.active_ladders:
                signal = {
                    "marketId": market_id,
                    "selectionId": selection_id,
                    "odds": snapshot.get("anchor_odd", 0.0),
                    "range_low": snapshot.get("range_low"),
                    "range_high": snapshot.get("range_high"),
                    "tick_pattern": snapshot.get("tick_pattern"),
                    "position_ratio": snapshot.get("position_ratio", 0.5),
                    "volatility": snapshot.get("volatility", 0.0),
                    "scalp_direction": snapshot.get("direction_bias", "flat"),
                    "signal_type": "exploratory",
                    "confidence": confidence,
                    "minutes_to_post": snapshot.get("minutes_to_post", 999),
                    "forced": True
                }

                customer_order_ref = f"explr_{uuid.uuid4().hex[:6]}"
                response = fire_initial_scalp(signal, customer_order_ref)
                if response:
                    print(f"🧪 Exploratory scalp fired for {selection_id} at {signal['odds']}")

            # Final fallback: check for exploratory scalp opportunity
            if 0.55 < confidence < 0.72 and (market_id, selection_id) not in self.active_ladders:
                signal = {
                    "marketId": market_id,
                    "selectionId": selection_id,
                    "odds": snapshot.get("anchor_odd", 0.0),
                    "range_low": snapshot.get("range_low"),
                    "range_high": snapshot.get("range_high"),
                    "tick_pattern": snapshot.get("tick_pattern"),
                    "position_ratio": snapshot.get("position_ratio", 0.5),
                    "volatility": snapshot.get("volatility", 0.0),
                    "scalp_direction": snapshot.get("direction_bias", "flat"),
                    "signal_type": "exploratory",
                    "confidence": confidence,
                    "minutes_to_post": snapshot.get("minutes_to_post", 999),
                    "forced": True
                }

                customer_order_ref = f"explr_{uuid.uuid4().hex[:6]}"
                response = fire_initial_scalp(signal, customer_order_ref)
                if response:
                    print(f"🧪 Exploratory scalp fired for {selection_id} at {signal['odds']}")

            # ✅ Evaluate blueprint-based signal after exploratory logic
            self.evaluate_signal_for_runner(market_id, selection_id)

            self.check_and_register_new_ladders()
    def resolve_pnl_for_runner(self, market_id, selection_id):
        import sqlite3
        import json
        from config_paths import DB_PATH
        from signal_memory_engine.pnl import track_live_pattern_pnl
        from signal_memory_engine.playbook import record_matched_bet

        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT odds, stake, side, pnl, blueprint_match, signal_type, placed_at
                FROM bets
                WHERE marketId = ? AND selectionId = ? AND status = 'matched' AND bet_settled = 1
                ORDER BY placed_at DESC
                LIMIT 1
            """, (market_id, selection_id))
            row = cursor.fetchone()
            conn.close()

            if not row:
                print(f"🔍 No settled matched bets found for {selection_id}")
                return

            odds, stake, side, pnl, blueprint_match, signal_type, placed_at = row
            pnl = float(pnl or 0.0)
            result = "win" if pnl > 0 else "loss" if pnl < 0 else "neutral"

            # Log pattern-level PnL
            if blueprint_match:
                track_live_pattern_pnl(blueprint_match, pnl)

            # Log final outcome to playbook
            record = {
                "marketId": market_id,
                "selectionId": selection_id,
                "odds": odds,
                "stake": stake,
                "side": side,
                "signal_type": signal_type,
                "blueprint_match": blueprint_match,
                "pnl": pnl,
                "result": result,
                "timestamp": placed_at
            }

            record_matched_bet(record)
            print(f"📘 Recorded PnL for {selection_id}: £{pnl:.2f} ({result})")

        except Exception as e:
            print(f"❌ Error resolving PnL for runner {selection_id}: {e}")
