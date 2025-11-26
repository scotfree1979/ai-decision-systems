###1 TRADE STRUCTURE SETUP
# Group linked trades by signal: Entry, Hedge, Stop + Enhanced Range Logic

def build_trade_group(signal):
    from upgrade_import_patch import get_tick_size

    suffix = signal.get("customerOrderRef", _next_suffix())
    tick_size = get_tick_size(signal["odds"])
    ticks = signal.get("scalp_ticks", 2)

    return {
        "entry": {
            "side": "LAY" if signal["scalp_direction"] == "lay_to_back" else "BACK",
            "odds": signal["odds"],
            "stake": signal["stake"],
            "ref": f"{suffix}_E"
        },
        "hedge": {
            "side": "BACK" if signal["scalp_direction"] == "lay_to_back" else "LAY",
            "ticks": ticks,
            "ref": f"{suffix}_H"
        },
        "meta": {
            "group_id": suffix,
            "direction": signal["scalp_direction"],
            "marketId": signal["marketId"],
            "selectionId": signal["selectionId"],
            "tick_size": tick_size
        }
    }


###2 SMART SCALPING LOGIC – Final Refactor with Unified Logic

def execute_smart_scalp(signal):
    from bet_core import place_bet, check_bet_matched, cancel_bet
    from price_math import get_tick_size
    from upgrade_import_patch import get_session_token
    from nextgen_trading import active_scalps, save_bet_to_db, get_matched_amount, _next_suffix

    try:
        session_token = get_session_token()
        signal["session_token"] = session_token
        selection_id = signal["selectionId"]
        odds = signal["odds"]

        # Frequency and odds throttle
        if odds is None or odds > 20:
            print(f"❌ [HEADTRADER] Odds too high ({odds}). Skipping.")
            return

        if active_scalps[selection_id] >= 3:
            print(f"❌ [HEADTRADER] Too many active trades on runner {selection_id}. Skipping.")
            return

        # Blueprint awareness (optional future logic, can stub)
        if signal.get("blueprint") == "avoid":
            print("⚠️ [HEADTRADER] Skipping due to blueprint conflict.")
            return

        # Range logic integration
        range_high = signal.get("range_high")
        range_low = signal.get("range_low")
        ticks = signal.get("scalp_ticks", 2)
        tick_size = get_tick_size(odds)
        signal["tick_size"] = tick_size

        if range_high and range_low:
            ticks_to_high = int((range_high - odds) / tick_size)
            ticks_to_low = int((odds - range_low) / tick_size)

            if signal["scalp_direction"] == "lay_to_back" and ticks_to_high < ticks:
                print("⏳ Waiting for breakout above range before lay_to_back.")
                return
            if signal["scalp_direction"] == "back_to_lay" and ticks_to_low < ticks:
                print("⏳ Waiting for breakout below range before back_to_lay.")
                return

        # 🔁 Historical evolution placeholder (adaptive model)
        if signal.get("history_block") == "bad_zone":
            print("📉 Skipping: historical movement suggests poor timing.")
            return

        # 🔮 Blueprint reinforcement (early pattern match)
        if signal.get("pattern") in ["stall-drift", "spike-fade"] and signal.get("position") == "top":
            print("🧜️ Pattern detected. Holding for reversal.")
            return

        active_scalps[selection_id] += 1

        market_id = signal["marketId"]
        stake = signal.get("stake", 10.0)
        direction = signal["scalp_direction"]
        entry_side = "LAY" if direction == "lay_to_back" else "BACK"
        hedge_side = "BACK" if direction == "lay_to_back" else "LAY"
        hedge_odds = round(odds + ticks * tick_size, 2) if direction == "lay_to_back" else round(odds - ticks * tick_size, 2)

        suffix = _next_suffix()
        base_ref = f"HT_{suffix}"
        entry_ref = f"{base_ref}_E"
        hedge_ref = f"{base_ref}_H"

        entry_bet_id = place_bet(session_token, market_id, selection_id, stake, odds, entry_side, entry_ref)
        if not entry_bet_id:
            active_scalps[selection_id] -= 1
            return

        save_bet_to_db(market_id, selection_id, odds, stake, entry_side, entry_ref, entry_bet_id)

        matched_so_far = 0.0

        for i in range(10):
            time.sleep(1)
            matched_now = get_matched_amount(session_token, entry_bet_id)
            new_matched = round(matched_now - matched_so_far, 2)

            if new_matched >= 0.01:
                matched_so_far += new_matched
                print(f"[HEADTRADER] ✅ Matched £{new_matched:.2f} (Total: £{matched_so_far:.2f})")

                if entry_side == "LAY":
                    hedge_stake = round((odds * new_matched) / hedge_odds, 2)
                else:
                    hedge_stake = round((new_matched * hedge_odds) / odds, 2)

                hedge_sub_ref = f"{hedge_ref}_P{i}"
                hedge_bet_id = place_bet(session_token, market_id, selection_id, hedge_stake, hedge_odds, hedge_side, hedge_sub_ref, persistence_type="PERSIST")

                if hedge_bet_id:
                    save_bet_to_db(market_id, selection_id, hedge_odds, hedge_stake, hedge_side, hedge_sub_ref, hedge_bet_id)

        if matched_so_far == 0:
            print("[HEADTRADER] ❌ No match. Cancelling entry.")
            cancel_bet(session_token, entry_bet_id)

        active_scalps[selection_id] -= 1

    except Exception as e:
        print(f"💥 [HEADTRADER] Crash: {e}")
        active_scalps[selection_id] -= 1


###3 HEAD TRADER ENTRY (No Queue Required)

def launch_head_trader(signal):
    import threading
    threading.Thread(target=execute_smart_scalp, args=(signal,), daemon=True).start()
    print(f"🧠 HeadTrader launched signal: {signal.get('customerOrderRef')}")


###4 HEAD TRADER SIGNAL MONITOR LOOP

def launch_head_trader_signal_monitor(signal_stream):
    print("📱 HeadTrader signal monitor started...")
    for signal in signal_stream:
        launch_head_trader(signal)
        time.sleep(0.25)  # slight throttle between incoming signals

###5 BETFAIR SETTLEMENT (True PnL from API + fallback)

def settle_bets_from_api():
    import sqlite3
    import os
    import pandas as pd
    from upgrade_import_patch import get_session_token, APP_KEY
    import requests
    import json

    session_token = get_session_token()

    def fetch_cleared_bets(customer_refs):
        url = "https://api.betfair.com/exchange/betting/json-rpc/v1"
        headers = {
            "X-Application": APP_KEY,
            "X-Authentication": session_token,
            "Content-Type": "application/json"
        }

        payload = {
            "jsonrpc": "2.0",
            "method": "SportsAPING/v1.0/listClearedOrders",
            "params": {
                "betStatus": "SETTLED",
                "groupBy": "BET",
                "customerOrderRefs": customer_refs
            },
            "id": 1
        }

        try:
            response = requests.post(url, headers=headers, data=json.dumps(payload))
            response.raise_for_status()
            data = response.json()
            return data.get("result", {}).get("clearedOrders", [])
        except Exception as e:
            print(f"❌ Betfair API error: {e}")
            return []

    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT customerOrderRef, betfair_bet_id FROM bets WHERE status = 'bet_placed'")
            rows = cursor.fetchall()
            refs = [r[0] for r in rows if r[0]]

            if not refs:
                return

            for i in range(0, len(refs), 40):
                settled = fetch_cleared_bets(refs[i:i + 40])
                for bet in settled:
                    ref = bet.get("customerOrderRef")
                    profit = bet.get("profit", 0.0)
                    matched_time = bet.get("placedDate")
                    size = bet.get("sizeSettled", 0.0)
                    result = "won" if profit > 0 else "lost" if profit < 0 else "neutral"

                    cursor.execute("""
                        UPDATE bets SET pnl = ?, result = ?, status = 'settled',
                            matched_timestamp = ?, matched_liability = ?, unmatched_liability = 0
                        WHERE customerOrderRef = ?
                    """, (round(profit, 2), result, matched_time, size, ref))
                    print(f"✅ Settled {ref} with PnL: {round(profit,2)}")

            conn.commit()
    except Exception as e:
        print(f"❌ Error in API settlement loop: {e}")


###6 CSV FALLBACK SETTLEMENT (Optional final backup)

def load_csv_settlements(csv_folder="settlement_csv"):
    import os
    import pandas as pd
    import sqlite3

    try:
        for file in os.listdir(csv_folder):
            if not file.endswith(".csv"):
                continue

            path = os.path.join(csv_folder, file)
            df = pd.read_csv(path)
            with sqlite3.connect(DB_PATH) as conn:
                for _, row in df.iterrows():
                    ref = row.get("customerOrderRef")
                    betfair_id = row.get("bet_id")
                    pnl = row.get("pnl")
                    result = row.get("result")
                    ts = row.get("matched_timestamp")
                    if ref:
                        conn.execute("""
                            UPDATE bets SET pnl = ?, result = ?, status = 'settled',
                                matched_timestamp = ?, matched_liability = ?, unmatched_liability = 0
                            WHERE customerOrderRef = ?
                        """, (pnl, result, ts, row.get("stake", 0.0), ref))
                    elif betfair_id:
                        conn.execute("""
                            UPDATE bets SET pnl = ?, result = ?, status = 'settled',
                                matched_timestamp = ?, matched_liability = ?, unmatched_liability = 0
                            WHERE betfair_bet_id = ?
                        """, (pnl, result, ts, row.get("stake", 0.0), betfair_id))
            print(f"📥 Processed fallback CSV: {file}")
    except Exception as e:
        print(f"❌ CSV fallback error: {e}")
