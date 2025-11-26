import sqlite3
import time
import json
from datetime import datetime
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engines.database_hijack_monitor import enqueue_write
from engines.config_paths import DB_PATH

def safe_json(obj):
    try:
        return json.loads(json.dumps(obj))
    except Exception:
        return {}

def write_oc_snapshot_to_db(market_id, selection_id, oc_label, latest_odds, band_odds):
    band_column = f"{oc_label}_band"
    latest_column = oc_label
    try:
        query = f"""
            UPDATE bets SET
                {latest_column} = ?,
                {band_column} = ?
            WHERE market_id = ? AND selection_id = ?
        """
        params = (
            str(latest_odds),
            json.dumps(safe_json(band_odds)),
            market_id,
            selection_id
        )
        enqueue_write(query, params)
    except Exception as e:
        print(f"❌ DB write failed for {market_id} | {selection_id} | {oc_label}: {e}")

def save_chapter_to_runner_story(market_id, selection_id, chapter_data):
    from datetime import datetime
    import json

    oc_label = chapter_data.get("oc_label", "")
    if not oc_label.startswith("OC"):
        print(f"⚠️ Invalid OC label: {oc_label}")
        return

    try:
        chapter_index = int(oc_label[2:])
        if not (0 <= chapter_index <= 20):
            print(f"⚠️ OC label out of range: {oc_label}")
            return
        chapter_column = f"chapter_{chapter_index}"

        insert_sql = f"""
            INSERT INTO runner_ram_snapshots (market_id, selection_id, {chapter_column}, last_updated)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(market_id, selection_id) DO UPDATE SET
                {chapter_column} = excluded.{chapter_column},
                last_updated = excluded.last_updated
        """
        params = (
            market_id,
            selection_id,
            json.dumps(chapter_data),
            datetime.utcnow().isoformat()
        )
        enqueue_write(insert_sql, params)

        from signal_memory_engine.story_metrics import increment_saved_chapters
        increment_saved_chapters()
    except Exception as e:
        print(f"❌ Failed to enqueue chapter: {e}")

def persist_oc_snapshots_to_db(market_id, selection_id, snapshot):
    oc_snaps = snapshot.get("oc_snapshots", {})
    if not oc_snaps:
        return

    columns = []
    values = []
    for key, value in oc_snaps.items():
        if key.startswith("OC") and ("band" in key or isinstance(value, (list, dict))):
            columns.append(f"{key}")
            values.append(json.dumps(safe_json(value)))
        elif isinstance(value, dict) and "odds" in value:
            columns.append(key)
            values.append(str(value["odds"]))

    if not columns:
        return

    col_str = ", ".join([f"{c} = ?" for c in columns])
    sql = f"UPDATE bets SET {col_str} WHERE market_id = ? AND selection_id = ?"
    values.extend([market_id, selection_id])

    enqueue_write(sql, values)

from engines.database_hijack_monitor import enqueue_write

def persist_oc_label_and_band(market_id, selection_id, label, odds):
    if not isinstance(odds, (float, int)):
        return

    query = f"""
        UPDATE bets SET
            {label} = ?,
            {label}_band = json_insert(
                COALESCE({label}_band, '[]'),
                '$[#]', ?
            )
        WHERE marketId = ? AND selectionId = ?
    """
    params = [odds, odds, market_id, selection_id]
    enqueue_write(query, params)

def persist_oc_band_to_bets(market_id, selection_id, label, odds):
    try:
        if not label or odds is None:
            return

        band_column = f"{label}_band"
        update_sql = f"""
            UPDATE bets SET
                {band_column} = json_insert(
                    COALESCE({band_column}, '[]'),
                    '$[#]',
                    ?
                )
            WHERE market_id = ? AND selection_id = ?
        """
        enqueue_write(update_sql, [odds, market_id, selection_id])

    except Exception as e:
        print(f"❌ persist_oc_band_to_bets failed for {market_id} {selection_id} {label}: {e}")

def save_snapshot_to_ram(market_id, selection_id, snapshot):
    try:
        snapshot = safe_json(snapshot)
        enqueue_write("""
            INSERT OR REPLACE INTO runner_ram_snapshots (market_id, selection_id, snapshot, last_updated)
            VALUES (?, ?, ?, ?)
        """, (
            market_id,
            selection_id,
            json.dumps(snapshot),
            time.time()
        ))
    except Exception as e:
        print(f"⚠️ Failed to save snapshot: {e}")

def save_snapshot_to_bets(market_id, selection_id, snapshot):
    oc_snaps = snapshot.get("oc_snapshots", {})
    for key, val in oc_snaps.items():
        if isinstance(val, list):
            val = json.dumps(val)
        enqueue_write(
            f"UPDATE bets SET {key} = ? WHERE market_id = ? AND selection_id = ?",
            [val, market_id, selection_id]
        )

def build_and_save_runner_chapter(market_id, selection_id, oc_label, snapshot):
    try:
        chapter_index = int(oc_label.replace("OC", ""))
        if not (0 <= chapter_index <= 20):
            return

        # Gather base metrics
        range_low = snapshot.get("range_low")
        range_high = snapshot.get("range_high")
        tick_pattern = snapshot.get("tick_pattern")
        volatility = snapshot.get("volatility")
        position_ratio = snapshot.get("position_ratio")
        minutes_to_post = snapshot.get("minutes_to_post")
        odds = snapshot.get("odds")
        anchor_odd = snapshot.get("anchor_odd")
        oc_band = snapshot.get("oc_band", [])

        oc_ticks = [anchor_odd]
        for i in range(1, 7):
            oc = snapshot.get(f"oc{i}")
            if isinstance(oc, (float, int)):
                oc_ticks.append(oc)

        # Build history from prior chapters
        history = []
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            for i in range(chapter_index):
                cursor.execute(f"""
                    SELECT chapter_{i} FROM runner_ram_snapshots
                    WHERE market_id = ? AND selection_id = ?
                """, (market_id, selection_id))
                row = cursor.fetchone()
                if row and row[0]:
                    try:
                        history.append(json.loads(row[0]))
                    except:
                        continue

        chapter_data = {
            "chapter": chapter_index,
            "timestamp": datetime.utcnow().isoformat(),
            "oc_label": oc_label,
            "minutes_to_post": minutes_to_post,
            "range_low": range_low,
            "range_high": range_high,
            "tick_pattern": tick_pattern,
            "volatility": volatility,
            "position_ratio": position_ratio,
            "odds": odds,
            "oc_ticks": oc_ticks,
            "oc_band": oc_band,
            "anchor_odd": anchor_odd,
            "history": history
        }

        save_chapter_to_runner_story(market_id, selection_id, chapter_data)

    except Exception as e:
        print(f"❌ Chapter build failed for {market_id} {selection_id} {oc_label}: {e}")
