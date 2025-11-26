# ✅ Memory → DB Mapping Sheet (RAM/STM/LTM)

# ---------------------------------------------------------
# 🧠 1. MEMORY WRITE (was: self.memory_write) → ram_snapshots
# ---------------------------------------------------------
# Structure:
#   key = (marketId, selectionId)
#   value = {
#       odds_history, tick_trail, oc_snapshots,
#       direction_bias, tick_pattern, anchor_odd,
#       range_low, range_high, position_ratio,
#       volatility, last_updated, position, minutes_to_post,
#       tier, snapshot
#   }

# ✅ Replace all self.memory_write[...] = {...} with
#     UPSERT INTO ram_snapshots

# ✅ Replace self.memory_read = deepcopy(memory_write) with:
#     SELECT * FROM ram_snapshots → self.memory_read = {}

# ✅ sync_memory_snapshots → becomes redundant once reading from DB


# ---------------------------------------------------------
# 🟢 2. UNMATCHED SIGNAL FINALISE (finalise_signal) → stm_live_signals
# ---------------------------------------------------------
# On every finalise_signal:
# ✅ INSERT INTO stm_live_signals (...columns...)
#     odds, tick_pattern, confidence, etc.
# ✅ customerOrderRef is primary unique key
# ✅ includes status = approved or pending

# This replaces signal_memory.unmatched_signals and signals tracked in memory


# ---------------------------------------------------------
# 📘 3. LADDER + TRADE RECORDS → ltm_playbooks
# ---------------------------------------------------------
# Structure per (marketId, selectionId):
#   playbook_group_id, pattern_key, blueprint_match
#   tick_pattern, confidence, scalp_ticks
#   entry_side, entry_odds, entry_stake, entry_bet_id
#   hedge_side, hedge_odds, hedge_stake, hedge_bet_id
#   pnl, result, opened_at, closed_at, customerOrderRef
#   ladder_matched, ladder_total

# ✅ Finalised after resolve_runner_pnl()
# ✅ Update partial playbooks on each rung using: UPDATE ... WHERE playbook_group_id = ?


# ---------------------------------------------------------
# 🔁 4. INTEGRATION ROUTING
# ---------------------------------------------------------
# All functions that previously relied on:
#     self.memory_write[...] or self.unmatched_signals[...] →
#     must now SELECT FROM ram_snapshots or stm_live_signals

# Ladder lookups (active_ladders): can be restored from ltm_playbooks ladder log
# Trend detection: use ram_snapshots.tick_trail + volatility
# Blueprint matching: still same logic, just gets data from DB


# ---------------------------------------------------------
# 📌 IMPLEMENTATION ORDER (to patch code)
# ---------------------------------------------------------
# 1. Replace memory_write updates → ram_snapshots UPSERT
# 2. Replace memory_read sync with SELECT from DB
# 3. Inject INSERT stm_live_signals during finalize_signal
# 4. Rewrite playbook lifecycle to write into ltm_playbooks
# 5. Confirm tick pattern, volatility, position_ratio → calculated and stored
# 6. All blueprint matching, ladder logic reads from DB not memory

# ✅ Once complete:
# - All memory calls become database-backed
# - Full recoverability of trading state
# - No more mutation issues or in-memory crashes

# Step 1: Helper Functions for Database Access (replacing memory_read/write)
# These will live in a central utils/db_memory_adapter.py

import sqlite3
import json
from config_paths import DB_PATH
from datetime import datetime

### --- RAM SNAPSHOTS (read/write) --- ###

def write_ram_snapshot(market_id, selection_id, snapshot: dict):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO ram_snapshots (
                    marketId, selectionId, odds, tick_pattern, direction_bias,
                    anchor_odd, range_low, range_high, position_ratio, volatility,
                    tick_trail, oc_snapshots, position, minutes_to_post, tier,
                    last_updated, snapshot
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                market_id,
                selection_id,
                snapshot.get("odds"),
                snapshot.get("tick_pattern"),
                snapshot.get("direction_bias"),
                snapshot.get("anchor_odd"),
                snapshot.get("range_low"),
                snapshot.get("range_high"),
                snapshot.get("position_ratio"),
                snapshot.get("volatility"),
                json.dumps(snapshot.get("tick_trail", [])),
                json.dumps(snapshot.get("oc_snapshots", {})),
                snapshot.get("position"),
                snapshot.get("minutes_to_post"),
                snapshot.get("tier"),
                datetime.utcnow().isoformat(),
                json.dumps(snapshot)
            ))
            conn.commit()
    except Exception as e:
        print(f"❌ DB Write Error (RAM Snapshot): {e}")


def read_ram_snapshot(market_id, selection_id):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT snapshot FROM ram_snapshots
                WHERE marketId = ? AND selectionId = ?
            """, (market_id, selection_id))
            row = cursor.fetchone()
            if row and row[0]:
                return json.loads(row[0])
    except Exception as e:
        print(f"❌ DB Read Error (RAM Snapshot): {e}")
    return {}


### --- STM (short-term memory) LIVE SIGNALS --- ###

def insert_stm_signal(signal):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO stm_live_signals (
                    marketId, selectionId, odds, range_low, range_high,
                    tick_pattern, direction_bias, confidence, drift, spread,
                    position_ratio, volatility, signal_type, stake,
                    session_token, timestamp, customerOrderRef, blueprint_match,
                    scalp_direction, scalp_ticks, forced, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal.get("marketId"),
                signal.get("selectionId"),
                signal.get("odds"),
                signal.get("range_low"),
                signal.get("range_high"),
                signal.get("tick_pattern"),
                signal.get("direction_bias"),
                signal.get("confidence"),
                signal.get("drift"),
                signal.get("spread"),
                signal.get("position_ratio"),
                signal.get("volatility"),
                signal.get("signal_type"),
                signal.get("stake"),
                signal.get("session_token"),
                datetime.utcnow().isoformat(),
                signal.get("customerOrderRef"),
                signal.get("blueprint_match"),
                signal.get("scalp_direction"),
                signal.get("scalp_ticks"),
                int(signal.get("forced", False)),
                signal.get("status")
            ))
            conn.commit()
    except Exception as e:
        print(f"❌ DB Insert Error (STM Signal): {e}")


def get_all_signals_from_stm():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM stm_live_signals")
            rows = cursor.fetchall()
            return rows
    except Exception as e:
        print(f"❌ DB Fetch Error (STM Signals): {e}")
    return []

"""

## ✅ Review Summary: Signal Path to Bet Placement

### 🧠 Signal Lifecycle Review (Updated Understanding)

| Stage | Current Flow | Ownership | Notes |
|-------|---------------|-----------|-------|
| 1. Signal is built | `evaluate_signal()` or `evaluate_signal_for_runner()` | Brain / Market Loop | Constructs signal dict with core metadata |
| 2. Signal is finalized | `finalise_signal(signal)` | Brain | This calls `launch_nextgen_thread(signal)` **and** passes it into `signal_memory` |
| 3. Signal is recorded | `track_unmatched_signal()` in `SignalMemoryEngine` | Signal Memory | This logs to internal memory dict **and** (confirmed) already logs into the database (`live_signals`) |
| 4. Signal becomes bet | `track_unmatched_signal()` launches the ladder / betting process | Signal Memory | This is **where the actual bets are placed**, via `_attempt_next_ladder_rung()` → `place_ladder_bet()` |

### ✅ Final Clarification
> Finalize **does** pass the signal to SignalMemory.
> SignalMemory **does** place the bet.
> Signal **is already** saved to the database **before** bet placement (via `save_signal_snapshot_to_live_bets`).

✅ **Conclusion**: Nothing is broken in this chain — we just need to **redirect reads/writes away from memory dicts to SQLite tables**, preserving the logic.

---

## 📋 Next Step: Line-by-Line Mapping Sheet
**Purpose**: Replace all usage of internal memory (`memory_write`, `memory_read`, `unmatched_signals`, `live_playbooks`) with DB equivalents (RAM, STM, LTM).

🎯 Proceeding now to generate that sheet in a new canvas...
"""