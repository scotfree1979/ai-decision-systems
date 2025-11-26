# -----------------------------
# 💰 BUDGET MANAGER FINAL APPROVAL STAGE
# -----------------------------
def budget_manager():
    import sqlite3
    import logging
    from upgrade.upgrade_20250525 import DB_PATH, get_simulated_now
    from signal_monitor import bot_signal_queues
    from budget_manager import BudgetManager
    live_budget_mgr = BudgetManager(DB_PATH)

    logging.info("💰 Running budget manager for risk-evaluated signals")
    now = get_simulated_now()

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM bets WHERE status = 'risk_evaluated'
        """)
        rows = cursor.fetchall()

        for row in rows:
            try:
                # Updated mapping based on confirmed 38-column schema
                signal = {
                    "marketId": row[0],
                    "selectionId": row[1],
                    "stake": row[2],
                    "odds": row[3],
                    "bet_type": row[4],
                    "placed_at": row[5],
                    "status": row[6],
                    "matched_liability": row[7],
                    "unmatched_liability": row[8],
                    "pnl": row[9],
                    "liability": row[10],
                    "result": row[11],
                    "timestamp": row[12],
                    "matched_timestamp": row[13],
                    "betfair_bet_id": row[14],
                    "horse_name": row[15],
                    "strategy_name": row[16],
                    "signal_type": row[17],
                    "bot_name": row[18],
                    "date": row[19],
                    "time_signal": row[20],
                    "can_accept_liability": row[21],
                    "time_session": row[22],
                    "race_name": row[23],
                    "market_name": row[24],
                    "event_name": row[25],
                    "anchor_odd": row[26],
                    "odds_check_1": row[27],
                    "odds_check_2": row[28],
                    "odds_check_3": row[29],
                    "odds_check_4": row[30],
                    "odds_check_5": row[31],
                    "odds_check_6": row[32],
                    "customerOrderRef": row[33],
                    "bet_settled": row[34],
                    "marketStartTime": row[35],
                    "meta_json": row[36],
                    "test_mode": row[37]
                }

                result = live_budget_mgr.evaluate_budget(signal)
                new_status = "approved" if result else "rejected"

                cursor.execute("""
                    UPDATE bets SET status = ?, timestamp = ?
                    WHERE marketId = ? AND selectionId = ? AND customerOrderRef = ?
                """, (new_status, now.isoformat(), signal["marketId"], signal["selectionId"], signal["customerOrderRef"]))

                if result and signal["bot_name"] in bot_signal_queues:
                    bot_signal_queues[signal["bot_name"]].put(signal)
                    logging.info(f"✅ Sent to bot queue: {signal['bot_name']} [{signal['customerOrderRef']}]")
                else:
                    logging.warning(f"🚫 Signal {signal['customerOrderRef']} not approved or bot unknown")

            except Exception as e:
                logging.error(f"❌ Budget manager failed for signal {row[0]}: {e}")

        conn.commit()


# -----------------------------
# 📡 MARKET MONITOR SIGNALS STAGE
# -----------------------------
def market_monitor_signals():
    import sqlite3
    import logging
    from upgrade.upgrade_20250525 import DB_PATH, get_simulated_now

    logging.info("📡 Running market_monitor_signals for awaiting_signal rows")
    now = get_simulated_now()

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM bets
            WHERE status = 'awaiting_signal'
        """)
        rows = cursor.fetchall()

        for row in rows:
            signal = {
                "marketId": row[0],
                "selectionId": row[1],
                "horse_name": row[15],
                "time_signal": row[20],
                "customerOrderRef": row[33]
            }

            signal_type = None
            if signal["time_signal"] == "time_20":
                signal_type = "Market Drifter Signal"
            elif signal["time_signal"] == "time_10":
                signal_type = "Scalp Lay Signal"
            elif signal["time_signal"] == "time_5":
                signal_type = "GreenUp 5-Minute Signal"
            elif signal["time_signal"] == "time_0":
                signal_type = "InPlay Signal"

            if signal_type:
                try:
                    cursor.execute("""
                        UPDATE bets
                        SET signal_type = ?, status = 'signal_assigned'
                        WHERE marketId = ? AND selectionId = ? AND customerOrderRef = ?
                    """, (
                        signal_type,
                        signal["marketId"],
                        signal["selectionId"],
                        signal["customerOrderRef"]
                    ))
                    logging.info(f"📌 Assigned {signal_type} to {signal['horse_name']} [{signal['marketId']} / {signal['customerOrderRef']}]")
                except Exception as e:
                    logging.error(f"❌ Failed to assign signal_type for {signal['customerOrderRef']}: {e}")

        conn.commit()


# -----------------------------
# 🧠 SIGNAL MONITOR STAGE
# -----------------------------
def signal_monitor():
    import sqlite3
    import logging
    from datetime import datetime
    from upgrade.upgrade_20250525 import DB_PATH, get_simulated_now
    from market_monitor_signals import SIGNAL_BOT_MAPPING

    logging.info("🧠 Enriching and routing signal_assigned records")
    now = get_simulated_now()

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM bets WHERE status = 'signal_assigned'
        """)
        rows = cursor.fetchall()

        for row in rows:
            signal = {
                "marketId": row[0],
                "selectionId": row[1],
                "horse_name": row[15],
                "time_signal": row[20],
                "customerOrderRef": row[33],
                "signal_type": row[17],
                "event_name": row[25],
                "race_name": row[23],
                "marketStartTime": row[35]
            }

            bots = SIGNAL_BOT_MAPPING.get(signal["signal_type"], [])
            if not bots:
                logging.warning(f"⚠️ No bot targets found for signal type: {signal['signal_type']}")
                continue

            stake = 10.0 if signal["time_signal"] == "time_20" else 20.0 if signal["time_signal"] == "time_10" else 5.0 if signal["time_signal"] == "time_0" else None

            for bot in bots:
                new_ref = f"{signal['customerOrderRef']}_{bot[:3].upper()}"[:32]
                try:
                    cursor.execute("""
                        INSERT OR IGNORE INTO bets (
                            marketId, selectionId, stake, odds, bet_type, placed_at, status,
                            matched_liability, unmatched_liability, pnl, liability, result,
                            timestamp, matched_timestamp, betfair_bet_id, horse_name,
                            strategy_name, signal_type, bot_name, date, time_signal,
                            can_accept_liability, time_session, race_name, market_name, event_name,
                            anchor_odd, odds_check_1, odds_check_2, odds_check_3, odds_check_4,
                            odds_check_5, odds_check_6, customerOrderRef, bet_settled,
                            marketStartTime, meta_json, test_mode
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        signal["marketId"], signal["selectionId"], stake, None, None, None, "fully_enriched",
                        None, None, None, None, None,
                        now.isoformat(), None, None, signal["horse_name"],
                        signal["signal_type"], signal["signal_type"], bot, now.strftime('%Y-%m-%d'), signal["time_signal"],
                        None, None, signal["race_name"], None, signal["event_name"],
                        None, None, None, None, None,
                        None, None, new_ref, None,
                        signal["marketStartTime"], None, None
                    ))
                    logging.info(f"✅ Enriched + routed signal for {signal['horse_name']} to {bot}: {new_ref}")
                except Exception as e:
                    logging.error(f"❌ Enrichment failed for {signal['customerOrderRef']} → {bot}: {e}")

        conn.commit()


# -----------------------------
# 🧮 RISK MANAGER EVALUATION STAGE
# -----------------------------
def risk_manager():
    import sqlite3
    import logging
    from datetime import datetime
    from upgrade.upgrade_20250525 import DB_PATH, get_simulated_now

    logging.info("🔍 Running risk manager stage for fully_enriched signals")
    now = get_simulated_now()

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM bets WHERE status = 'fully_enriched'
        """)
        rows = cursor.fetchall()

        for row in rows:
            signal = {
                "marketId": row[0],
                "selectionId": row[1],
                "horse_name": row[15],
                "time_signal": row[20],
                "customerOrderRef": row[33]
            }

            try:
                cursor.execute("""
                    UPDATE bets
                    SET status = 'risk_evaluated', timestamp = ?
                    WHERE marketId = ? AND selectionId = ? AND customerOrderRef = ?
                """, (
                    now.isoformat(),
                    signal["marketId"],
                    signal["selectionId"],
                    signal["customerOrderRef"]
                ))
                logging.info(f"🔐 Risk manager flagged {signal['customerOrderRef']} as risk_evaluated")
            except Exception as e:
                logging.error(f"❌ Risk manager failed to update {signal['customerOrderRef']}: {e}")

        conn.commit()

# ---------------------------------------------
# ✅ ULTIMATE BET PLACER FUNCTIONS (FINALIZED)
# ---------------------------------------------

def place_basic_lay_bet(signal):
    from bet_core import place_bet, DB_PATH
    import sqlite3, logging, json
    session_token = signal.get("session_token")
    marketId = signal.get("marketId")
    selectionId = signal.get("selectionId")
    odds = signal.get("odds")
    stake = signal.get("stake", 2.0)
    ref = signal.get("customerOrderRef")

    bet_id = place_bet(session_token, marketId, selectionId, stake, odds, "LAY", ref)
    if bet_id:
        logging.info(f"✅ [BasicLay] {selectionId} @ {odds} (£{stake})")
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                UPDATE bets SET betfair_bet_id=?, status='placed', placed_at=?, odds=?
                WHERE customerOrderRef=?
            """, (bet_id, datetime.utcnow().isoformat(), odds, ref))
            conn.commit()
    else:
        logging.warning("🔴 [BasicLay] Lay bet failed")


def place_ladder_lay_bet(signal):
    from bet_core import place_bet, DB_PATH, calculate_tick, round_to_valid_odds
    import sqlite3, logging, json
    session_token = signal.get("session_token")
    marketId = signal.get("marketId")
    selectionId = signal.get("selectionId")
    base_odds = signal.get("odds")
    base_stake = signal.get("stake", 2.0)
    tick = calculate_tick(base_odds)
    ref_base = signal.get("customerOrderRef")[:28]

    for i in range(5):
        odds = round_to_valid_odds(base_odds - i * tick)
        ref = f"{ref_base}_L{i}"
        bet_id = place_bet(session_token, marketId, selectionId, base_stake, odds, "LAY", ref)
        if bet_id:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("""
                    UPDATE bets SET betfair_bet_id=?, status='placed', placed_at=?, odds=?
                    WHERE customerOrderRef=?
                """, (bet_id, datetime.utcnow().isoformat(), odds, ref))
                conn.commit()
            logging.info(f"🪜 [LadderLay] Leg {i+1}: {selectionId} @ {odds} (£{base_stake})")
        else:
            logging.warning(f"🔴 [LadderLay] Failed leg {i+1} @ {odds}")


def place_scalp_trade(signal):
    from bet_core import execute_scalp, DB_PATH
    import sqlite3, logging, json
    session_token = signal.get("session_token")
    marketId = signal.get("marketId")
    selectionId = signal.get("selectionId")
    stake = signal.get("stake", 20.0)
    odds = signal.get("odds")
    direction = signal.get("scalp_direction", "lay_to_back")
    market_time = signal.get("marketStartTime")
    ref = signal.get("customerOrderRef")

    execute_scalp(session_token, marketId, selectionId, stake, odds, direction, market_time)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            UPDATE bets SET status='placed', placed_at=?, odds=?
            WHERE customerOrderRef=?
        """, (datetime.utcnow().isoformat(), odds, ref))
        conn.commit()
    logging.info(f"💹 [Scalp] Logged {direction} trade for {selectionId} @ {odds}")


def place_inplay_lay_bet(signal):
    from bet_core import place_bet, DB_PATH
    import sqlite3, logging, json
    session_token = signal.get("session_token")
    marketId = signal.get("marketId")
    selectionId = signal.get("selectionId")
    odds = signal.get("odds")
    stake = signal.get("stake", 5.0)
    ref = signal.get("customerOrderRef")

    bet_id = place_bet(session_token, marketId, selectionId, stake, odds, "LAY", ref)
    if bet_id:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                UPDATE bets SET betfair_bet_id=?, status='placed', placed_at=?, odds=?
                WHERE customerOrderRef=?
            """, (bet_id, datetime.utcnow().isoformat(), odds, ref))
            conn.commit()
        logging.info(f"🏇 [InPlayLay] Bet placed for {selectionId} @ {odds} (£{stake})")
    else:
        logging.warning("🔴 [InPlayLay] Failed")


def place_greenup_exit(signal):
    from green_up import green_up
    session_token = signal.get("session_token")
    marketId = signal.get("marketId")
    green_up(session_token, marketId, target_profit=1.0)
    logging.info(f"🟩 [GreenUp] Triggered for market {marketId}")


# ---------------------------------------------
# 🧾 FINAL BET SETTLEMENT MONITOR
# ---------------------------------------------

def bet_settlement_monitor():
    import sqlite3
    from bet_core import check_bet_matched, DB_PATH

    logging.info("🕵️‍♂️ [BetSettler] Starting final match-check for unsettled bets")

    def get_open_bets():
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT customerOrderRef, betfair_bet_id, marketId, selectionId FROM bets
                WHERE status = 'placed' AND betfair_bet_id IS NOT NULL
            """)
            return cur.fetchall()

    while True:
        open_bets = get_open_bets()
        for ref, bet_id, marketId, selectionId in open_bets:
            session_token = os.environ.get("SESSION_TOKEN")
            if check_bet_matched(session_token, bet_id):
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute("""
                        UPDATE bets SET status='matched', matched_timestamp=?
                        WHERE customerOrderRef=?
                    """, (datetime.utcnow().isoformat(), ref))
                    conn.commit()
                logging.info(f"✅ [BetSettler] Bet matched and confirmed: {ref} / {bet_id}")
        time.sleep(60)

# ⏳ Settler checks run every 60s for up to 30 minutes per race

