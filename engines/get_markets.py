# -----------------------------
# ✅ Core Fetch – get_markets() (Standalone Refactor)
# -----------------------------
import requests
import os
import json
import logging
logging.basicConfig(level=logging.INFO, format="%(message)s")

from datetime import datetime, timedelta
from engines.database_hijack_monitor import enqueue_write
from engines.upgrade_import_patch import get_session_token, set_session_token
from engines.daily_config import APP_KEY, DB_PATH

# add near existing imports
from datetime import timezone
from engines.config_paths import bets_db  # canonical bets path

# 📍 TARGET: engines/get_markets.py
# 🔎 SEARCH: def _resolve_creds()
def _resolve_creds() -> tuple[str, str]:
    """
    Resolve Betfair credentials.
    AppKey = single source of truth from daily_config.
    SessionToken = may come from DB, GUI shim, or environment.
    """
    tok = None

    # 1) DB secrets (Step-1 writes these)
    try:
        from engines.session_secrets import load_betfair_creds
        _, tok = load_betfair_creds()  # ignore app key here
    except Exception:
        pass

    # 2) GUI shim (in-process)
    if not tok:
        try:
            from engines.upgrade_import_patch import get_session_token as _gst
            tok = (_gst() or "").strip()
        except Exception:
            pass

    # 3) Environment
    if not tok:
        tok = (os.getenv("BETFAIR_SESSION") or "").strip()

    # 4) AppKey always from daily_config
    try:
        from engines.daily_config import APP_KEY as DK_APP
        app = (DK_APP or "").strip()
    except Exception:
        app = ""

    return app, tok or ""

SNAPSHOT_DATA = {}

def _ensure_system_flags(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_flags(
          key TEXT PRIMARY KEY,
          value TEXT
        )
    """)
    conn.commit()

def should_run_fetch(conn):
    _ensure_system_flags(conn)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
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

def get_markets_and_insert(app_key: str | None = None, session_token: str | None = None):
    if not app_key or not session_token:
        app_key, session_token = _resolve_creds()
    global SNAPSHOT_DATA
    import sqlite3
    conn = sqlite3.connect(bets_db())  # instead of sqlite3.connect(DB_PATH)
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

    app_key, session_token = _resolve_creds()

    def try_fetch(token, app):
        return requests.post(
            url="https://api.betfair.com/exchange/betting/json-rpc/v1",
            headers={
                "X-Application": app,
                "X-Authentication": token,
                "Content-Type": "application/json",
                "Accept": "application/json",
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
                        "maxResults": "1000"  # room for a full card
                    },
                    "id": 1
                }
            ])
        )

    try:
        response = try_fetch(session_token, app_key)

        if (not session_token) or (not app_key) or response.status_code != 200:
            logging.warning(f"❌ Initial market fetch failed (HTTP {getattr(response,'status_code',None)}). Prompting for new token…")
            app_key, session_token = _resolve_creds()
            if not app_key or not session_token:
                raise RuntimeError("Missing Betfair credentials from Step-1. Paste token and click Step-1 again.")

        resp = try_fetch(session_token, app_key)
        resp.raise_for_status()

        data = resp.json()
        # Betfair JSON-RPC returns a list; first element has either "result" or "error"
        first = data[0] if isinstance(data, list) and data else {}
        if "error" in first:
            # surface the error so the GUI can show the messagebox and stop
            raise RuntimeError(f"API error: {first['error']}")
        markets = (first.get("result") or [])



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

if __name__ == "__main__":
    import logging
    from engines.config_paths import bets_db

    # Show INFO logs from this script
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        markets = get_markets_and_insert()
        n_markets = len(markets or [])
        n_runners = sum(len(m.get("runners") or []) for m in (markets or []))
        print(f"\n✅ GetMarkets complete | DB={bets_db()}")
        print(f"   Markets: {n_markets}  |  Runners: {n_runners}")
        if n_markets:
            # show a couple for confidence
            sample = markets[:3]
            for m in sample:
                mid = m.get("marketId")
                off = m.get("marketStartTime")
                rc  = len(m.get("runners") or [])
                print(f"   → {mid}  off={off}  runners={rc}")
        else:
            print("   (No markets returned; check token/app key or filters)")
    except SystemExit:
        raise
    except Exception as e:
        print(f"❌ GetMarkets crashed: {e}")

