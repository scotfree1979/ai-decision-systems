
"""
🧠 Enhanced Blueprint Engine (Trend-Following Scalping Profiler + Profit Match + Pattern Ranking)
"""
PARSE_FAILURES = set()

import sqlite3
import json
from datetime import date, datetime, timedelta
from collections import defaultdict, Counter
from config_paths import DB_PATH
from price_math import calculate_tick_distance
from blueprint_csv_loader import get_matched_bet_ids_for_runner
from signal_memory_engine import get_known_blueprint_patterns
import ast


# Settings
STOP_THRESHOLD = 3
MIN_TREND_TICKS = 3
TICK_SIZES = [
    (1.01, 2.0, 0.01), (2.0, 3.0, 0.02), (3.0, 4.0, 0.05), (4.0, 6.0, 0.1),
    (6.0, 10.0, 0.2), (10.0, 20.0, 0.5), (20.0, 30.0, 1.0), (30.0, 50.0, 2.0),
    (50.0, 100.0, 5.0), (100.0, 1000.0, 10.0)
]

def detect_inplay_blueprints():
    import sqlite3
    from price_math import calculate_tick_distance
    from config_paths import DB_PATH
    from signal_memory_engine import get_known_blueprint_patterns

    print("🔍 Running In-Play Blueprint Detector...")
    trades = []

    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()

            # ✅ Create table if missing
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS inplay_ticks (
                    market_id TEXT,
                    selection_id INTEGER,
                    timestamp TEXT,
                    lay_odds REAL
                )
            """)
            conn.commit()

            cursor.execute("SELECT DISTINCT market_id, selection_id FROM inplay_ticks")
            rows = cursor.fetchall()

            KNOWN_BP = get_known_blueprint_patterns()

            for market_id, selection_id in rows:
                cursor.execute("""
                    SELECT lay_odds FROM inplay_ticks
                    WHERE market_id = ? AND selection_id = ?
                    ORDER BY timestamp ASC
                """, (market_id, selection_id))
                odds = [float(r[0]) for r in cursor.fetchall() if r[0] is not None]

                if len(odds) < 6:
                    continue

                for i in range(len(odds) - 1):
                    for j in range(i + 1, len(odds)):
                        entry, exit = odds[i], odds[j]
                        ticks_moved = calculate_tick_distance(entry, exit)
                        if ticks_moved < 3:
                            continue

                        slice = odds[i:j+1]
                        tick_pattern = classify_tick_pattern(entry, exit, slice)
                        move_dir = "drift" if exit > entry else "steam"
                        trade_type = "lay-to-back" if move_dir == "drift" else "back-to-lay"
                        pattern_key = f"{move_dir}→IP{i+1}-IP{j+1}→{tick_pattern}→{trade_type}"

                        blueprint_match = pattern_key if pattern_key in KNOWN_BP else None
                        meta = KNOWN_BP.get(pattern_key, {}).get("meta", {}) if blueprint_match else {}
                        win_rate = meta.get("win_rate", 0.0)
                        avg_conf = meta.get("avg_confidence", 0.0)

                        confidence = round(0.65 - (0.05 if tick_pattern == "flat" else 0), 3)

                        trades.append({
                            "runner": f"IP_{selection_id}",
                            "market_id": market_id,
                            "selection_id": selection_id,
                            "entry": entry,
                            "exit": exit,
                            "ticks_moved": ticks_moved,
                            "move": move_dir,
                            "tick_pattern": tick_pattern,
                            "trade_type": trade_type,
                            "pattern_key": pattern_key,
                            "blueprint_match": blueprint_match,
                            "confidence": confidence,
                            "win_rate": win_rate,
                            "avg_confidence": avg_conf,
                            "source": "inplay"
                        })

    except Exception as e:
        print(f"❌ In-play blueprint detection failed: {e}")

    return trades

def round_to_nearest_tick(odds):
    for lower, upper, tick in TICK_SIZES:
        if lower <= odds < upper:
            return round(round((odds - lower) / tick) * tick + lower, 2)
    return round(odds, 2)

def get_recent_odds_from_db(market_id, selection_id):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT odds_check_1, odds_check_2, odds_check_3,
                       odds_check_4, odds_check_5, odds_check_6
                FROM bets
                WHERE marketId = ? AND selectionId = ?
                ORDER BY placed_at DESC LIMIT 1
            """, (market_id, selection_id))
            row = cursor.fetchone()
            if row:
                return [round_to_nearest_tick(float(o)) for o in row if o is not None]
    except Exception as e:
        print(f"❌ Error fetching odds for {selection_id} in {market_id}: {e}")
    return []

def classify_tick_pattern(entry, exit, series):
    deltas = [series[i+1] - series[i] for i in range(len(series) - 1)]
    if all(d > 0 for d in deltas): return "drifted"
    if all(d < 0 for d in deltas): return "steamed"
    if any(abs(d) >= 2 for d in deltas): return "volatile"
    if any(d != 0 for d in deltas): return "pingpong"
    return "flat"

print("\n🔁 Running Enhanced Blueprint Builder")
runner_trades = []

try:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT marketId, selectionId, anchor_odd, horse_name, meta_json, placed_at
            FROM bets
            WHERE anchor_odd IS NOT NULL
        """)
        rows = cursor.fetchall()
except Exception as e:
    print(f"❌ DB error: {e}")
    rows = []

for market_id, selection_id, anchor_odd, horse_name, meta_json, placed_at in rows:
    try:
        odds_list = get_recent_odds_from_db(market_id, selection_id)
        odds_source = "full_odds_check" if len(odds_list) >= 4 else "partial_odds_check"
        if not odds_list:
            continue

        runner_name = horse_name or f"Runner_{selection_id}"
        anchor = round_to_nearest_tick(float(anchor_odd))
        oc_band = ""
        try:
            meta = json.loads(meta_json) if meta_json else {}
            oc_band = meta.get("runner", {}).get("oc_band", "")
        except: pass

        full_series = [anchor] + odds_list

        for i in range(len(full_series) - 1):
            for j in range(i + 1, len(full_series)):
                entry, exit = full_series[i], full_series[j]
                series_slice = full_series[i:j+1]
                ticks_moved = calculate_tick_distance(entry, exit)
                if ticks_moved < MIN_TREND_TICKS:
                    continue

                move_dir = "drift" if exit > entry else "steam"
                trade_type = "lay-to-back" if move_dir == "drift" else "back-to-lay"
                peak = max(entry, exit) if trade_type == "lay-to-back" else min(entry, exit)
                is_breakout = False
                try:
                    if oc_band:
                        low_str, high_str = oc_band.split(" → ")
                        oc_low, oc_high = float(low_str), float(high_str)
                        is_breakout = (exit > oc_high if trade_type == "lay-to-back" else exit < oc_low)
                except: pass

                reverse_ticks = calculate_tick_distance(peak, exit)
                if reverse_ticks >= STOP_THRESHOLD:
                    continue

                entry_tick_ratio, exit_tick_ratio = None, None
                try:
                    if oc_band:
                        low_str, high_str = oc_band.split(" → ")
                        oc_low, oc_high = float(low_str), float(high_str)
                        if oc_high != oc_low:
                            entry_tick_ratio = round((entry - oc_low) / (oc_high - oc_low), 3)
                            exit_tick_ratio = round((exit - oc_low) / (oc_high - oc_low), 3)
                except: pass

                pattern_key = f"{move_dir}→OC{i+1}-OC{j+1}→{classify_tick_pattern(entry, exit, series_slice)}→{trade_type}"

                KNOWN_BP = get_known_blueprint_patterns()
                blueprint_match = pattern_key if pattern_key in KNOWN_BP else None
                meta = KNOWN_BP.get(pattern_key, {}).get("meta", {}) if blueprint_match else {}
                win_rate = meta.get("win_rate", 0.0)
                avg_conf = meta.get("avg_confidence", 0.0)


                runner_trades.append({
                    "runner": runner_name,
                    "market_id": market_id,
                    "selection_id": selection_id,
                    "entry": entry,
                    "exit": exit,
                    "peak": peak,
                    "trend_ticks": ticks_moved,
                    "move": move_dir,
                    "trade_type": trade_type,
                    "oc_band": oc_band,
                    "source": odds_source,
                    "signal_number": i + 1,
                    "is_breakout": is_breakout,
                    "would_hit_stop": False,
                    "entry_tick_ratio": entry_tick_ratio,
                    "exit_tick_ratio": exit_tick_ratio,
                    "pattern_key": pattern_key,
                    "tick_pattern": classify_tick_pattern(entry, exit, series_slice),
                    "confidence": round(0.65 + (0.1 if is_breakout else 0) - (0.05 if classify_tick_pattern(entry, exit, series_slice) == "flat" else 0), 3),
                    "blueprint_match": blueprint_match,
                    "win_rate": win_rate,
                    "avg_confidence": avg_conf
                })

    except Exception as ex:
        print(f"❌ Runner error: {selection_id} → {ex}")

# 🔢 Pattern Meta Summary
pattern_summary = defaultdict(lambda: {"total": 0, "profitable": 0, "confidence_sum": 0.0})
for trade in runner_trades:
    key = trade["pattern_key"]
    pattern_summary[key]["total"] += 1
    pattern_summary[key]["confidence_sum"] += trade["confidence"]

# 📜 Export Known Blueprints
print("\n📜 Known Blueprint Patterns")
try:
    enriched_patterns = {}
    for key, summary in pattern_summary.items():
        enriched_patterns[key] = {
            "sequence": key.split("→"),
            "meta": {
                "total": summary["total"],
                "profitable": 0,
                "win_rate": 0.0,
                "avg_confidence": round(summary["confidence_sum"] / summary["total"], 3) if summary["total"] > 0 else 0.0

            }
        }
    with open("known_blueprints.py", "w") as f:
        f.write("KNOWN_BLUEPRINT_PATTERNS = ")
        json.dump(enriched_patterns, f, indent=2)
except Exception as e:
    print(f"⚠️ Failed to write blueprint patterns: {e}")


# ⬇️ Export (NEW FORMAT)
print(f"\n📦 Exporting {len(runner_trades)} trend trades to structured JSON format")

# 🔄 Convert list of trades into dict by pattern_key
pattern_dict_export = {}
for trade in runner_trades:
    key = trade.get("pattern_key")
    if key:
        if key not in pattern_dict_export:
            pattern_dict_export[key] = {
                "pattern_key": key,
                "trades": []
            }
        pattern_dict_export[key]["trades"].append(trade)

# ➕ Merge enriched meta data directly into each pattern key
for pattern_key, meta in enriched_patterns.items():
    if pattern_key in pattern_dict_export:
        pattern_dict_export[pattern_key]["meta"] = meta.get("meta", {})
    else:
        # Allow blueprint-only entries (if no trades matched this pattern today)
        pattern_dict_export[pattern_key] = {
            "pattern_key": pattern_key,
            "meta": meta.get("meta", {}),
            "trades": []
        }

# 📦 Export to JSON file
try:
    filename = f"blueprint_signals_{date.today().isoformat()}.json"
    with open(filename, "w") as f:
        json.dump(pattern_dict_export, f, indent=2)
    print(f"✅ Exported {len(pattern_dict_export)} blueprint patterns to {filename}")
except Exception as e:
    print(f"❌ Failed to write blueprint signals: {e}")


# 📊 Save meta summary separately
try:
    with open("blueprint_meta_summary.json", "w") as f:
        json.dump({k: v.get("meta", {}) for k, v in enriched_patterns.items()}, f, indent=2)
except Exception as e:
    print(f"⚠️ Failed to export meta summary: {e}")

# ✅ In-Play Blueprint Extension
try:
    inplay_trades = detect_inplay_blueprints()
    if inplay_trades:
        print(f"📥 Found {len(inplay_trades)} in-play blueprint candidates")
        runner_trades.extend(inplay_trades)
except Exception as e:
    print(f"❌ In-play blueprint extension failed: {e}")

def evaluate_blueprint_pnl():
    import ast
    pattern_scores = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0, "net_pnl": 0.0})
    july_9_scores = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0, "net_pnl": 0.0})
    yesterday_scores = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0, "net_pnl": 0.0})
    day_before_scores = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0, "net_pnl": 0.0})
    all_dates = defaultdict(lambda: defaultdict(float))

    today = datetime.utcnow().date()
    yesterday = today - timedelta(days=1)
    day_before = today - timedelta(days=2)
    pivot_date = date(2025, 7, 9)

    def safe_meta(raw_meta):
        try:
            meta = json.loads(raw_meta)
            return meta if isinstance(meta, dict) else {}
        except:
            try:
                meta = ast.literal_eval(raw_meta)
                return meta if isinstance(meta, dict) else {}
            except:
                return {}

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        for trade in runner_trades:
            pattern = trade.get("pattern_key")
            if not pattern:
                continue

            try:
                cursor.execute("""
                    SELECT meta_json, placed_at FROM bets
                    WHERE marketId = ? AND selectionId = ?
                    ORDER BY placed_at DESC LIMIT 1
                """, (trade["market_id"], trade["selection_id"]))
                row = cursor.fetchone()
                if not row:
                    continue
                raw_meta, placed_at = row
                meta_json = safe_meta(raw_meta)
                runner_name = meta_json.get("runner", {}).get("runnerName")
                if not runner_name or not placed_at:
                    continue
                settled_date = str(placed_at)[:10]
                settled_obj = datetime.strptime(settled_date, "%Y-%m-%d").date()

                cursor.execute("""
                    SELECT profit_loss FROM blueprint_pnl_log
                    WHERE horse_name = ? AND settled_date = ?
                """, (runner_name, settled_date))
                rows = cursor.fetchall()
                pnl_total = sum(float(r[0]) for r in rows if r[0] is not None)
            except Exception:
                continue

            if pattern:
                pattern_scores[pattern]["total"] += 1
                pattern_scores[pattern]["net_pnl"] += pnl_total
                all_dates[pattern][settled_date] += pnl_total
                if pnl_total > 0:
                    pattern_scores[pattern]["wins"] += 1
                else:
                    pattern_scores[pattern]["losses"] += 1

                if settled_obj >= pivot_date:
                    july_9_scores[pattern]["total"] += 1
                    july_9_scores[pattern]["net_pnl"] += pnl_total
                    if pnl_total > 0:
                        july_9_scores[pattern]["wins"] += 1
                    else:
                        july_9_scores[pattern]["losses"] += 1

                if settled_obj == yesterday:
                    yesterday_scores[pattern]["total"] += 1
                    yesterday_scores[pattern]["net_pnl"] += pnl_total
                    if pnl_total > 0:
                        yesterday_scores[pattern]["wins"] += 1
                    else:
                        yesterday_scores[pattern]["losses"] += 1

                if settled_obj == day_before:
                    day_before_scores[pattern]["total"] += 1
                    day_before_scores[pattern]["net_pnl"] += pnl_total
                    if pnl_total > 0:
                        day_before_scores[pattern]["wins"] += 1
                    else:
                        day_before_scores[pattern]["losses"] += 1

    def pct_change(new, old):
        if abs(old) < 0.01:
            return 0.0
        return round(((new - old) / abs(old)) * 100, 2)

    def print_snapshot(label, data, compare_to=None):
        print(f"\n📜 {label.upper()}")
        for pattern, stats in sorted(data.items(), key=lambda x: x[1]["net_pnl"], reverse=True):
            win_rate = stats["wins"] / stats["total"] if stats["total"] > 0 else 0.0
            delta_pct = 0.0
            arrow = ""
            if compare_to and pattern in compare_to:
                prev_pnl = compare_to[pattern]["net_pnl"]
                delta_pct = pct_change(stats["net_pnl"], prev_pnl)
                arrow = "🡅" if delta_pct > 0 else ("🡇" if delta_pct < 0 else "")
                print(f"🔹 {pattern} → PnL: £{stats['net_pnl']:.2f} | WinRate: {win_rate:.1%} | Trades: {stats['total']} | Δ {delta_pct:+.2f}% {arrow}")
            else:
                print(f"🔹 {pattern} → PnL: £{stats['net_pnl']:.2f} | WinRate: {win_rate:.1%} | Trades: {stats['total']}")

    print_snapshot("Blueprint Performance Snapshot", pattern_scores)
    print_snapshot("Performance Since July 9", july_9_scores, compare_to=pattern_scores)
    print_snapshot("Yesterday vs Day Before", yesterday_scores, compare_to=day_before_scores)

    for pattern, scores in pattern_scores.items():
        if pattern not in enriched_patterns:
            continue
        delta = pct_change(yesterday_scores[pattern]["net_pnl"], day_before_scores[pattern]["net_pnl"]) if pattern in yesterday_scores and pattern in day_before_scores else 0.0
        if pattern not in enriched_patterns:
            enriched_patterns[pattern] = {
                "sequence": pattern.split("→"),
                "meta": {}
            }

        enriched_patterns[pattern]["meta"].update({
            "net_pnl": scores["net_pnl"],
            "daily_delta_pct": delta
        })


    with open("known_blueprints.py", "w") as f:
        f.write("KNOWN_BLUEPRINT_PATTERNS = ")
        json.dump(enriched_patterns, f, indent=2)

evaluate_blueprint_pnl()
print("✅ Blueprint update complete.")

