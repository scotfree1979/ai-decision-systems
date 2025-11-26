# signal_memory_engine.py

from collections import defaultdict, deque
import time
import sqlite3
from datetime import datetime
# 📘 Load from dynamic blueprint export
from config_paths import DB_PATH
import uuid
import threading  # Make sure it's imported at the top (it already is)
import copy
from utils.time_utils import calculate_minutes_to_post
import logging
import os
import json
import traceback

# ⚠️ NEW: Set upper limit for stacking bets
MAX_OPEN_BETS_PER_RUNNER = 3
MIN_LIVE_ODDS_FOR_ENTRY = 1.5
MAX_LIVE_ODDS_FOR_ENTRY = 8.0
MAX_ABSOLUTE_ODDS = 20.0
position_ratio = 0.5  # Default if not set above
APP_KEY = "CZHojduNWa3kxWIn"

# ✅ Helper to fetch settled PnL from Betfair API
def fetch_settled_pattern_pnl(session_token):
    try:
        headers = {
            'X-Application': APP_KEY,
            'X-Authentication': session_token,
            'Content-Type': 'application/json'
        }
        payload = {
            "jsonrpc": "2.0",
            "method": "SportsAPING/v1.0/listSettledBets",
            "params": {
                "betStatus": "SETTLED"
            },
            "id": 1
        }
        response = requests.post("https://api.betfair.com/exchange/betting/json-rpc/v1", headers=headers, json=payload)
        data = response.json()
        settled = data.get('result', {}).get('bets', [])
        return settled
    except Exception as e:
        print(f"⚠️ Failed to fetch settled bets: {e}")
        return []


def save_signal_snapshot_to_live_bets(signal):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS live_signals(
                    marketId TEXT,
                    selectionId INTEGER,
                    odds REAL,
                    range_low REAL,
                    range_high REAL,
                    tick_pattern TEXT,
                    confidence REAL,
                    drift REAL,
                    spread REAL,
                    position_ratio REAL,
                    volatility REAL,
                    signal_type TEXT,
                    stake REAL,
                    timestamp TEXT
                )
            """)
            cursor.execute("""
                INSERT INTO live_signals(
                    marketId, selectionId, odds, range_low, range_high, tick_pattern,
                    confidence, drift, spread, position_ratio, volatility, signal_type, stake, timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal.get("marketId"),
                signal.get("selectionId"),
                signal.get("odds"),
                signal.get("range_low"),
                signal.get("range_high"),
                signal.get("tick_pattern"),
                signal.get("confidence"),
                signal.get("drift", 0.0),
                signal.get("spread", 0),
                signal.get("position_ratio"),
                signal.get("volatility", 0.0),
                signal.get("signal_type", "None"),
                signal.get("stake"),
                datetime.utcnow().isoformat()
            ))
            conn.commit()
    except Exception as e:
        print(f"⚠️ Failed to save signal snapshot: {e}")


class SignalMemoryEngine:
    def __init__(self, max_history=12):
        # 📘 Load known blueprints + segment structure
        self.KNOWN_BLUEPRINT_PATTERNS = self.load_known_blueprints()
        print(f"📘 Loaded {len(self.KNOWN_BLUEPRINT_PATTERNS)} blueprint patterns from JSON.")
        self.SEGMENT_DB = self.extract_blueprint_segments()
        self.debug_signal_counter = 0
        self._track_signal_counter = defaultdict(int)
        self._last_track_reported = 0


        # 🧠 Core memory and locks
        self.memory_lock = threading.Lock()
        self.memory_write = defaultdict(dict)
        self.memory_read = {}
        import logging
        self._debug_logger = logging.getLogger("SignalDebugLogger")
        self._debug_logger.addHandler(logging.NullHandler())
        self.live_inplay_odds = defaultdict(lambda: defaultdict(list))


        # 📡 Market tracking
        self.live_markets = defaultdict(list)
        self.active_tradable_markets = []
        self.active_blueprints_today = set()
        self.unmatched_lock = threading.Lock()

        # 📊 Signal tracking
        self.unmatched_signals = {}
        self.active_ladders = {}
        self.trade_log = []
        self.clean_exit_log = []
        self.live_playbooks = defaultdict(dict)
        self.live_pattern_pnl = defaultdict(lambda: {"trades": 0, "net_pnl": 0.0})

        # 🔁 Open bet tracking
        self.open_bets = defaultdict(list)

        # 📈 Session control
        self.session_active = True
        self.session_date = datetime.utcnow().date()
        self.starting_balance = 500.0
        self.current_balance = 500.0
        self.current_stake = 10.0
        self.halt_triggered = False

        # 📉 Trend tracking
        self.passive_tick_tracker = defaultdict(lambda: defaultdict(list))
        self.in_play_tick_tracker = defaultdict(lambda: defaultdict(list))

        # 🪵 Logging
        self._debug_logger = None
        self._debug_log_path = None


    def load_known_blueprints(self):
        import os
        import glob
        import json

        try:
            blueprint_files = glob.glob("blueprint_signals_*.json")
            if not blueprint_files:
                print("⚠️ No blueprint JSON files found.")
                return {}

            latest_file = max(blueprint_files, key=os.path.getctime)
            print(f"📘 Loading blueprints from latest file: {latest_file}")

            with open(latest_file, "r") as f:
                raw_data = json.load(f)

                if not isinstance(raw_data, dict):
                    print("❌ Expected dict of blueprint patterns. Got:", type(raw_data))
                    return {}

                print(f"✅ Loaded {len(raw_data)} blueprint patterns from dict.")
                return raw_data

        except Exception as e:
            print(f"⚠️ Failed to load known blueprints: {e}")
            return {}

    def update_runner_memory(self, market_id, selection_id, updates: dict):
        key = (market_id, selection_id)
        with self.memory_lock:
            if key not in self.memory_write:
                self.memory_write[key] = {
                    "odds_history": deque(maxlen=12),
                    "tick_trail": deque(maxlen=12),
                    "oc_snapshots": {},
                    "direction_bias": "flat",
                    "tick_pattern": "flat",
                    "anchor_odd": None,
                    "range_low": None,
                    "range_high": None,
                    "position_ratio": position_ratio,  # if the value was calculated above

                    "volatility": 0.0,
                    "last_updated": None,
                    "position": None,
                    "minutes_to_post": 999
                }
            mem = self.memory_write[key]
            mem.update(updates)
            mem["snapshot"] = self.build_final_runner_snapshot(market_id, selection_id)


    def evaluate_market_loop(self):
        import sqlite3
        from upgrade_import_patch import get_session_token
        from utils.api_tools import fetch_live_odds

        while True:
            try:
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                today_str = datetime.utcnow().strftime("%Y-%m-%d")
                session_token = get_session_token()

                for market_id in self.active_tradable_markets:
                    cursor.execute("""
                        SELECT selectionId FROM bets
                        WHERE marketId = ? AND DATE(marketStartTime) = ?
                    """, (market_id, today_str))
                    rows = cursor.fetchall()

                    enriched_runners = []
                    for row in rows:
                        selection_id = row[0]
                        odds = fetch_live_odds(session_token=session_token, marketId=market_id, selectionId=selection_id)
                        lay = odds.get("lay") if odds else None
                        back = odds.get("back") if odds else None
                        if not lay:
                            continue
                        enriched_runners.append({
                            "selectionId": selection_id,
                            "odds": lay,
                            "tick_trail": [lay, back] if back else [lay]
                        })

                    if enriched_runners:
                        inject_live_market_data(market_id, enriched_runners)

                conn.close()

            except Exception as e:
                print(f"⚠️ evaluate_market_loop error: {e}")
            time.sleep(2)


    def maybe_reset_for_new_day(self):
        # ✅ Super-safe guard to delay thread launches until session_token is injected
        while not hasattr(self, "session_token") or self.session_token is None:
            print("⏳ Waiting for session token...")
            time.sleep(1)

        today = datetime.utcnow().date()
        if not hasattr(self, 'session_date'):
            self.session_date = today

        if today != self.session_date:
            signal_memory.flush_inplay_odds_to_db()

            print(f"🔄 New day detected ({today}). Resetting session state.")
            self.start_new_session(balance=self.current_balance)
            self.session_date = today
        else:
            print("📆 Same day. Continuing with existing session.")
            


        if not hasattr(self, "_debug_logger"):
            self.setup_daily_logger()

        # ✅ Inject session token early so all threads can access it
        from upgrade_import_patch import get_session_token
        
        if self.session_token:
            print(f"🔐 Session token injected: {self.session_token[:6]}...")
        else:
            print("⚠️ Warning: No session token available yet.")


        
        self.memory_read = {}  # frozen copy updated every 10s

        self.session_active = True
        self.session_date = datetime.utcnow().date()
        self.starting_balance = 600.0
        self.current_balance = 600.0
        self.halt_triggered = False
        self.trade_log = []
        self.clean_exit_log = []
        self.current_stake = 10.0
        self.open_bets = defaultdict(list)
        self.unmatched_signals = {}
        self.SEGMENT_DB = self.extract_blueprint_segments()

        self.live_playbooks = defaultdict(lambda: {
            "start_oc": None,
            "end_oc": None,
            "opened_at": None,
            "closed_at": None,
            "pnl": 0.0,
            "status": "open",
            "steps": []
        })

        self.live_pattern_pnl = defaultdict(lambda: {
            "trades": 0,
            "net_pnl": 0.0
        })

        self.load_pattern_pnl_from_playbooks()

        threading.Thread(target=self.monitor_passive_trends, daemon=True).start()
        # Pre-off tick tracking (formerly monitor_in_play_trends)
        threading.Thread(target=self.monitor_pre_off_trends, daemon=True).start()

        # NEW: In-play odds tracking from OC6 to +15min post
        threading.Thread(target=self.monitor_in_play_trends, daemon=True).start()


        print("📦 Waiting 5s before initial OC snapshot load...")
        time.sleep(5)
        print("📦 Initialising session with OC snapshot load...")
        self.load_oc_snapshots_from_db()
        threading.Thread(target=self.sync_memory_snapshots, daemon=True).start()
        threading.Thread(target=self.evaluate_market_loop, daemon=True).start()
        threading.Thread(target=self.generate_signal_status_report, daemon=True).start()
        threading.Thread(target=self.update_tradable_markets, daemon=True).start()
        threading.Thread(target=self.monitor_live_for_partial_blueprints, daemon=True).start()
        threading.Thread(target=self.evaluate_active_runners, daemon=True).start()
        # 🔎 Start volatility conflict watchdog thread
        threading.Thread(target=self._conflict_debug_watchdog, daemon=True).start()

        # 📍 Add this new method to SignalMemoryEngine:
    def _conflict_debug_watchdog(self):
        while True:
            with self.memory_lock:
                for key, data in self.memory_write.items():
                    if data.get("tick_pattern") == "drifted" and data.get("volatility", 0) > 0.3:
                        print(f"⚠️ HIGH VOL DRIFT: {key} → Vol={data.get('volatility'):.2f} | OC={len(data.get('oc_snapshots', {}))}")
                    # 🧮 Track stale counts per cycle
                    self._stale_market_counter = getattr(self, "_stale_market_counter", defaultdict(set))
                    self._stale_count_reported = getattr(self, "_stale_count_reported", 0)

                    last_updated = data.get("last_updated")
                    if isinstance(last_updated, (int, float)) and time.time() - last_updated > 10:
                        self._stale_market_counter[key[0]].add(key[1])

                    total_stale = sum(len(v) for v in self._stale_market_counter.values())
                    if total_stale != self._stale_count_reported:
                        print(f"⚠️ STALE SNAPSHOTS: {len(self._stale_market_counter)} markets, {total_stale} runners")
                        self._stale_count_reported = total_stale



            time.sleep(6)

        
    def sync_memory_snapshots(self):
        while True:
            try:
                with self.memory_lock:
                    self.memory_read = copy.deepcopy(self.memory_write)
                    print(f"🧊 Synced memory_read ← memory_write ({len(self.memory_read)} runners)")




            except Exception as e:
                print(f"⚠️ Failed to sync memory snapshot: {e}")
            time.sleep(10)


    def get_static_snapshot(self):
        with self.memory_lock:
            return copy.deepcopy(self.memory_read)


        self.live_playbooks = defaultdict(lambda: {
            "start_oc": None,
            "end_oc": None,
            "opened_at": None,
            "closed_at": None,
            "pnl": 0.0,
            "status": "open",
            "steps": []
        })

        # Inside SignalMemoryEngine.__init__()
        self.live_pattern_pnl = defaultdict(lambda: {
            "trades": 0,
            "net_pnl": 0.0
        })

        self.load_pattern_pnl_from_playbooks()  # ✅ Restore confidence from DB



        self.session_active = True
        self.session_date = datetime.utcnow().date()  # ✅ add this line
        self.starting_balance = 575.0
        self.current_balance = 575.0
        self.halt_triggered = False
        self.trade_log = []
        self.clean_exit_log = []
        self.current_stake = 10.0
        self.open_bets = defaultdict(list)
        self.unmatched_signals = {}
        threading.Thread(target=self.monitor_live_for_partial_blueprints, daemon=True).start()

    def sync_memory_snapshots(self):
        import time
        while True:
            try:
                with self.memory_lock:
                    self.memory_read = copy.deepcopy(self.memory_write)
                    print(f"🧊 Synced memory_read ← memory_write ({len(self.memory_read)} runners)")
            except Exception as e:
                print(f"⚠️ Failed to sync memory snapshot: {e}")
            time.sleep(10)



    def monitor_passive_trends(self):
        from upgrade_import_patch import get_session_token
        self.session_token = get_session_token()  # ✅ Inject live session token
        from utils.api_tools import fetch_live_odds
        import time

        while True:
            for market_id, runners in list(self.live_markets.items()):
                for selection_id in runners:
                    try:
                        odds = fetch_live_odds(get_session_token(), market_id, selection_id)
                        lay = odds.get("lay") if odds else None
                        if not lay:
                            continue
                        self.passive_tick_tracker[market_id][selection_id].append(lay)
                    except Exception as e:
                        print(f"⚠️ Passive trend error: {e}")
            time.sleep(2)

    def update_tradable_markets(self):
        import time
        while True:
            try:
                market_oc_times = []
                for market_id, runners in list(self.live_markets.items()):
                    if not runners:
                        continue
                    mins = self.get_minutes_to_post(market_id)
                    if -15 <= mins <= 90:  # ✅ Exclude markets older than 15 mins
                        market_oc_times.append((market_id, mins))

                self.active_tradable_markets = [m[0] for m in market_oc_times]

                print(f"\n🧭 Tracking {len(self.active_tradable_markets)} markets under 90 mins:")
                for market_id, mins in market_oc_times[:10]:
                    print(f"  • {market_id} → {mins} min to post")
            except Exception as e:
                print(f"⚠️ Failed to update tradable markets: {e}")
            time.sleep(15)


    def evaluate_active_runners(self):
        """
        🧠 New logic to evaluate runners using segment-based prediction.
        Runs every few seconds.
        """
        import time

        while True:
            try:
                with self.memory_lock:
                    memory_snapshot = copy.deepcopy(self.memory_read)
                    blueprints = dict(self.KNOWN_BLUEPRINT_PATTERNS)
                    signals_to_track = []


                for key, runner in memory_snapshot.items(): # 🔐 static copy

               
                    market_id, selection_id = key
                    snapshot = copy.deepcopy(runner.get("snapshot", {}))
                    if not snapshot:
                        return

                    tier = runner.get("tier")
                    if tier != "active":
                        if tier is None:
                            print(f"❌️ Skipped: Runner {selection_id} → Tier is MISSING or None!")
                            continue


                    tick_chain = snapshot.get("tick_trail", [])
                    if not tick_chain or len(tick_chain) < 4:
                        continue

                    tick_pattern = snapshot.get("tick_pattern", "flat")
                   

                    oc_snaps = copy.deepcopy(snapshot.get("oc_snapshots", {}))

                    if not oc_snaps:
                        continue

                    if "OC0" not in oc_snaps:
                        continue  # skip runners with no anchor

                    oc_labels = sorted(oc_snaps.keys(), key=lambda x: int(x.replace("OC", "")))
                    if len(oc_labels) < 2:  # now OC0 + 1 more = enough
                        continue


                    # 🧩 Form last known segment (e.g., drift→OC2→drifted)
                    latest_oc = oc_labels[-1]
                    direction = snapshot.get("direction_bias", "flat")
                    tick_type = tick_pattern

                    segment_key = f"{direction}→{latest_oc}→{tick_type}"

                    prediction = self.predict_next_segment(segment_key)
                    if not prediction:
                        continue

                    print(f"🔮 Runner {selection_id} → Predicted: {prediction['next_segment']} | Confidence: {prediction['confidence']:.2f}")

                    if prediction["confidence"] >= 0.7:
                        odds = tick_chain[-1]
                        direction = prediction["direction"]
                        scalp_ticks = 2
                        fake_signal = {
                            "marketId": market_id,
                            "selectionId": selection_id,
                            "odds": odds,
                            "scalp_direction": direction,
                            "confidence": prediction["confidence"],
                            "tick_pattern": tick_type,
                            "pattern_key": prediction["segment_key"],
                            "blueprint_match": prediction["segment_key"],
                            "signal_type": "segment_prediction",
                            "forced": True,
                            "scalp_ticks": scalp_ticks
                        }
                        signals_to_track.append(fake_signal)

                for signal in signals_to_track:
                    threading.Thread(target=self.track_unmatched_signal, args=(signal,), daemon=True).start()

          
            except Exception as e:
                
                print("❌️ evaluate_active_runners crashed:", e)
                
                traceback.print_exc()
            time.sleep(4)

    def periodically_sync_from_db(self):
        import time
        while True:
            try:
                self.load_oc_snapshots_from_db()
            except Exception as e:
                print(f"⚠️ Failed to load OC snapshots from DB: {e}")
            time.sleep(12)

    def load_oc_snapshots_from_db(self):
        print("📦 [load_oc_snapshots_from_db] Starting snapshot load...")

        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            today_str = datetime.utcnow().strftime("%Y-%m-%d")
            print(f"📅 [DB Query] Looking for anchor odds for date: {today_str}")

            cursor.execute("""
                SELECT marketId, selectionId, anchor_odd,
                       odds_check_1, odds_check_2, odds_check_3,
                       odds_check_4, odds_check_5, odds_check_6
                FROM bets
                WHERE anchor_odd IS NOT NULL
                  AND DATE(marketStartTime) = ?
            """, (today_str,))
            rows = cursor.fetchall()

            print(f"🔢 Fetched {len(rows)} runner rows with anchor odds")

            # 🧭 Rebuild live_markets from memory_read
            self.live_markets = defaultdict(list)
            for (market_id, selection_id) in self.memory_read:
                self.live_markets[market_id].append(selection_id)
                print(f"📋 Rebuilt live_markets: {len(self.live_markets)} markets")

            for market_id, selection_id, anchor, *ocs in rows:
                key = (market_id, selection_id)
                # 🚫 Freeze OC injection if ladder is currently active
                if key in self.active_ladders:
                    ladder = self.active_ladders[key]
                    if ladder.get("status") == "active":
                        print(f"🚫 Skipping OC injection for {selection_id} (active ladder) → Preventing overwrite")
                        continue
                print(f"\n🧩 Injecting: Market {market_id} | Selection {selection_id} | Anchor {anchor}")

                with self.memory_lock:
                    self.memory_write[key] = {
                        "odds_history": deque(maxlen=12),
                        "last_updated": None,
                        "range_exits": 0,
                        "range_exit_start": None,
                        "direction_bias": "flat",
                        "anchor_odd": anchor,
                        "range_low": None,
                        "range_high": None,
                        "entry_opportunities": 0,
                        "oc_snapshots": {},
                        "last_oc_breakout": None,
                        "tick_pattern": "flat",
                        "position": None,
                        "position_ratio": 0.5,
                        "volatility": 0.0
                    }

                oc_snaps = {}
                if anchor is not None:
                    oc_snaps["OC0"] = {"odds": anchor}

                for i, oc_val in enumerate(ocs):
                    if oc_val is not None:
                        label = f"OC{i+1}"
                        oc_snaps[label] = {"odds": oc_val}
                        print(f"📦 OC Snapshot: {label} = {oc_val}")

                with self.memory_lock:
                    self.memory_write[key]["oc_snapshots"] = oc_snaps
                    mem = self.memory_write[key]

                    odds_chain = [oc_snaps[k]["odds"] for k in sorted(oc_snaps.keys(), key=lambda x: int(x.replace("OC", "")))]
                    mem["tick_trail"] = deque(odds_chain, maxlen=12)
                    mem["range_low"] = min(odds_chain)
                    mem["range_high"] = max(odds_chain)

                    print(f"📈 Odds Chain: {odds_chain}")
                    print(f"📉 Range: {mem['range_low']} → {mem['range_high']}")

                    tick_pattern = self.classify_tick_pattern_from_trail(mem["tick_trail"])
                    mem["tick_pattern"] = tick_pattern
                    mem["direction_bias"] = (
                        "drift" if tick_pattern == "drifted"
                        else "steam" if tick_pattern == "steamed"
                        else "flat"
                    )

                    print(f"🧠 Tick Pattern: {tick_pattern}")
                    print(f"🧭 Bias: {mem['direction_bias']}")

                    mem["snapshot"] = self.build_final_runner_snapshot(market_id, selection_id)
                    print(f"📸 Snapshot injected for runner {selection_id}")

                    # 👇 Place inside load_oc_snapshots_from_db after mem is fully populated
                    odds = self.memory_write[key].get("anchor_odd") or self.memory_write[key].get("oc_snapshots", {}).get("OC1", {}).get("odds")
                    if isinstance(odds, (float, int)):
                        if odds <= 12.0:
                            self.memory_write[key]["tier"] = "active"
                        elif odds <= 50.0:
                            self.memory_write[key]["tier"] = "passive"
                        else:
                            self.memory_write[key]["tier"] = "ignored"
                    else:
                        self.memory_write[key]["tier"] = "ignored"


        with self.memory_lock:
            self.memory_read = copy.deepcopy(self.memory_write)
            print(f"\n🧊 Final memory sync after enrichment ({len(self.memory_read)} runners)")

        with self.memory_lock:
            for key in self.memory_write.keys():
                market_id, selection_id = key
                if market_id not in self.live_markets:
                    self.live_markets[market_id] = []
                if selection_id not in self.live_markets[market_id]:
                    self.live_markets[market_id].append(selection_id)

        print("✅ [load_oc_snapshots_from_db] Completed.")

    def monitor_pre_off_trends(self):
       

        from upgrade_import_patch import get_session_token
        self.session_token = get_session_token()  # ✅ Inject live session token
        from utils.api_tools import fetch_live_odds
        import time

        while True:
            for market_id, runners in self.in_play_tick_tracker.items():
                for selection_id in runners:
                    try:
                        odds = fetch_live_odds(get_session_token(), market_id, selection_id)
                        lay = odds.get("lay") if odds else None
                        if not lay:
                            continue
                        

                        # ✅ WINNER LOGIC
                        if lay <= 1.5:
                            self.record_signal_outcome(
                                market_id=market_id,
                                selection_id=selection_id,
                                result="won",
                                explanation="Detected in-play collapse to ≤ 1.5"
                            )
                    except Exception as e:
                        print(f"⚠️ In-play monitor error: {e}")
            time.sleep(2)

    def monitor_in_play_trends(self):
        from upgrade_import_patch import get_session_token
        from utils.api_tools import fetch_live_odds
        import time

        self.session_token = get_session_token()
  
        while True:
            for market_id, runners in list(self.live_markets.items()):
                for selection_id in runners:
                    try:
                        odds = fetch_live_odds(self.session_token, market_id, selection_id)
                        lay = odds.get("lay") if odds else None
                        if not lay:
                            continue

                        with sqlite3.connect(DB_PATH) as conn:
                            cursor = conn.cursor()
                            cursor.execute("""
                                INSERT INTO inplay_ticks (market_id, selection_id, timestamp, lay_odds)
                                VALUES (?, ?, ?, ?)
                            """, (market_id, selection_id, datetime.utcnow().isoformat(), lay))
                        conn.commit()
                        with sqlite3.connect(DB_PATH) as conn:
                            cursor = conn.cursor()
                            cursor.execute("""
                                CREATE TABLE IF NOT EXISTS inplay_odds_log (
                                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                                    market_id TEXT,
                                    selection_id INTEGER,
                                    date TEXT,
                                    start_time TEXT,
                                    end_time TEXT,
                                    odds_series TEXT
                                )
                            """)
                            conn.commit()

                        minutes = self.get_minutes_to_post(market_id)
                        if minutes > 0 or minutes < -15:
                            continue

                        self.in_play_tick_tracker[market_id][selection_id].append(lay)
                        # 🔁 Track odds in memory
                        self.live_inplay_odds[market_id][selection_id].append(lay)


                        with self.memory_lock:
                            key = (market_id, selection_id)
                            if key in self.memory_write:
                                self.memory_write[key].setdefault("oc_snapshots", {})
                                self.memory_write[key]["oc_snapshots"].setdefault("OC7", {"odds_series": []})
                                self.memory_write[key]["oc_snapshots"]["OC7"]["odds_series"].append(lay)

                        if lay <= 1.5:
                            self.record_signal_outcome(
                                market_id=market_id,
                                selection_id=selection_id,
                                result="won",
                                explanation="In-play odds collapsed to ≤ 1.5"
                            )
                    except Exception as e:
                        print(f"⚠️ In-play monitor error: {e}")
            time.sleep(2)


    def classify_oc7_trend(self, oc6, oc7_series):
        if not oc6 or not oc7_series:
            return "unknown"
        final = oc7_series[-1]
        if final < oc6 - 1.0:
            return "steamed"
        elif final > oc6 + 1.0:
            return "drifted"
        return "pingpong"



    def monitor_live_for_partial_blueprints(self):
        def scan():
            try:
                from price_math import calculate_tick_distance  # ✅ Make sure this is imported at the top
                from upgrade.nextgen_trading import launch_nextgen_thread  # 🔁 Delayed import
            except Exception as e:
                print(f"❌ Failed to import NextGenThread: {e}")
                return

            while True:
                try:
                    with self.unmatched_lock:
                        snapshot = copy.deepcopy(self.unmatched_signals)

                    snapshot = {
                        (market_id, selection_id): signals[-3:]
                        for (market_id, selection_id), signals in snapshot.items()
                    }

                    if len(self.unmatched_signals) > 0:
                        print(f"📊 Unmatched snapshot keys available: {len(self.unmatched_signals)}")

                    for key, history in snapshot.items():
                        print(f"🔎 Checking unmatched history for {key}: length {len(history)}")

                        if len(history) < 2:
                            continue

                        recent = history[:3]
                        tick_chain = "→".join([x["tick_pattern"] for x in recent])

                        for pattern_key in self.KNOWN_BLUEPRINT_PATTERNS:
                            if tick_chain not in pattern_key:
                                continue

                            parts = pattern_key.split("→")
                            if len(parts) < 4:
                                continue

                            direction_prefix = parts[0]
                            oc_range = parts[1]
                            exit_type = parts[2]
                            action = parts[3]

                            start_oc = int(oc_range.split("-")[0].replace("OC", ""))
                            end_oc = int(oc_range.split("-")[1].replace("OC", ""))
                            current_oc = start_oc + len(recent) - 1
                            remaining = end_oc - current_oc

                            if remaining < 1:
                                continue

                            scalp_ticks = min(3, max(1, remaining))

                            print(f"🧠 [Memory] Partial match on {pattern_key} (ticks={scalp_ticks})")

                            fake_signal = {
                                "marketId": key[0],
                                "selectionId": key[1],
                                "odds": recent[0]["odds"],
                                "scalp_direction": action,
                                "confidence": 0.74,
                                "tick_pattern": recent[0]["tick_pattern"],
                                "pattern_key": pattern_key,
                                "blueprint_match": pattern_key,
                                "signal_type": "partial_blueprint",
                                "forced": True,
                                "scalp_ticks": scalp_ticks
                            }

                            # ✅ Launch separately to avoid mid-loop dictionary mutation
                            def delayed_launch(signal_copy):
                                time.sleep(0.1)
                                launch_nextgen_thread(signal_copy)

                            self.track_unmatched_signal(fake_signal)

                            break  # ✅ only launch first match per runner

                except Exception as e:
                    print(f"⚠️ Error in partial blueprint scan: {e}")
                time.sleep(4)


        import threading
        threading.Thread(target=scan, name="BlueprintScanner", daemon=True).start()

    # ✅ LIVE REPORT THREAD
    # Paste this method inside SignalMemoryEngine class and start it in maybe_reset_for_new_day()

    def generate_signal_status_report(self):
        while not hasattr(self, "session_token") or self.session_token is None:
            print("⏳ Waiting for session token to begin status reporting...")
            time.sleep(10)

        from collections import defaultdict
        import logging
        from logging.handlers import RotatingFileHandler

        if not hasattr(self, "_debug_logger"):
            log_path = "signal_debug.log"
            logger = logging.getLogger("SignalDebugLogger")
            logger.setLevel(logging.INFO)
            handler = RotatingFileHandler(log_path, maxBytes=100_000, backupCount=3)
            formatter = logging.Formatter('%(asctime)s | %(message)s')
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            self._debug_logger = logger
        else:
            logger = self._debug_logger

        while self.session_active or self.active_tradable_markets:

            try:
                total = len(self.memory_read)
                signals_placed = sum(len(v) for v in self.unmatched_signals.values())
                tier_counts = defaultdict(int)
              

                now = datetime.utcnow().strftime("%H:%M:%S")

                ready = 0
                awaiting_tick = 0
                inside_window = 0
                forming = 0
                blocked = 0
                total_oc_snapshots = 0
                oc_band_counter = defaultdict(int)
                drift_groups = defaultdict(int)
                steam_groups = defaultdict(int)
                blocked_reasons = defaultdict(int)
                conflict_flags = defaultdict(int)
                static_snapshot = self.get_static_snapshot()
                for key, runner in static_snapshot.items():
                    tier = runner.get("tier", "unknown")
                    tier_counts[tier] += 1
                    # Capture known signal conflicts
                    conflict_flag = runner.get("snapshot", {}).get("conflict_flag")
                    if conflict_flag:
                        conflict_flags[conflict_flag] += 1


                    runner_snapshot = runner.get("snapshot", {})
                    if not runner_snapshot:
                        continue

                    oc_snaps = runner_snapshot.get("oc_snapshots", {})
                    if not oc_snaps:
                        continue  # avoid crash on empty dict

                    current_oc = len([k for k in oc_snaps if k != "OC0"])
                    total_oc_snapshots += current_oc

                    direction = runner.get("direction_bias", "flat")
                    tick = runner.get("tick_pattern", "flat")

                    if current_oc < 2:
                        forming += 1
                        continue

                    latest_label = sorted(oc_snaps.keys())[-1]
                    try:
                        latest_oc_num = int(latest_label.replace("OC", ""))
                    except:
                        continue

                    for bp in self.KNOWN_BLUEPRINT_PATTERNS:
                        parts = bp.split("→")
                        if len(parts) != 4:
                            continue
                        dir_, oc_range, pattern, _ = parts
                        if dir_ != direction:
                            continue

                        start = int(oc_range.split("-")[0].replace("OC", ""))
                        end = int(oc_range.split("-")[1].replace("OC", ""))
                        if not (start <= current_oc <= end):
                            continue

                        inside_window += 1
                        if tick == pattern:
                            runner_is_ready = True
                            reasons_blocked = []

                            if tick not in ["drifted", "steamed"]:
                                runner_is_ready = False
                                reasons_blocked.append("tick not actionable")

                            if runner.get("range_low") is None or runner.get("range_high") is None:
                                runner_is_ready = False
                                reasons_blocked.append("missing range")

                            if len(runner.get("odds_history", [])) < 3:
                                runner_is_ready = False
                                reasons_blocked.append("insufficient odds history")

                            if key[0] not in self.active_tradable_markets:
                                runner_is_ready = False
                                reasons_blocked.append("market not tradable")

                            if not runner_is_ready:
                                blocked += 1
                                for reason in reasons_blocked:
                                    blocked_reasons[reason] += 1
                                if logger:
                                    logger.info(f"Runner {key} blocked at 'Ready to Fire' → {', '.join(reasons_blocked)}")

                            else:
                                ready += 1
                        else:
                            awaiting_tick += 1

                        group_label = f"OC{start}-OC{end}"
                        if direction == "drift":
                            drift_groups[group_label] += 1
                        elif direction == "steam":
                            steam_groups[group_label] += 1
                        oc_band_counter[group_label] += 1
                        break

                # 🕒 Session phase detection
                if total < 10:
                    phase = "BOOTING"
                    advice = "Waiting for markets to populate"
                elif signals_placed == 0 and forming > 0:
                    phase = "EARLY PHASE"
                    advice = "OC formation starting. No signals yet."
                elif signals_placed >= 1 and ready >= 1:
                    phase = "ACTIVE PHASE"
                    advice = "Blueprints triggering. Monitor placements."
                elif signals_placed > 0 and ready == 0 and forming == 0:
                    phase = "COOLDOWN"
                    advice = "Session tapering. Monitor remaining exposure."
                else:
                    phase = "IN TRANSIT"
                    advice = "Mixed signal readiness. Await clearer picture."

                print("\033c", end="")  # Clear terminal
                print(f"📈 BLUEPRINT SIGNAL STATUS REPORT (Updated: {now})\n")

                print("==========================================================")
                print("🔁 SESSION SNAPSHOT")
                print("==========================================================")
                try:
                    print(f"• Signals Generated:            {self.debug_signal_counter}")
                except AttributeError:
                    print(f"• Signals Generated:            0")
                print(f"• Runners Monitored:        {total}")
                print(f"• Memory Synced Runners:    {len(self.memory_read)}")
                print(f"• Signals Placed:           {signals_placed}")
                signals_tracked = sum(self._track_signal_counter.values())
                print(f"• Signals Tracked:          {signals_tracked}")

                print(f"• Blueprints Triggered:     {len(self.active_blueprints_today)} / {len(self.KNOWN_BLUEPRINT_PATTERNS)}")
                print(f"• Total OC Snapshots:       {total_oc_snapshots}")
                if oc_band_counter:
                    top_band = max(oc_band_counter.items(), key=lambda x: x[1])
                    print(f"• Top OC Band:              {top_band[0]} ({top_band[1]} runners)")
                else:
                    print(f"• Top OC Band:              N/A")
                print(f"• Completed Playbooks:       {len([pb for pb in self.live_playbooks.values() if pb.get('status') in ('success', 'poor_edge')])}")

                print("\n==========================================================")
                print("📦 STAGING BREAKDOWN")
                print("==========================================================")
                print(f"• Stage:   Forming (OCs < 2):            {forming}")
                print(f"• Stage:   In OC Window:                 {inside_window}")
                
                print(f"• Stage:   Awaiting Tick Pattern Match:  {awaiting_tick}")
                print(f"• Stage:   Ready to Fire:                {ready}")
                print(f"• Stage:   Blocked at Launch:            {blocked}")
                print(f"• Stage:   Active Ladders:               {len([l for l in self.active_ladders.values() if l.get('status') == 'active'])}")

                print("\n==========================================================")
                print("🛑 BLOCKED RUNNER DIAGNOSTICS")
                print("==========================================================")
                if not blocked_reasons and not conflict_flags:
                    print("• No blocked runners.")
                else:
                    for reason, count in blocked_reasons.items():
                        print(f"• ❌ {reason.title()}: {count}")
                    for reason, count in conflict_flags.items():
                        label = reason.replace('_', ' ').title()
                        print(f"• ⚠️ Conflict: {label} = {count}")


                print("\n==========================================================")
                print("📊 BLUEPRINT DIRECTIONAL GROUPS")
                print("==========================================================")
                print("→ DRIFT Candidates by OC Band:")
                for group, count in drift_groups.items():
                    print(f"   {group}:  {count}")
                print(f"   TOTAL:   {sum(drift_groups.values())}\n")

                print("→ STEAM Candidates by OC Band:")
                for group, count in steam_groups.items():
                    print(f"   {group}:  {count}")
                print(f"   TOTAL:   {sum(steam_groups.values())}")

                print("\n==========================================================")
                print("🕑 SESSION PHASE DETECTED")
                print("==========================================================")
                print(f"→ Status: {phase}")
                print(f"→ Focus:  {advice}")

                print("\n==========================================================")
                print("🏷️  RUNNER TIER DISTRIBUTION")
                print("==========================================================")
                print(f"• Active Runners:   {tier_counts.get('active', 0)}")
                print(f"• Passive Runners:  {tier_counts.get('passive', 0)}")
                print(f"• Ignored Runners:  {tier_counts.get('ignored', 0)}")
                print(f"• Unknown Runners:  {tier_counts.get('unknown', 0)}")

                from collections import Counter

                signal_type_counter = Counter()
                for signals in self.unmatched_signals.values():
                    for s in signals:
                        signal_type_counter[s.get("signal_type", "unknown")] += 1

                if signal_type_counter:
                    print("\n==========================================================")
                    print("📦 UNMATCHED SIGNAL TYPES")
                    print("==========================================================")
                    for sig_type, count in signal_type_counter.items():
                        print(f"• {sig_type}: {count}")

                print("\n==========================================================")
                print("📌 NEXT REPORT IN 10s...")
                print("==========================================================\n")

            except Exception as e:
                print(f"⚠️ Failed to generate signal report: {e}")
            time.sleep(10)

    def extract_blueprint_segments(self):
        segment_index = defaultdict(list)

        for full_key, data in self.KNOWN_BLUEPRINT_PATTERNS.items():
            sequence = full_key.split("→")
            for i in range(0, len(sequence) - 3, 3):
                current = "→".join(sequence[i:i+3])
                next_block = "→".join(sequence[i+3:i+6]) if i+6 <= len(sequence) else None
                segment_index[current].append({
                    "full_pattern": full_key,
                    "next_segment": next_block,
                    "meta": data.get("meta", {})
                })

        return segment_index

    def match_partial_segment(self, segment_key):
        return self.SEGMENT_DB.get(segment_key, [])

    def predict_next_segment(self, segment_key):
        matches = self.match_partial_segment(segment_key)
        if not matches:
            return None

        next_counts = Counter()
        total_conf = defaultdict(float)

        for m in matches:
            if m["next_segment"]:
                next_counts[m["next_segment"]] += 1
                total_conf[m["next_segment"]] += m["meta"].get("avg_confidence", 0.0)

        if not next_counts:
            return None

        best_next = next_counts.most_common(1)[0][0]
        avg_conf = round(total_conf[best_next] / next_counts[best_next], 3)
        direction = "lay_to_back" if "drift" in best_next else "back_to_lay"

        return {
            "next_segment": best_next,
            "confidence": avg_conf,
            "direction": direction,
            "segment_key": segment_key
        }

    
    def setup_daily_logger(self):
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
    
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        part = 1

        while True:
            log_file = os.path.join(log_dir, f"{today_str}_part{part}.log")
            if not os.path.exists(log_file):
                break
            if os.path.getsize(log_file) < 100_000:
                break
            part += 1

        logger = logging.getLogger("SignalDebugLogger")
        logger.setLevel(logging.INFO)

        if logger.hasHandlers():
            logger.handlers.clear()

        handler = logging.FileHandler(log_file, mode='a')
        formatter = logging.Formatter('%(asctime)s | %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        self._debug_logger = logger
        self._debug_log_path = log_file  # optional reference
        logger.info(f"🟢 Logging started: {log_file}")


    def refresh_pattern_pnl_from_api(self):
        session_token = get_session_token()
        settled_bets = fetch_settled_pattern_pnl(session_token)
        for bet in settled_bets:
            try:
                pattern_key = bet.get("customerOrderRef", "").split("_")[0]  # Assume base ref holds pattern key
                profit = float(bet.get("profit", 0.0))
                if pattern_key in self.KNOWN_BLUEPRINT_PATTERNS:
                    self.live_pattern_pnl[pattern_key]["trades"] += 1
                    self.live_pattern_pnl[pattern_key]["net_pnl"] += profit
            except Exception as e:
                continue
        print(f"📡 Synced pattern PnL using API fallback")

    def load_pattern_pnl_from_playbooks(self):
        """
        📦 Load historical playbook PnL from DB to restore memory across sessions.
        Builds self.live_pattern_pnl[pattern_key] with {trades, net_pnl}.
        """
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT pattern_key, COUNT(*), SUM(pnl)
                    FROM playbooks
                    WHERE result IN ('success', 'poor_edge')
                    GROUP BY pattern_key
                """)
                rows = cursor.fetchall()

                for pattern_key, trade_count, net_pnl in rows:
                    if not pattern_key:
                        continue
                    self.live_pattern_pnl[pattern_key]["trades"] = trade_count
                    self.live_pattern_pnl[pattern_key]["net_pnl"] = net_pnl or 0.0

            print(f"✅ Loaded historical PnL from playbooks ({len(rows)} patterns)")
        except Exception as e:
            print(f"⚠️ Failed to load PnL from playbooks: {e}")


    def track_live_pattern_pnl(self, pattern_key, pnl):
        if not pattern_key:
            return
        self.live_pattern_pnl[pattern_key]["trades"] += 1
        self.live_pattern_pnl[pattern_key]["net_pnl"] += pnl


    def assess_signal_health(self, market_id, selection_id):
        key = (market_id, selection_id)

        with self.memory_lock:
            if key not in self.memory_write:
                return {
                    "status": "unknown",
                    "failures": 0,
                    "last_result": None,
                    "flagged": False
                }

        outcomes = self.memory_write[key].get("signal_outcomes", [])


        if not outcomes:
            return {
                "status": "unknown",
                "failures": 0,
                "last_result": None,
                "flagged": False
            }

        failures = [o for o in outcomes if o["result"] != "success"]
        last_result = outcomes[-1]["result"] if outcomes else None
        flagged = len(failures) >= 2

        if flagged:
            print(f"🚫 [SignalMemory] Runner {selection_id} flagged due to repeated failures ({len(failures)} total).")

        return {
            "status": "flagged" if flagged else "healthy",
            "failures": len(failures),
            "last_result": last_result,
            "flagged": flagged
        }
    def start_new_session(self, balance=500.0):
        self.session_active = True
        self.starting_balance = balance
        self.current_balance = balance
        self.halt_triggered = False
        self.trade_log = []
        self.clean_exit_log = []
        self.current_stake = 10.0
        self.open_bets.clear()
        self.unmatched_signals.clear()
        with self.memory_lock:
            self.memory_write.clear()
            self.memory_read.clear()


    def analyse_runner(self, market_id, runners):
        from volatility_check import get_recent_odds, get_volatility_meta
        from price_math import get_tick_size

        # Step 1: Rank runners by current odds
        sorted_runners = sorted(
            [r for r in runners if isinstance(r.get("odds"), (int, float))],
            key=lambda x: x["odds"]
        )
        for idx, runner in enumerate(sorted_runners):
            runner["position"] = idx + 1  # 1-based rank

        for r in runners:
            selection_id = r["selectionId"]
            current_odds = r.get("odds")
            if not isinstance(current_odds, (int, float)):
                continue

            key = (market_id, selection_id)

            # Step 2: Tier determination
            if current_odds <= 12.0:
                tier = "active"
            elif current_odds <= 50.0:
                tier = "passive"
            else:
                tier = "ignored"

            # Step 3: Get odds history and volatility
            odds_list = get_recent_odds(market_id, selection_id) or [current_odds] * 6
            vol_meta = get_volatility_meta(market_id, selection_id, odds_list)

            # Step 4: Position ratio within range
            range_low = vol_meta["range_low"]
            range_high = vol_meta["range_high"]
            position_ratio = 0.5
            if isinstance(range_low, (int, float)) and isinstance(range_high, (int, float)) and range_low < range_high:
                position_ratio = round((current_odds - range_low) / (range_high - range_low + 0.0001), 3)
                position_ratio = max(0.0, min(1.0, position_ratio))

            # Step 5: Tick pattern
            tick_trail = r.get("tick_trail", [])
            tick_pattern = "flat"
            if len(tick_trail) >= 3:
                deltas = [tick_trail[i+1] - tick_trail[i] for i in range(len(tick_trail) - 1)]
                if all(d > 0 for d in deltas):
                    tick_pattern = "drifted"
                elif all(d < 0 for d in deltas):
                    tick_pattern = "steamed"
                elif any(abs(d) > 0.05 for d in deltas):
                    tick_pattern = "pingpong"

            direction_bias = (
                "drift" if tick_pattern == "drifted"
                else "steam" if tick_pattern == "steamed"
                else "flat"
            )

            # Step 6: Write into memory
            with self.memory_lock:
                if key not in self.memory_write:
                    self.memory_write[key] = {
                        "odds_history": deque(maxlen=12),
                        "last_updated": time.time(),
                        "range_low": range_low,
                        "range_high": range_high,
                        "anchor_odd": current_odds,
                        "tick_pattern": tick_pattern,
                        "position_ratio": r.get("position_ratio", 0.5),
                        "volatility": vol_meta["avg_volatility"],
                        "direction_bias": direction_bias,
                        "oc_snapshots": {},
                        "position": r.get("position"),
                        "minutes_to_post": self.get_minutes_to_post(market_id),
                        "tier": tier
                    }

                mem = self.memory_write[key]
                mem.update({
                    "range_low": range_low,
                    "range_high": range_high,
                    "tick_pattern": tick_pattern,
                    "direction_bias": direction_bias,
                    "position_ratio": r.get("position_ratio", 0.5),
                    "volatility": vol_meta["avg_volatility"],
                    "position": r.get("position"),
                    "tier": tier,
                    "last_updated": time.time()
                })


    def is_trend_forming(self, market_id, selection_id, direction, odds, range_high, range_low):
        key = (market_id, selection_id)
        runner = self.get_static_snapshot().get(key)


        if not runner:
            return False

        odds_history = list(runner.get("odds_history", []))
        if len(odds_history) < 3:
            return False

        deltas = [odds_history[i+1] - odds_history[i] for i in range(len(odds_history) - 1)]
        if direction == "lay_to_back" and all(d > 0 for d in deltas):
            return True
        elif direction == "back_to_lay" and all(d < 0 for d in deltas):
            return True

        return False

    def evaluate_signal_for_runner(self, market_id, selection_id):
        key = (market_id, selection_id)
        runner = self.get_static_snapshot().get(key)
        if not runner:
            return

        snapshot = copy.deepcopy(runner.get("snapshot", {}))
        if not snapshot:
            return

        oc_snaps = copy.deepcopy(snapshot.get("oc_snapshots", {}))

        if not oc_snaps or "OC0" not in oc_snaps:
            return

        tick_pattern = snapshot.get("tick_pattern", "flat")
        direction_bias = snapshot.get("direction_bias", "flat")
        odds = snapshot.get("tick_trail", [])[-1] if snapshot.get("tick_trail") else snapshot.get("anchor_odd", 0.0)
        direction = "lay_to_back" if tick_pattern == "drifted" else "back_to_lay"
        confidence = 0.7

        signal = {
            "marketId": market_id,
            "selectionId": selection_id,
            "odds": odds,
            "range_low": snapshot.get("range_low"),
            "range_high": snapshot.get("range_high"),
            "tick_pattern": tick_pattern,
            "position_ratio": snapshot.get("position_ratio", 0.5),
            "volatility": snapshot.get("volatility", 0.0),
            "scalp_direction": direction,
            "signal_type": "evaluate_signal",
            "confidence": confidence,
            "minutes_to_post": snapshot.get("minutes_to_post", 999),
            "forced": True,
        }

        self._evaluate_signal_counter = getattr(self, "_evaluate_signal_counter", defaultdict(int))
        self._last_evaluate_reported = getattr(self, "_last_evaluate_reported", 0)
        self.attempt_partial_blueprint_match(market_id, selection_id)
        self._evaluate_signal_counter[market_id] += 1
        total = sum(self._evaluate_signal_counter.values())
        if total != self._last_evaluate_reported:
            #print(f"📤 SIGNALS LAUNCHED: {len(self._evaluate_signal_counter)} markets, {total} runners")
            self._last_evaluate_reported = total

        self.track_unmatched_signal(signal)


    def ingest_market_snapshot(self, market_id, runners):
        """
        🔁 Called every 10s from evaluate_market_loop().
        Runners = live_runner_map[market_id], already has all enriched fields.
        """
        from datetime import datetime, timezone
        self.analyse_runner(market_id, runners)
        # ✅ Ensure live_markets is populated with latest runners
        if market_id not in self.live_markets:
            self.live_markets[market_id] = [r["selectionId"] for r in runners]
        else:
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
                    continue  # Don't evaluate signals for passive

                key = (market_id, selection_id)
                mem = self.memory_write.get(key)
                if not mem:
                    continue  # Skip if memory not available


                if not mem.get("snapshot"):
                    print(f"❌️ Snapshot not built for runner {selection_id} → skipping eval")
                    continue

                self.evaluate_signal_for_runner(market_id, selection_id)

                if len(mem["odds_history"]) < 3:
                    continue

                tick_pattern = mem.get("tick_pattern", "flat")
                oc_snaps = mem.get("oc_snapshots", {})
                anchor = mem.get("anchor_odd")
                range_low = mem.get("range_low")
                range_high = mem.get("range_high")
                confidence = 0.72

                if not all([anchor, range_low, range_high]):
                    continue

                if tick_pattern not in ["drifted", "steamed"]:
                    continue

                direction = "lay_to_back" if tick_pattern == "drifted" else "back_to_lay"

                signal = {
                    "marketId": market_id,
                    "selectionId": selection_id,
                    "odds": mem["odds_history"][-1],
                    "range_low": range_low,
                    "range_high": range_high,
                    "tick_pattern": tick_pattern,
                    "position_ratio": mem.get("position_ratio", 0.5),
                    "volatility": mem.get("volatility", 0.0),
                    "scalp_direction": direction,
                    "signal_type": "autonomous_scan",
                    "confidence": confidence,
                    "minutes_to_post": mem.get("minutes_to_post", 999),
                }
                self.memory_write[key]["snapshot"] = self.build_final_runner_snapshot(market_id, selection_id)

                print(f"📤 Attempting to track signal: {signal['marketId']} | {signal['selectionId']} | Tick: {signal['tick_pattern']} | OC Count: {len(oc_snaps)}")

                self.track_unmatched_signal(signal)
                print(f"✅ Signal handed off for unmatched tracking.")
                pattern_key = self.detect_blueprint_match(market_id, selection_id, signal["odds"], signal["scalp_direction"], range_low, range_high)

                if pattern_key:
                    print(f"🎯 Blueprint matched! {pattern_key} – Launching match.")
                    signal["blueprint_match"] = pattern_key
                    self.active_blueprints_today.add(pattern_key)
                    if hasattr(self, "_debug_logger"):
                        self._debug_logger.info(f"🎯 FULL blueprint matched: {pattern_key}")

                    signal["confidence"] = 0.81
                    signal["signal_type"] = "blueprint_match"
                    signal["scalp_ticks"] = 2
                    signal["forced"] = True
                    signal["customerOrderRef"] = f"{pattern_key}_{uuid.uuid4().hex[:6]}"
                    self.track_unmatched_signal(signal)


                signal["confidence"] = 0.81
                signal["signal_type"] = "blueprint_match"
                signal["scalp_ticks"] = 2
                signal["forced"] = True
                signal["customerOrderRef"] = f"{pattern_key}_{uuid.uuid4().hex[:6]}"
                self.track_unmatched_signal(signal)
          
           


                signal["confidence"] = 0.81
                signal["signal_type"] = "blueprint_match"
                signal["scalp_ticks"] = 2
                signal["forced"] = True
                signal["customerOrderRef"] = f"{pattern_key}_{uuid.uuid4().hex[:6]}"
                self.track_unmatched_signal(signal)

    

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

    def _attempt_next_ladder_rung(self, key, allow_partial=True):
        market_id, selection_id = key
        ladder = self.active_ladders.get(key)
        if not ladder:
            print(f"❌ No ladder config found for {key}")
            return
        # ✅ NEW DEFENSE: Don't proceed unless both entry + hedge matched
        if ladder["rung"] > 0 and not self._both_sides_matched(key):
            print(f"⛔ Ladder halted – waiting for both sides to match before rung {ladder['rung'] + 1} on {key}")
            return
        direction = ladder["direction"]
        current_odds = ladder["current_odds"]
        tick_size = ladder["tick_size"]
        rung = ladder["rung"]
        max_rungs = ladder.get("max_rungs", 3)
        stake = ladder["stake"]

        if rung >= max_rungs:
            print(f"✅ Ladder complete for {key} → Rungs: {rung}/{max_rungs}")
            ladder["status"] = "complete"
            return

        next_odds = current_odds - tick_size if direction == "lay_to_back" else current_odds + tick_size
        side = "BACK" if direction == "lay_to_back" else "LAY"

        print(f"🎯 Ladder rung {rung+1} for {key}: {side} @ {next_odds:.2f} | Stake: £{stake:.2f}")

        customer_order_ref = f"ladder_{uuid.uuid4().hex[:6]}"
        response = self.place_ladder_bet(market_id, selection_id, side, next_odds, stake, customer_order_ref)

        ladder["rung"] += 1
        ladder["current_odds"] = next_odds
        ladder.setdefault("rung_log", []).append({
            "rung": ladder["rung"],
            "odds": next_odds,
            "side": side,
            "status": "placed" if response else "failed",
            "timestamp": time.time()
        })


        # Optionally recursively continue to next rung
        if allow_partial:
            time.sleep(ladder.get("ladder_interval", 1))
            self._attempt_next_ladder_rung(key, allow_partial=True)

    def place_ladder_bet(self, market_id, selection_id, side, odds, stake, customer_order_ref):
        from utils.api_tools import place_bet  # or your correct API wrapper
        try:
            response = place_bet(
                market_id=market_id,
                selection_id=selection_id,
                side=side,
                odds=odds,
                stake=stake,
                customer_order_ref=customer_order_ref
            )
            print(f"✅ Ladder bet placed: {side} {selection_id} @ {odds} for £{stake}")
            return response
        except Exception as e:
            print(f"❌ Failed to place ladder bet: {e}")
            return None


    def should_continue_ladder(self, key):
        ladder = self.active_ladders.get(key, {})
        snapshot = self.memory_read.get(key, {}).get("snapshot", {})
        odds = snapshot.get("tick_trail", [])[-1] if snapshot.get("tick_trail") else None
        direction = ladder.get("direction")

        if not ladder or ladder.get("status") != "active" or not odds:
            return False

        # 1. Max rungs hit
        if ladder.get("rung", 0) >= ladder.get("max_rungs", 3):
            print(f"🚫 Ladder halted — max rungs hit for {key}")
            return False

        # 2. Boundary breach
        if odds > ladder.get("range_high", 999) or odds > ladder.get("tier_max", 999):
            print(f"🚫 Ladder halted — odds {odds} beyond boundary {ladder.get('range_high', 999)}")
            return False

        # 3. Trend reversal
        if self.detect_trend_reversal(key, direction, odds):
            print(f"🚫 Ladder halted — trend reversal detected for {key}")
            return False

        # 4. Timeout or staleness
        if ladder.get("rung_log"):
            last = ladder["rung_log"][-1]
            if time.time() - last.get("timestamp", 0) > 10:
                print(f"🚫 Ladder halted — stale rung >10s ago for {key}")
                return False

        return True

    def detect_trend_reversal(self, key, direction, odds):
        from price_math import get_tick_difference

        snapshot = self.memory_read.get(key, {}).get("snapshot", {})
        anchor = snapshot.get("anchor_odd")
        if not anchor or not odds:
            return False

        ticks_moved = get_tick_difference(anchor, odds)

        if direction == "lay_to_back" and odds > anchor and ticks_moved >= 3:
            return True
        if direction == "back_to_lay" and odds < anchor and ticks_moved >= 3:
            return True
        return False

        if not self.should_continue_ladder(key):
            self.active_ladders[key]["status"] = "halted"
            print(f"❌ Ladder stopped: condition failed for {key}")
            return


    def track_unmatched_signal(self, signal):
        self._track_signal_counter = getattr(self, "_track_signal_counter", defaultdict(int))
        self._last_track_reported = getattr(self, "_last_track_reported", 0)

        self._track_signal_counter[signal["marketId"]] += 1
        total = sum(self._track_signal_counter.values())
        if total != self._last_track_reported:
            #print(f"📆 SIGNALS TRACKED: {len(self._track_signal_counter)} markets, {total} runners")
            self._last_track_reported = total

        self.debug_signal_counter = getattr(self, "debug_signal_counter", 0) + 1
        key = (signal.get("marketId"), signal.get("selectionId"))
        # ✅ Inject memory if missing, using forced logic – but DO NOT skip filtering
        if key not in self.memory_write:
            print(f"🧠 Forcing memory entry for {key} from unmatched signal (simulation parity)")

            snapshot = {
                "odds_history": deque([signal["odds"]], maxlen=12),
                "range_low": signal["range_low"],
                "range_high": signal["range_high"],
                "tick_pattern": signal["tick_pattern"],
                "position_ratio": signal.get("position_ratio", 0.5),
                "volatility": signal.get("volatility", 0.0),
                "direction_bias": signal["scalp_direction"].replace("_", ""),
                "anchor_odd": signal["odds"],
                "oc_snapshots": {"OC1": {"odds": signal["odds"]}},
                "minutes_to_post": signal.get("minutes_to_post", 10),
                "last_updated": time.time(),
                "snapshot": {},  # this will get overwritten
                "tier": "active"
            }

            with self.memory_lock:
                self.memory_write[key] = snapshot
                self.memory_write[key]["snapshot"] = self.build_final_runner_snapshot(*key)
                self.memory_read[key] = self.memory_write[key]

            if signal["marketId"] not in self.active_tradable_markets:
                self.active_tradable_markets.append(signal["marketId"])

            write = self.memory_write.get(key, {})
            read = self.memory_read.get(key, {})

            # Tick pattern mismatch between memory read/write → downgrade confidence
            if read.get("tick_pattern") != write.get("tick_pattern"):
                print(f"⚠️ Tick mismatch: read={read.get('tick_pattern')} vs write={write.get('tick_pattern')}")
                signal["confidence"] = min(signal.get("confidence", 0.7), 0.65)
                signal["conflict_flag"] = "tick_pattern_mismatch"

            # Check if snapshot is stale (>8s old)
            if time.time() - write.get("last_updated", 0) > 8:
                print(f"⚠️ Stale snapshot detected for {key[1]}")
                signal["confidence"] = min(signal.get("confidence", 0.7), 0.6)
                signal["conflict_flag"] = "stale_memory"


        if signal.get("signal_type") == "partial_blueprint" and not signal.get("forced", False):
            #print(f"🚩 Skipping non-forced partial signal.")
            return

        if signal["marketId"] not in self.active_tradable_markets:
            #print(f"🚩 Skipping trade — {signal['marketId']} not in active tradable markets.")
            return

        # 🔄 Memory fallback (preserved from original)
        with self.memory_lock:
            if key not in self.memory_write:
                self.memory_write[key] = {
                    "odds_history": deque([signal["odds"]], maxlen=12),
                    "last_updated": time.time(),
                    "range_low": signal.get("range_low"),
                    "range_high": signal.get("range_high"),
                    "anchor_odd": signal.get("odds"),
                    "tick_pattern": signal.get("tick_pattern", "flat"),
                    "direction_bias": "flat",
                    "oc_snapshots": {"OC1": {"odds": signal["odds"]}},
                    "position": None
                }
            self.memory_read[key] = self.memory_write[key]  # 🔄 Direct sync for visibility
            # Store conflict flag in runner snapshot for reporting
            if "conflict_flag" in signal:
                self.memory_write[key]["conflict_flag"] = signal["conflict_flag"]
                self.memory_write[key].setdefault("snapshot", {})["conflict_flag"] = signal["conflict_flag"]


        # 🔹 Blueprint partial matching (restored)
        runner = self.memory_read.get(key, {})
        oc_snaps = runner.get("oc_snapshots", {})
        if not oc_snaps:
            return

        latest_oc = sorted(oc_snaps.keys(), key=lambda x: int(x.replace("OC", "")))[-1]
        current_oc = int(latest_oc.replace("OC", ""))
        direction_prefix = runner.get("direction_bias", "flat")

        matches = match_blueprint_partial(
            direction_prefix=direction_prefix,
            current_oc=current_oc,
            oc_snapshots=oc_snaps,
            tick_pattern=runner.get("tick_pattern", "flat"),
            unmatched_history=self.unmatched_signals.get(key, []),
            market_id=signal["marketId"],
            selection_id=signal["selectionId"],
            blueprint_patterns=self.KNOWN_BLUEPRINT_PATTERNS
        )

        if not matches:
            return

        top = matches[0]
        print(f"🤠 Partial match: {top['pattern_key']} (OC{current_oc})")

        signal.update({
            "tick_pattern": runner.get("tick_pattern", "flat"),
            "direction_bias": direction_prefix,
            "range_low": signal.get("range_low"),
            "range_high": signal.get("range_high"),
            "blueprint_match": top["pattern_key"],
            "pattern_key": top["pattern_key"],
            "confidence": top["confidence"],
            "scalp_direction": top["expected_action"],
            "signal_type": "partial_blueprint",
            "forced": True,
            "scalp_ticks": min(3, max(1, top["end_oc"] - current_oc)),
            "customerOrderRef": f"{top['pattern_key']}_{uuid.uuid4().hex[:6]}",
            "entry_odds": signal["odds"],
            "entry_side": "LAY" if top["expected_action"] == "lay_to_back" else "BACK",
            "stake": self.get_adaptive_stake()
        })

        save_signal_snapshot_to_live_bets(signal)
        self.active_blueprints_today.add(top["pattern_key"])

        # 🚀 Launch ladder trader
        if key in self.active_ladders:
            print(f"❌ Ladder already active for {key}. Ignoring duplicate.")
            return

        tick_size = self.get_tick_size(signal["odds"])
        self.active_ladders[key] = {
            "direction": signal.get("scalp_direction"),
            "confidence": signal["confidence"],
            "stake": signal["stake"],
            "tick_size": tick_size,
            "scalp_ticks": signal["scalp_ticks"],
            "ladder_interval": 1,
            "max_rungs": 3,
            "rung": 0,
            "status": "active",
            "range_high": signal.get("range_high", 999),
            "tier_max": 12.0,
            "current_odds": signal.get("odds"),
            "rung_log": []
        }
        print(f"📌 PASS: Signal {key} cleared filters — Ladder will now be simulated or placed")

        self._attempt_next_ladder_rung(key, allow_partial=True)

        # 🔗 Track to unmatched history (preserved)
        with self.unmatched_lock:
            if key not in self.unmatched_signals:
                self.unmatched_signals[key] = []
            self.unmatched_signals[key].append({
                "odds": signal["odds"],
                "confidence": signal["confidence"],
                "tick_pattern": signal["tick_pattern"],
                "timestamp": time.time(),
                "range_low": signal["range_low"],
                "range_high": signal["range_high"]
            })
            # ✅ Prevent undefined variables from crashing system
            self.active_ladders[key].setdefault("rung_log", [])
            self.active_ladders[key]["rung_log"].append({
                "rung": self.active_ladders[key].get("rung", 0),
                "odds": signal.get("odds", 0),
                "status": "matched",
                "timestamp": time.time()
            })


            self.live_playbooks[key]["steps"].append(f"ladder:{json.dumps(self.active_ladders[key]['rung_log'])}")
            

        self._learning_reverse_counter = getattr(self, "_learning_reverse_counter", defaultdict(int))
        self._learning_last_reported = getattr(self, "_learning_last_reported", 0)

        if signal["scalp_direction"] == "lay_to_back" and signal["tick_pattern"] == "came_in":
            self._learning_reverse_counter[signal["marketId"]] += 1
            total = sum(self._learning_reverse_counter.values())
            if total != self._learning_last_reported:
                print(f"📉 LEARNING REVERSED: {len(self._learning_reverse_counter)} markets, {total} runners")
                self._learning_last_reported = total


    # 🔄 Track Playbook Start
    def start_playbook(self, market_id, selection_id, pattern_key):
        group_id = str(uuid.uuid4())[:8]
        self.live_playbooks[(market_id, selection_id)] = {
            "playbook_group_id": group_id,
            "pattern_key": pattern_key,
            "opened_at": time.time(),
            "status": "open",
            "pnl": 0.0,
            "steps": [f"start:{pattern_key}"]
        }
        print(f"📘 New playbook started: {pattern_key} → ID {group_id}")

    # ✅ Finalise Playbook
    def finalise_playbook(self, market_id, selection_id, result, profit):
        key = (market_id, selection_id)
        if key not in self.live_playbooks:
            return

        playbook = self.live_playbooks[key]
        playbook["closed_at"] = time.time()
        playbook["status"] = result
        ladder_log = playbook.get("steps", [])
        for s in ladder_log:
            if s.startswith("ladder:"):
                try:
                    rung_data = json.loads(s.replace("ladder:", ""))
                    playbook["ladder_matched"] = sum(1 for r in rung_data if r.get("status") == "matched")
                    playbook["ladder_total"] = len(rung_data)
                except:
                    pass

        playbook["pnl"] = profit

        pattern = playbook.get("pattern_key")
        self.track_live_pattern_pnl(pattern, profit)

        print(f"📘 Playbook finished for {selection_id}: {pattern} | Result: {result} | PnL: £{profit:.2f}")


    def save_playbook_to_db(self, market_id, selection_id, playbook):
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS playbooks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        playbook_group_id TEXT,
                        marketId TEXT,
                        selectionId INTEGER,
                        pattern_key TEXT,
                        blueprint_match TEXT,
                        scalp_direction TEXT,
                        tick_pattern TEXT,
                        confidence REAL,
                        scalp_ticks INTEGER,
                        entry_odds REAL,
                        hedge_odds REAL,
                        entry_side TEXT,
                        hedge_side TEXT,
                        entry_stake REAL,
                        hedge_stake REAL,
                        entry_bet_id TEXT,
                        hedge_bet_id TEXT,
                        pnl REAL,
                        result TEXT,
                        opened_at TEXT,
                        closed_at TEXT,
                        customer_order_ref TEXT,
                        ladder_matched INTEGER,
                        ladder_total INTEGER
                    )
                """)
                cursor.execute("""
                    INSERT INTO playbooks (
                        playbook_group_id, marketId, selectionId, pattern_key, blueprint_match,
                        scalp_direction, tick_pattern, confidence, scalp_ticks,
                        entry_odds, hedge_odds, entry_side, hedge_side,
                        entry_stake, hedge_stake, entry_bet_id, hedge_bet_id,
                        pnl, result, opened_at, closed_at, customer_order_ref, ladder_matched, ladder_total
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    playbook.get("playbook_group_id"),
                    market_id,
                    selection_id,
                    playbook.get("pattern_key"),
                    playbook.get("blueprint_match"),
                    playbook.get("scalp_direction"),
                    playbook.get("tick_pattern"),
                    playbook.get("confidence"),
                    playbook.get("scalp_ticks"),
                    playbook.get("entry_odds"),
                    playbook.get("hedge_odds"),
                    playbook.get("entry_side"),
                    playbook.get("hedge_side"),
                    playbook.get("entry_stake"),
                    playbook.get("hedge_stake"),
                    playbook.get("entry_bet_id"),
                    playbook.get("hedge_bet_id"),
                    playbook.get("pnl"),
                    playbook.get("status"),
                    playbook.get("opened_at"),
                    playbook.get("closed_at"),
                    playbook.get("customer_order_ref"),
                    playbook.get("ladder_matched"),
                    playbook.get("ladder_total")
                ))
                conn.commit()
        except Exception as e:
            print(f"⚠️ Failed to save playbook: {e}")

    def record_matched_bet(self, market_id, selection_id, odds, stake, side, profit=None, pattern_key=None, signal=None, bet_id=None):
        key = (market_id, selection_id)
        if key in self.live_playbooks:
            self.live_playbooks[key].update({
                "blueprint_match": signal.get("blueprint_match"),
                "scalp_direction": signal.get("scalp_direction"),
                "tick_pattern": signal.get("tick_pattern"),
                "confidence": signal.get("confidence"),
                "scalp_ticks": signal.get("scalp_ticks"),
                "signal_type": signal.get("signal_type")
            })

            # 🧩 Add entry/hedge details to live_playbooks for full playbook save
            if side == "entry":
                self.live_playbooks[key]["entry_odds"] = odds
                self.live_playbooks[key]["entry_stake"] = stake
                self.live_playbooks[key]["entry_side"] = side
                self.live_playbooks[key]["entry_bet_id"] = signal.get("entry_bet_id") if signal else None
            elif side == "hedge":
                self.live_playbooks[key]["hedge_odds"] = odds
                self.live_playbooks[key]["hedge_stake"] = stake
                self.live_playbooks[key]["hedge_side"] = side
                self.live_playbooks[key]["hedge_bet_id"] = signal.get("hedge_bet_id") if signal else None

            if signal and "customerOrderRef" in signal:
                self.live_playbooks[key]["customer_order_ref"] = signal.get("customerOrderRef")

            if bet_id:
                 self.open_bets[key].append(side)
                 print(f"✅ Recorded matched {side} bet for {key} → Bet ID: {bet_id}")
            else:
                 print(f"⚠️ No {side}_bet_id provided — bet may not be active.")



        group_id = self.live_playbooks[key].get("playbook_group_id") if key in self.live_playbooks else None

        entry = {
            "timestamp": time.time(),
            "market_id": market_id,
            "selection_id": selection_id,
            "odds": odds,
            "stake": stake,
            "side": side,
            "profit": profit,
            "playbook_group_id": group_id
        }
        self.trade_log.append(entry)



        if profit is not None:
            self.current_balance += profit
            if pattern_key:
                self.track_live_pattern_pnl(pattern_key, profit)


    def should_halt_trading(self):
        if not self.session_active:
            self.halt_triggered = True
            return True
        if self.current_balance < self.starting_balance * 0.75:
            self.halt_triggered = True
            return True
        if sum(len(v) for v in self.open_bets.values()) >= 10:
            return True
        return False

    def can_place_more_bets(self, market_id, selection_id, pattern_key=None, confidence=None):
        key = (market_id, selection_id)
        trades = self.open_bets.get(key, [])

        # ✅ Count active unmatched trade pairs (i.e., where entry and hedge are not balanced)
        entry_count = trades.count("entry")
        hedge_count = trades.count("hedge")
        open_pairs = abs(entry_count - hedge_count)

        # ✅ Allow max 3 open/unmatched trades
        if open_pairs >= MAX_OPEN_BETS_PER_RUNNER:
            print(f"🚫 Max open trades reached for {selection_id}. Entry blocked.")
            return False

        # ✅ Allow if it's a blueprint and it passes confidence and profit gate
        if pattern_key and confidence is not None:
            if pattern_key in self.KNOWN_BLUEPRINT_PATTERNS:
                if confidence >= MIN_BLUEPRINT_CONFIDENCE:
                    return True

        # 🧪 Let new blueprints pass with small trades
        if trades < 3:
            print(f"🧪 Cold blueprint {pattern_key} – Allowing early low-risk trials.")
            return True

        # 🪙 Profit and confidence gates for STAKE adjustment only
        if net_pnl < 5 or confidence < 0.68:
            print(f"⚠️ Blueprint {pattern_key} has low edge (PnL £{net_pnl:.2f}, conf {confidence:.2f}). Stake will be reduced.")
            return True  # We allow, stake logic handles reduction

        return True

    # 🛑 Prevent runaway entry stacking: check if unmatched BACK orders exist
    def has_unmatched_back_orders(self, market_id, selection_id):
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT COUNT(*) FROM bet_orders
                    WHERE market_id = ? AND selection_id = ?
                    AND side = 'BACK' AND order_status = 'EXECUTABLE'
                """, (market_id, selection_id))
                row = cursor.fetchone()
                return row[0] > 0
        except Exception as e:
            print(f"⚠️ Order check failed for {selection_id}: {e}")
            return False

    def _both_sides_matched(self, key):
        entries = self.open_bets.get(key, [])
        return entries.count("entry") == entries.count("hedge") and len(entries) > 0

    def is_valid_entry_odds(self, odds):
        return isinstance(odds, (int, float)) and MIN_LIVE_ODDS_FOR_ENTRY <= odds <= MAX_LIVE_ODDS_FOR_ENTRY



    def is_exit_only_odds(self, odds):
        return isinstance(odds, (int, float)) and MAX_LIVE_ODDS_FOR_ENTRY < odds <= MAX_ABSOLUTE_ODDS

    def get_entry_type_for_odds(self, odds):
        if odds <= MAX_LIVE_ODDS_FOR_ENTRY:
            return "entry"
        elif odds <= MAX_ABSOLUTE_ODDS:
            return "exit_only"
        return None

    def estimate_liability(self, odds, stake, side):
        if side == "LAY" and isinstance(odds, (float, int)):
            return (odds - 1) * stake
        elif side == "BACK":
            return stake
        return 0.0

    def get_tick_difference(self, start_odds, current_odds):
        try:
            diff = abs(float(current_odds) - float(start_odds))
            if start_odds < 2.0:
                return round(diff / 0.01)
            elif start_odds < 3.0:
                return round(diff / 0.02)
            elif start_odds < 4.0:
                return round(diff / 0.05)
            elif start_odds < 6.0:
                return round(diff / 0.1)
            elif start_odds < 10.0:
                return round(diff / 0.2)
            elif start_odds < 20.0:
                return round(diff / 0.5)
            elif start_odds < 30.0:
                return round(diff / 1.0)
            elif start_odds < 50.0:
                return round(diff / 2.0)
            elif start_odds < 100.0:
                return round(diff / 5.0)
            else:
                return round(diff / 10.0)
        except:
            return 0

    def classify_tick_pattern_from_memory(self, market_id, selection_id):
        try:
            key = (market_id, selection_id)
            runner = self.get_static_snapshot().get(key)
            if not runner:
                return "flat"

            trail = list(runner.get("tick_trail", []))
            if len(trail) < 3:
                return "flat"

            deltas = [trail[i+1] - trail[i] for i in range(len(trail) - 1)]

            if all(d > 0 for d in deltas):
                return "drifted"
            elif all(d < 0 for d in deltas):
                return "steamed"
            elif any(abs(d) > 0.05 for d in deltas) and not all(abs(d) < 0.1 for d in deltas):
                return "pingpong"
            else:
                return "flat"
        except Exception as e:
            print(f"⚠️ Failed to classify tick pattern for {selection_id}: {e}")
            return "flat"

    def classify_tick_pattern_from_trail(self, trail):
        try:
            trail = list(trail)
            if len(trail) < 3:
                return "flat"

            deltas = [trail[i+1] - trail[i] for i in range(len(trail) - 1)]

            if all(d > 0 for d in deltas):
                return "drifted"
            elif all(d < 0 for d in deltas):
                return "steamed"
            elif any(abs(d) > 0.05 for d in deltas) and not all(abs(d) < 0.1 for d in deltas):
                return "pingpong"
            else:
                return "flat"
        except Exception as e:
            print(f"⚠️ classify_tick_pattern_from_trail error: {e}")
            return "flat"



    def should_force_exit_early(self, signal, current_odds):
        entry_odds = signal.get("odds")
        ticks_moved = self.get_tick_difference(entry_odds, current_odds)
        if ticks_moved >= 5:
            print(f"⚠️ Exiting early: Moved {ticks_moved} ticks from {entry_odds} to {current_odds}")
            return True
        return False

    def get_current_liability(self, market_id, selection_id):
        key = (market_id, selection_id)
        total = 0.0
        for bet_type in self.open_bets.get(key, []):
            if bet_type == "entry" or bet_type == "hedge":
                total += 10.0
        return total

    def can_afford_bet(self, market_id, selection_id, odds, stake, side):
        new_liability = self.estimate_liability(odds, stake, side)
        current_liability = self.get_current_liability(market_id, selection_id)
        remaining_balance = self.current_balance - current_liability
        return new_liability <= remaining_balance

    def is_top_six_runner(self, position):
        return isinstance(position, int) and position < 6

    def should_lay_to_back(self, signal):
        snapshot = self.memory_read.get((signal["marketId"], signal["selectionId"]), {}).get("snapshot", {})

        position = snapshot.get("position")

        return (
            signal.get("scalp_direction") == "lay_to_back"
            and self.is_top_six_runner(position)
            and signal.get("odds") <= MAX_LIVE_ODDS_FOR_ENTRY
        )

    def should_back_to_lay(self, signal):
        snapshot = self.memory_read.get((signal["marketId"], signal["selectionId"]), {}).get("snapshot", {})

        position = snapshot.get("position")

        mins = signal.get("minutes_to_post", 999)
        return (
            signal.get("scalp_direction") == "back_to_lay"
            and self.is_top_six_runner(position)
            and signal.get("tick_pattern") == "drifted"
            and 5 <= mins <= 10
        )

    def enrich(self, signal):
        key = (signal.get("marketId"), signal.get("selectionId"))
        snapshot = self.memory_read.get(key, {}).get("snapshot", {})

        # 🧠 Update tick pattern from live memory trail
        signal["tick_pattern"] = self.classify_tick_pattern_from_memory(signal["marketId"], signal["selectionId"])
        with self.memory_lock:
            self.memory_write[key]["tick_pattern"] = signal["tick_pattern"]


        if not snapshot:
            return signal

        signal["tick_pattern"] = snapshot.get("tick_pattern", "flat")
        signal["direction_bias"] = snapshot.get("direction_bias", "flat")
        signal["range_low"] = snapshot.get("range_low")
        signal["range_high"] = snapshot.get("range_high")

        anchor = snapshot.get("anchor_odd")
        oc_snaps = snapshot.get("oc_snapshots", {})
        latest_label = sorted(oc_snaps.keys())[-1] if oc_snaps else None
        latest_oc = oc_snaps.get(latest_label) if latest_label else None

        if anchor and latest_oc:
            move_type = snapshot.get("tick_pattern", "flat")
            direction = signal.get("scalp_direction")
            pattern_key = f"{snapshot.get('direction_bias', 'flat')}→{latest_label}→{move_type}→{direction}"
            if pattern_key in self.KNOWN_BLUEPRINT_PATTERNS:
                signal["blueprint_match"] = pattern_key

        # 🧠 Check recent outcomes to detect failure patterns
        outcomes = self.memory_write.get(key, {}).get("signal_outcomes", [])
        failures = [o for o in outcomes if o["result"] != "success"]
        if len(failures) >= 2:
            print(f"⚠️ Runner {key[1]} has failed {len(failures)} times — consider blocking.")

        return signal

    def detect_blueprint_match(self, market_id, selection_id, odds, direction, range_low, range_high):
        key = (market_id, selection_id)
        snapshot = self.memory_read.get(key, {}).get("snapshot", {})
        if not snapshot:
            return None

        anchor = snapshot.get("anchor_odd")
        oc_snaps = snapshot.get("oc_snapshots", {})
        bias = snapshot.get("direction_bias")

        if not anchor or not oc_snaps:
            return None

        latest_label = sorted(oc_snaps.keys())[-1] if oc_snaps else ""
        latest_oc = oc_snaps.get(latest_label)

        if not latest_oc:
            return None

        move_type = snapshot.get("tick_pattern", "flat")

        pattern_key = f"{bias}→{latest_label}→{move_type}→{direction}"

        return pattern_key if pattern_key in self.KNOWN_BLUEPRINT_PATTERNS else None

    def get_minutes_to_post(self, market_id):
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT marketStartTime FROM bets
                    WHERE marketId = ?
                    LIMIT 1
                """, (market_id,))
                row = cursor.fetchone()
                if row and row[0]:
                    from datetime import datetime, timezone
                    race_time = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    diff = (race_time - now).total_seconds() / 60.0
                    return round(diff, 1)
        except Exception as e:
            print(f"⚠️ Failed to fetch marketStartTime for {market_id}: {e}")
        return 999



    def resolve_runner_pnl(self, market_id, selection_id):
        # Check if both entry and hedge bets were recorded
        
        entry = None
        hedge = None
        for t in reversed(self.trade_log):
            if t["market_id"] == market_id and t["selection_id"] == selection_id:
                if t["side"] in ["LAY", "BACK"] and not entry:
                    entry = t
                elif t["side"] in ["LAY", "BACK"] and entry:
                    hedge = t
                    break

        if not entry:
            result = "error"
            reason = "No entry recorded"
        elif not hedge:
            result = "partial"
            reason = "Hedge bet missing"
        else:
            entry_odds = entry["odds"]
            hedge_odds = hedge["odds"]
            tick_diff = self.get_tick_difference(entry_odds, hedge_odds)
            result = "success" if tick_diff >= 1 else "poor_edge"
            reason = f"Entry @ {entry_odds} → Hedge @ {hedge_odds} ({tick_diff} ticks)"

        self.record_signal_outcome(
            market_id=market_id,
            selection_id=selection_id,
            result=result,
            explanation=reason
        )

        if entry and hedge:
            entry_profit = self.estimate_liability(entry["odds"], entry["stake"], entry["side"])
            hedge_profit = self.estimate_liability(hedge["odds"], hedge["stake"], hedge["side"])
            net_profit = hedge_profit - entry_profit
        else:
            net_profit = 0.0

        self.finalise_playbook(market_id, selection_id, result, net_profit)
        return True

    
    def record_signal_outcome(self, market_id, selection_id, result, explanation=None):
        key = (market_id, selection_id)
        with self.memory_lock:
            if "signal_outcomes" not in self.memory_write[key]:
                self.memory_write[key]["signal_outcomes"] = []

        outcome = {
            "timestamp": time.time(),
            "result": result,
            "explanation": explanation or "No explanation provided"
        }
        self.memory_write[key]["signal_outcomes"].append(outcome)

        print(f"""🧠 SIGNAL OUTCOME:
        🏇 Runner:      {selection_id}
        🎯 Market:      {market_id}
        📈 Result:      {result}
        📝 Explanation: {explanation}
        """)

    def build_final_runner_snapshot(self, market_id, selection_id):
        from price_math import calculate_tick_distance  # ✅ Make sure this is imported at the top
        """
        Builds a unified view of all known data for a runner.
        Combines DB data, memory, tick trail, volatility meta, etc.
        Called at the end of each ingest or signal eval loop.
        """
        key = (market_id, selection_id)
        runner = self.memory_write.get(key, {})

        # 🧠 Calculate range intelligence stats
        range_low = runner.get("range_low")
        range_high = runner.get("range_high")
        current_odds = runner.get("tick_trail")[-1] if runner.get("tick_trail") else runner.get("anchor_odd", 0.0)

        # Safe defaults
        position_ratio = 0.5
        ticks_from_low = 0
        ticks_from_high = 0
        ticks_available = 0

        if isinstance(current_odds, (int, float)) and isinstance(range_low, (int, float)) and isinstance(range_high, (int, float)) and range_low < range_high:
            position_ratio = round((current_odds - range_low) / (range_high - range_low + 0.0001), 3)
            position_ratio = max(0.0, min(1.0, position_ratio))
            ticks_from_low = self.get_tick_difference(range_low, current_odds)
            ticks_from_high = self.get_tick_difference(current_odds, range_high)
            ticks_available = self.get_tick_difference(range_low, range_high)



        snapshot = {
            "marketId": market_id,
            "selectionId": selection_id,
            "tick_pattern": runner.get("tick_pattern", "flat"),
            "direction_bias": runner.get("direction_bias", "flat"),
            "range_low": runner.get("range_low"),
            "range_high": runner.get("range_high"),
            "anchor_odd": runner.get("anchor_odd"),

            "position_ratio": position_ratio,
    

            "ticks_from_low": ticks_from_low,
            "ticks_from_high": ticks_from_high,
            "ticks_available": ticks_available,
            "is_near_top": ticks_from_high <= 2,
            "is_near_bottom": ticks_from_low <= 2,
            "is_breakout_imminent": ticks_from_high <= 1 or ticks_from_low <= 1,
            "volatility": runner.get("volatility", 0.0),
            "tier": runner.get("tier", "unknown"),
            "minutes_to_post": runner.get("minutes_to_post", 999),
            "tick_trail": list(runner.get("tick_trail", [])),
            "oc_snapshots": runner.get("oc_snapshots", {}),
            "position": runner.get("position"),
            "last_updated": runner.get("last_updated")
        }

        self.memory_write[key]["snapshot"] = snapshot
        return snapshot

    def attempt_partial_blueprint_match(self, market_id, selection_id):
        key = (market_id, selection_id)
        runner = self.get_static_snapshot().get(key)
        if not runner:
            return

        snapshot = runner.get("snapshot", {})
        oc_snaps = snapshot.get("oc_snapshots", {})
        if not oc_snaps or "OC0" not in oc_snaps or len(oc_snaps) < 2:
            return

        latest_oc = sorted(oc_snaps.keys(), key=lambda x: int(x.replace("OC", "")))[-1]
        current_oc = int(latest_oc.replace("OC", ""))
        direction = snapshot.get("direction_bias", "flat")
        tick_pattern = snapshot.get("tick_pattern", "flat")

        matches = match_blueprint_partial(
            direction_prefix=direction,
            current_oc=current_oc,
            oc_snapshots=oc_snaps,
            tick_pattern=tick_pattern,
            unmatched_history=self.unmatched_signals.get(key, []),
            market_id=market_id,
            selection_id=selection_id,
            blueprint_patterns=self.KNOWN_BLUEPRINT_PATTERNS
        )

        if not matches:
            return

        match = matches[0]
        signal = {
            "marketId": market_id,
            "selectionId": selection_id,
            "odds": snapshot.get("tick_trail", [])[-1] if snapshot.get("tick_trail") else snapshot.get("anchor_odd", 0.0),
            "range_low": snapshot.get("range_low"),
            "range_high": snapshot.get("range_high"),
            "tick_pattern": tick_pattern,
            "volatility": snapshot.get("volatility", 0.0),
            "position_ratio": snapshot.get("position_ratio", 0.5),
            "minutes_to_post": snapshot.get("minutes_to_post", 999),
            "confidence": match["confidence"],
            "blueprint_match": match["pattern_key"],
            "pattern_key": match["pattern_key"],
            "scalp_direction": match["expected_action"],
            "signal_type": "partial_blueprint",
            "scalp_ticks": match["scalp_ticks"],
            "customerOrderRef": f"{match['pattern_key']}_{uuid.uuid4().hex[:6]}",
            "forced": False
        }

        print(f"🤠 [PARTIAL MATCH] {selection_id} → {match['pattern_key']} | OC Band: {match['oc_band']} | Confidence: {match['confidence']}")
        self.track_unmatched_signal(signal)




    def record_trade_fault(self, market_id, selection_id, fault_type, context=None):
        key = (market_id, selection_id)
        with self.memory_lock:
            if "faults" not in self.memory_write[key]:
                self.memory_write[key]["faults"] = []
        fault = {
            "timestamp": time.time(),
            "type": fault_type,
            "context": context or "N/A"
        }
        self.memory_write[key]["faults"].append(fault)
        print(f"""🧠 TRADE FAULT:
        🏇 Runner:    {selection_id}
        🎯 Market:    {market_id}
        ❌ Fault:     {fault_type}
        📄 Context:   {context}
        """)

# ✅ PATCH: Finalize match_blueprint_partial integration
# Location: At the bottom of signal_memory_engine.py, before `signal_memory = SignalMemoryEngine()`

import sqlite3
import time
from datetime import datetime

def match_blueprint_partial(direction_prefix, current_oc, oc_snapshots, tick_pattern, unmatched_history, market_id, selection_id, blueprint_patterns):
    """
    Matches partial blueprints using live direction, OC stage, and micro trend memory.
    Returns best match (if any), and logs partial summaries into playbooks.
    """
    matches = []
    now = datetime.utcnow().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        # 🔧 Ensure new columns exist
        for col in ["report_type", "status", "oc_band", "customer_order_ref"]:
            try:
                cursor.execute(f"ALTER TABLE playbooks ADD COLUMN {col} TEXT")
            except:
                pass

        for pattern_key in blueprint_patterns:
            try:
                parts = pattern_key.split("→")
                if len(parts) != 4:
                    continue

                bp_direction, oc_range, bp_pattern, action = parts
                if bp_direction != direction_prefix:
                    continue
                if bp_pattern != tick_pattern:
                    continue

                start_oc = int(oc_range.split("-")[0].replace("OC", ""))
                end_oc = int(oc_range.split("-")[1].replace("OC", ""))

                oc_band = f"OC{start_oc}-OC{end_oc}"

                if not (start_oc <= current_oc <= end_oc):
                    status = "outside_oc_range"
                elif current_oc >= end_oc:
                    status = "no_time_remaining"
                else:
                    # ✅ Allow matching even if earlier OCs are missing, as long as current OC exists
                    available_ocs = [f"OC{i}" for i in range(start_oc, current_oc + 1) if f"OC{i}" in oc_snapshots]
                    missing_ocs = [f"OC{i}" for i in range(start_oc, current_oc + 1) if f"OC{i}" not in oc_snapshots]

                    if f"OC{current_oc}" not in oc_snapshots:
                        status = "missing_current_oc"
                    else:
                        # Match strength logic
                        recent_patterns = [x.get("tick_pattern") for x in unmatched_history[-4:]]
                        match_strength = sum(1 for p in recent_patterns if p == tick_pattern)
                        scalp_ticks = 3 if match_strength == 4 else 2 if match_strength >= 2 else 1

                        # 🧠 Confidence based on actual data coverage
                        completion = len(available_ocs) / (end_oc - start_oc + 1)
                        confidence = round(0.6 + (0.4 * completion), 2)

                        status = "ready_to_fire"
                        matches.append({
                            "pattern_key": pattern_key,
                            "confidence": confidence,
                            "expected_action": action,
                            "end_oc": end_oc,
                            "scalp_ticks": scalp_ticks,
                            "oc_band": oc_band
                        })

                        # Save match row
                        cursor.execute("""
                            INSERT INTO playbooks (
                                marketId, selectionId, pattern_key, blueprint_match,
                                scalp_direction, tick_pattern, confidence, scalp_ticks,
                                result, opened_at, report_type, status, oc_band
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            market_id, selection_id, pattern_key, pattern_key,
                            action, tick_pattern, confidence, scalp_ticks,
                            None, now, "partial_report", status, oc_band
                        ))
                        

                # Save non-match attempt for transparency
                cursor.execute("""
                    INSERT INTO playbooks (
                        marketId, selectionId, pattern_key, blueprint_match,
                        scalp_direction, tick_pattern, confidence, scalp_ticks,
                        result, opened_at, report_type, status, oc_band
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    market_id, selection_id, pattern_key, None,
                    action, tick_pattern, 0.0, 0,
                    None, now, "partial_report", status, oc_band
                ))
            except Exception as e:
                continue

        conn.commit()

    # Return best match only
    if not matches:
        return None
    matches.sort(key=lambda x: x["confidence"], reverse=True)
    return matches[0]

# 🔁 Singleton instance
signal_memory = SignalMemoryEngine()

def inject_live_market_data(market_id, runners):
    """
    🔁 Public injection point from market_loop or evaluate_market_loop.
    This is the only external call needed.
    """
    signal_memory.ingest_market_snapshot(market_id, runners)



# 👇 Register signal relay for external use
from upgrade_import_patch import signal_relay

def forward_signal_to_engine(signal):
    try:
        signal_memory.track_unmatched_signal(signal)
    except Exception as e:
        print(f"⚠️ Failed to forward signal: {e}")

# ✅ Register function pointer
import upgrade_import_patch
upgrade_import_patch.signal_relay = forward_signal_to_engine


# 📘 Export known blueprints

def calculate_ladder_win_rate(pattern_key):
    import sqlite3
    from config_paths import DB_PATH
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT SUM(CASE WHEN ladder_matched IS NOT NULL THEN ladder_matched ELSE 0 END),
                       SUM(CASE WHEN ladder_total IS NOT NULL THEN ladder_total ELSE 0 END)
                FROM playbooks
                WHERE pattern_key = ?
            """, (pattern_key,))
            matched, total = cursor.fetchone()
            if not total or total == 0:
                return 0.0
            return round(matched / total, 3)
    except Exception as e:
        print(f"⚠️ Failed to calculate ladder win rate for {pattern_key}: {e}")
        return 0.0

def flush_inplay_odds_to_db(self):
    from datetime import datetime
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat()
        today = datetime.utcnow().strftime("%Y-%m-%d")

        for market_id, runners in self.live_inplay_odds.items():
            for selection_id, odds_series in runners.items():
                if not odds_series:
                    continue
                cursor.execute("""
                    INSERT INTO inplay_odds_log (market_id, selection_id, date, start_time, end_time, odds_series)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    market_id, selection_id, today, now, now, json.dumps(odds_series)
                ))
        conn.commit()
    print(f"✅ Flushed in-play odds to DB.")



def get_known_blueprint_patterns():
    try:
        return signal_memory.KNOWN_BLUEPRINT_PATTERNS
    except Exception as e:
        print(f"⚠️ Could not get known blueprint patterns: {e}")
        return {}

