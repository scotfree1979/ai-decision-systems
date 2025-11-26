# -----------------------------
# ✅ Core Fetch – get_markets() (Standalone Refactor)
# -----------------------------
import requests
import json
import logging
from datetime import datetime, timedelta
from engines.database_hijack_monitor import enqueue_write
from engines.upgrade_import_patch import get_session_token, set_session_token
from engines.daily_config import APP_KEY, DB_PATH

SNAPSHOT_DATA = {}

def should_run_fetch(conn):
    today = datetime.now().strftime("%Y-%m-%d")
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM system_flags WHERE key = 'last_fetch_date'")
    row = cursor.fetchone()

    if row is None or row[0] != today:
        cursor.execute("""
            INSERT INTO system_flags (key, value)
            VALUES ('last_fetch_date', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (today,))
        conn.commit()
        return True

    # ✅ Double-check actual volume of rows
    cursor.execute("""
        SELECT COUNT(*) FROM bets
        WHERE date = ? AND marketStartTime IS NOT NULL
    """, (today,))
    row_count = cursor.fetchone()[0]

    if row_count < 150:
        logging.warning(f"⚠️ Only {row_count} runners with marketStartTime found. Forcing re-fetch.")
        return True

    return False

def get_markets_and_insert():
    global SNAPSHOT_DATA

    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    if not should_run_fetch(conn):
        logging.info("✅ Fetch already run today. Using existing markets in database.")

        cursor = conn.cursor()
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        cursor.execute("""
            SELECT marketId, selectionId, horse_name, marketStartTime
            FROM bets
            WHERE date = ? AND marketStartTime IS NOT NULL
        """, (today_str,))
        rows = cursor.fetchall()

        if not rows:
            logging.warning("⚠️ No markets found in database despite fetch lock. Tool halted.")
            return []

        markets = {}
        for marketId, selectionId, horse_name, start_time in rows:
            if marketId not in markets:
                markets[marketId] = {
                    "marketId": marketId,
                    "marketStartTime": start_time,
                    "runners": []
                }
            markets[marketId]["runners"].append({
                "selectionId": selectionId,
                "runnerName": horse_name
            })

        return list(markets.values())


    logging.info("📡 Fetching live WIN markets with 7+ runners...")

    session_token = get_session_token()

    def try_fetch(token):
        return requests.post(
            url="https://api.betfair.com/exchange/betting/json-rpc/v1",
            headers={
                "X-Application": APP_KEY,
                "X-Authentication": token,
                "Content-Type": "application/json"
            },
            data=json.dumps([
                {
                    "jsonrpc": "2.0",
                    "method": "SportsAPING/v1.0/listMarketCatalogue",
                    "params": {
                        "filter": {
                            "eventTypeIds": ["7"],
                            "marketCountries": ["GB", "IE"],
                            "marketTypeCodes": ["WIN"],
                            "marketStartTime": {
                                "from": datetime.utcnow().strftime("%Y-%m-%dT00:00:00Z"),
                                "to": (datetime.utcnow() + timedelta(days=2)).strftime("%Y-%m-%dT00:00:00Z")
                            }
                        },
                        "marketProjection": ["RUNNER_DESCRIPTION", "MARKET_START_TIME", "EVENT"],
                        "sort": "FIRST_TO_START",
                        "maxResults": "50"
                    },
                    "id": 1
                }
            ])
        )

    try:
        response = try_fetch(session_token)

        if not session_token or response.status_code != 200:
            logging.warning(f"❌ Initial market fetch failed (status {response.status_code if response else 'none'}). Prompting for new token...")
            token = input("🔐 Enter your Betfair session token: ").strip()
            set_session_token(token)
            session_token = token
            response = try_fetch(session_token)

        if response.status_code != 200:
            logging.critical(f"❌ Market fetch failed after retry. Status code: {response.status_code}")
            raise SystemExit("🛑 Aborting: Valid session token not provided or rejected by Betfair API.")

        markets = response.json()[0].get("result", [])
        filtered = [m for m in markets if len(m.get("runners", [])) >= 7]
        logging.info(f"🎯 Filter complete: {len(filtered)} markets ready")

        for market in filtered:
            try:
                marketId = market["marketId"]
                start_time = datetime.fromisoformat(market["marketStartTime"].replace("Z", "+00:00"))
                market_name = market.get("marketName")
                event_name = market.get("event", {}).get("venue")
                race_name = market.get("event", {}).get("name")

                SNAPSHOT_DATA[marketId] = {
                    "start_time": start_time,
                    "market_name": market_name,
                    "event_name": event_name,
                    "race_name": race_name
                }
            except Exception as e:
                logging.warning(f"⚠️ Could not parse start time for {market.get('marketId')}: {e}")

        logging.info("📥 Inserting runners into bets table via hijack monitor...")
        for market in filtered:
            marketId = market["marketId"]
            market_name = market.get("marketName")
            event_name = market.get("event", {}).get("venue")
            race_name = market.get("event", {}).get("name")
            market_start_time = market.get("marketStartTime")
            date_str = market_start_time.split("T")[0] if market_start_time else None

            for runner in market.get("runners", []):
                selectionId = runner["selectionId"]
                horse_name = runner["runnerName"]
                meta_json = json.dumps({"runner": runner, "market": market})

                customer_order_ref = f"{marketId[-5:]}_{selectionId}_BASE"[:32]

                sql_insert = """
                    INSERT OR IGNORE INTO bets (
                        marketId, selectionId, horse_name, status, test_mode,
                        race_name, market_name, event_name, marketStartTime,
                        timestamp, date, meta_json, customerOrderRef
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                insert_params = (
                    marketId, selectionId, horse_name, "pending", "0",
                    race_name, market_name, event_name, market_start_time,
                    datetime.utcnow().isoformat(), date_str, meta_json, customer_order_ref
                )
                enqueue_write(sql_insert, insert_params)
                logging.info(f"📌 [Hijacked] Insert queued for {marketId}-{selectionId}")

                sql_update = """
                    UPDATE bets SET
                        race_name = ?, market_name = ?, event_name = ?
                    WHERE marketId = ? AND selectionId = ?
                """
                update_params = (race_name, market_name, event_name, marketId, selectionId)
                enqueue_write(sql_update, update_params)
                logging.info(f"📌 [Hijacked] Update queued for {marketId}-{selectionId}")

        for market in filtered:
            marketId = market["marketId"]
            for runner in market.get("runners", []):
                selectionId = runner["selectionId"]
                fallback_ref = f"{marketId[-5:]}_{selectionId}_BASE"[:32]
                enqueue_write("""
                    UPDATE bets
                    SET customerOrderRef = ?
                    WHERE marketId = ? AND selectionId = ? AND (customerOrderRef IS NULL OR customerOrderRef = '')
                """, (fallback_ref, marketId, selectionId))

        logging.info("✅ [Hijacked] All fetch-based runner rows submitted to queue.")
        return filtered

    except Exception as e:
        logging.error(f"❌ Error fetching markets: {e}")
        raise SystemExit("🛑 Fatal error during market fetch.")
