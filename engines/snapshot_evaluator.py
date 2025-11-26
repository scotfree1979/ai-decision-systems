import json
import time
from datetime import datetime, timedelta

from database_hijack_monitor import enqueue_write
from signal_memory_engine.ram_reader import safe_read
from upgrade_import_patch import get_session_token
from utils.api_tools import fetch_live_odds



def record_oc0_and_band():
    """Populate OC0-OC7 columns and store OC bands in runner_ram_snapshots."""
    # Ensure runner_ram_snapshots table exists
    enqueue_write(
        """
        CREATE TABLE IF NOT EXISTS runner_ram_snapshots (
            market_id TEXT,
            selection_id INTEGER,
            snapshot TEXT,
            last_updated REAL,
            PRIMARY KEY (market_id, selection_id)
        )
        """,
        [],
    )

    query = (
        "SELECT marketId, selectionId, anchor_odd, "
        "odds_check_1, odds_check_2, odds_check_3, odds_check_4, "
        "odds_check_5, odds_check_6, odds_check_7, timestamp "
        "FROM bets WHERE anchor_odd IS NOT NULL"
    )
    rows = safe_read(query)

    for row in rows:
        (
            market_id,
            selection_id,
            anchor,
            oc1,
            oc2,
            oc3,
            oc4,
            oc5,
            oc6,
            oc7,
            ts,
        ) = row

        oc_values = [anchor, oc1, oc2, oc3, oc4, oc5, oc6, oc7]
        oc_params = [v if v is not None else None for v in oc_values]

        enqueue_write(
            """
            UPDATE bets SET
                OC0 = ?, OC1 = ?, OC2 = ?, OC3 = ?,
                OC4 = ?, OC5 = ?, OC6 = ?, OC7 = ?
            WHERE marketId = ? AND selectionId = ?
            """,
            oc_params + [market_id, selection_id],
        )

        band_json = {}
        try:
            base_time = datetime.fromisoformat(ts)
        except Exception:
            base_time = None

        if base_time:
            for i in range(7):
                start = base_time + timedelta(seconds=i * 2)
                end = base_time + timedelta(seconds=(i + 1) * 2)
                band_rows = safe_read(
                    """
                    SELECT back FROM odds_snapshots
                    WHERE marketId = ? AND selectionId = ?
                      AND timestamp >= ? AND timestamp <= ?
                    ORDER BY timestamp
                    """,
                    [
                        market_id,
                        selection_id,
                        start.isoformat(),
                        end.isoformat(),
                    ],
                )
                band_json[f"OC{i}_band"] = [r[0] for r in band_rows]

        enqueue_write(
            """
            INSERT OR REPLACE INTO runner_ram_snapshots
                (market_id, selection_id, snapshot, last_updated)
            VALUES (?, ?, ?, ?)
            """,
            [
                market_id,
                selection_id,
                json.dumps(band_json),
                datetime.utcnow().timestamp(),
            ],
        )


def tick_and_record_oc_data():
    """Continuously fetch lay odds and group them into OC bands."""
    query = "SELECT marketId, selectionId, marketStartTime FROM bets"
    OC_TIMES = [80, 60, 40, 20, 10, 5, 0]

    # In-memory cache of band data per runner
    band_cache = {}

    while True:
        rows = safe_read(query)
        now = datetime.utcnow()
        session_token = get_session_token()

        for market_id, selection_id, start_time in rows:
            band_label = None
            lay = None
            try:
                start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                minutes_to_off = round((start_dt - now).total_seconds() / 60)

                for idx, oc_time in enumerate(OC_TIMES):
                    if minutes_to_off >= oc_time:
                        band_label = f"OC{idx}_band"
                        break

                odds = fetch_live_odds(session_token, market_id, selection_id) or {}
                lay = odds.get("lay")

                if band_label and lay is not None:
                    lay = round(lay, 2)
                    key = (market_id, selection_id)
                    band_json = band_cache.setdefault(key, {})
                    band_json.setdefault(band_label, []).append(lay)

            except Exception as e:
                print(f"❌ Error processing {market_id}-{selection_id}: {e}")
                continue

            print(
                f"📡 Tick: Market {market_id} | Runner {selection_id} | mto: {minutes_to_off} | band: {band_label} | lay: {lay}"
            )

        time.sleep(2)


