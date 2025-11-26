# 📁 signal_memory_engine/oc_band_tracker.py
# ✅ OC Band Tracker: rewritten to use utils.api_tools and capture OC0 + OC0_band on startup

import time
import sys
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from dateutil import parser
import threading
from engines.database_hijack_monitor import enqueue_write

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # ensure engines path

from utils.api_tools import fetch_live_odds

from signal_memory_engine.ram_writer import persist_oc_label_and_band
from upgrade_import_patch import get_session_token
from config_paths import DB_PATH

def get_oc_label_for_minutes_to_off(mto):
    if mto > 80:
        return "OC0"
    elif mto > 60:
        return "OC1"
    elif mto > 40:
        return "OC2"
    elif mto > 20:
        return "OC3"
    elif mto > 10:
        return "OC4"
    elif mto > 5:
        return "OC5"
    elif mto > 0:
        return "OC6"
    elif -0.5 <= mto <= 0.5:
        return "OC7"
    else:
        offset = abs(int(mto)) + 8
        return f"OC{offset}" if offset <= 20 else None

def persist_direct_oc_label_and_band(cursor, market_id, selection_id, label, odds, write_band=True):
    try:
        if write_band:
            enqueue_write(3, f"""
                UPDATE bets SET
                    {label} = ?,
                    {label}_band = json_insert(
                        COALESCE({label}_band, '[]'),
                        '$[#]', ?
                    )
                WHERE marketId = ? AND selectionId = ?
            """, [odds, odds, market_id, selection_id])
        else:
            enqueue_write(3, f"""
                UPDATE bets SET
                    {label} = ?
                WHERE marketId = ? AND selectionId = ?
            """, [odds, market_id, selection_id])


    except Exception as e:
        print(f"❌ Direct OC write failed for {label} | {market_id} | {selection_id}: {e}")

def historical_oc_cleanup():
    def cleanup_task():
        today = datetime.utcnow().date().isoformat()
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()

                cursor.execute("SELECT value FROM system_flags WHERE key = 'oc_cleanup_done_date'")
                row = cursor.fetchone()
                already_ran_today = row and row[0] == today

                cursor.execute("""
                    SELECT COUNT(*) FROM bets
                    WHERE (
                        (anchor_odd IS NOT NULL AND OC0 IS NULL)
                        OR (odds_check_1 IS NOT NULL AND OC1 IS NULL)
                        OR (odds_check_2 IS NOT NULL AND OC2 IS NULL)
                        OR (odds_check_3 IS NOT NULL AND OC3 IS NULL)
                        OR (odds_check_4 IS NOT NULL AND OC4 IS NULL)
                        OR (odds_check_5 IS NOT NULL AND OC5 IS NULL)
                        OR (odds_check_6 IS NOT NULL AND OC6 IS NULL)
                    )
                """
                )
                missing = cursor.fetchone()[0]

                print(f"🔎 Historical rows still missing: {missing}")
                if missing == 0:
                    cursor.execute("""
                        INSERT OR REPLACE INTO system_flags (key, value)
                        VALUES ('oc_cleanup_done_date', ?)
                    """, (today,))
                    conn.commit()
                    return

                cursor.execute("""
                    SELECT marketId, selectionId, anchor_odd, odds_check_1, odds_check_2,
                           odds_check_3, odds_check_4, odds_check_5, odds_check_6
                    FROM bets
                    WHERE anchor_odd IS NOT NULL
                """)
                rows = cursor.fetchall()

                for market_id, selection_id, a0, oc1, oc2, oc3, oc4, oc5, oc6 in rows:
                    if a0 is not None:
                        persist_direct_oc_label_and_band(cursor, market_id, selection_id, "OC0", a0, write_band=False)
                    if oc1 is not None:
                        persist_direct_oc_label_and_band(cursor, market_id, selection_id, "OC1", oc1, write_band=False)
                    if oc2 is not None:
                        persist_direct_oc_label_and_band(cursor, market_id, selection_id, "OC2", oc2, write_band=False)
                    if oc3 is not None:
                        persist_direct_oc_label_and_band(cursor, market_id, selection_id, "OC3", oc3, write_band=False)
                    if oc4 is not None:
                        persist_direct_oc_label_and_band(cursor, market_id, selection_id, "OC4", oc4, write_band=False)
                    if oc5 is not None:
                        persist_direct_oc_label_and_band(cursor, market_id, selection_id, "OC5", oc5, write_band=False)
                    if oc6 is not None:
                        persist_direct_oc_label_and_band(cursor, market_id, selection_id, "OC6", oc6, write_band=False)

                conn.commit()
                print("✅ Background historical OC cleanup complete.")

        except Exception as e:
            print(f"❌ Failed historical OC cleanup: {e}")

    print("\n🧹 Performing historical OC cleanup...")
    print("⏳ Cleaning up in background...")
    threading.Thread(target=cleanup_task, daemon=True).start()

def run_oc_band_tracker():
    print("🟢 OC Band Tracker (Live) Started")
    historical_oc_cleanup()

    session_token = get_session_token()

    while True:
        try:
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            today_iso = today_start.isoformat()
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT marketId, selectionId, marketStartTime
                    FROM bets
                    WHERE anchor_odd IS NOT NULL
                      AND marketStartTime >= ?
                """, (today_iso,))
                rows = cursor.fetchall()

            for market_id, selection_id, start in rows:
                try:
                    if not start or not isinstance(start, str):
                        print(f"⚠️ Skipping {market_id}: marketStartTime is missing or invalid")
                        continue

                    start_time = parser.isoparse(start).astimezone(timezone.utc)
                    now_time = datetime.now(timezone.utc)
                    mto = (start_time - now_time).total_seconds() / 60.0
                except Exception as e:
                    print(f"❌ Error parsing marketStartTime for {selection_id}: {e}")
                    continue

                label = get_oc_label_for_minutes_to_off(mto)
                if not label:
                    continue

                odds_data = fetch_live_odds(session_token, market_id, selection_id)
                odds = odds_data.get("last_traded_price") or odds_data.get("lay") or odds_data.get("back")

                if not isinstance(odds, (float, int)):
                    continue

                persist_oc_label_and_band(market_id, selection_id, label, odds)



        except Exception as e:
            print(f"❌ Error in OC Band Tracker: {e}")

        time.sleep(2)
