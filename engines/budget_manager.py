# ✅ UPGRADE 20250525 – CORE BudgetManager (Fixed horse_name reference)

from upgrade_import_patch import *
import logging
import sqlite3
from enhanced_real_time_bot_feed import log_risk_management
from upgrade.upgrade_20250525 import DB_PATH, get_simulated_now
from signal_monitor import bot_signal_queues

class BudgetManager:
    def __init__(self, db_path="bot_budgets.db"):
        self.db_path = db_path
        self.available_budget = 0

    def update_available_budget(self, available_budget):
        self.available_budget = available_budget
        logging.info(f"✅ Budget explicitly set: £{available_budget}")

    def get_bot_status_from_db(self, bot_name):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT SUM(matched_liability), SUM(pnl) FROM bets WHERE bot_name=?", (bot_name,))
        data = cursor.fetchone()
        conn.close()
        return data if data else (0, 0)

    def handle_rejected_signal(self, signal, reason):
        log_risk_management(
            "budget_rejection",
            signal.get("strategy_name", "Unknown"),
            f"{reason} | Bot: {signal['bot_name']}, Market: {signal['marketId']}, Odds: {signal['odds']}, Stake: £{signal['stake']}"
        )
        return None

    def evaluate_budget(self, signal):
        bot_name = signal['bot_name']
        stake = signal['stake']
        odds = signal['odds']
        liability = stake * (odds - 1)
        signal_type = signal.get('signal_type', '')
        marketId = signal['marketId']

        fields = [
            'marketId', 'selectionId', 'stake', 'odds', 'bet_type', 'placed_at', 'status',
            'matched_liability', 'unmatched_liability', 'pnl', 'liability', 'result',
            'timestamp', 'matched_timestamp', 'betfair_bet_id', 'horse_name',
            'strategy_name', 'bot_name', 'date', 'can_accept_liability',
            'time_session', 'race_name', 'market_name', 'event_name', 'anchor_odd',
            'odds_check_1', 'odds_check_2', 'odds_check_3', 'odds_check_4',
            'odds_check_5', 'odds_check_6', 'customerOrderRef', 'bet_settled',
            'marketStartTime', 'meta_json', 'test_mode'
        ]

        for field in fields:
            if field not in signal:
                signal[field] = None

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT MAX(liability) FROM bets
            WHERE bot_name=? AND marketId=?;
        """, (bot_name, marketId))
        highest_existing_liability = cursor.fetchone()[0] or 0

        max_race_liability = max(highest_existing_liability, liability)

        cursor.execute("""
            SELECT SUM(highest_liability) FROM (
                SELECT MAX(liability) AS highest_liability FROM bets
                WHERE bot_name=?
                GROUP BY marketId
            );
        """, (bot_name,))
        total_bot_liability = cursor.fetchone()[0] or 0

        conn.close()

        global_max_exposure = 500
        max_daily_loss_per_bot = 200

        if "Scalping" in signal_type:
            scalping_max_exposure = global_max_exposure * 2
            if total_bot_liability + max_race_liability > scalping_max_exposure:
                return self.handle_rejected_signal(signal, "Scalping exposure limit exceeded")
        else:
            if total_bot_liability + max_race_liability > global_max_exposure:
                return self.handle_rejected_signal(signal, "Global exposure limit exceeded")

        matched_liability, daily_pnl = self.get_bot_status_from_db(bot_name)
        matched_liability = matched_liability or 0
        daily_pnl = daily_pnl or 0

        if daily_pnl <= -max_daily_loss_per_bot:
            return self.handle_rejected_signal(signal, "Daily loss limit reached")

        self.update_matched_bets(signal, liability)

        log_risk_management(
            "budget_approval",
            signal.get("strategy_name", "Unknown"),
            f"Bot: {bot_name}, Market: {signal['marketId']}, Liability: £{liability:.2f}, Total Exposure: £{total_bot_liability + max_race_liability:.2f}"
        )

        strategy_name = signal.get("strategy_name")
        if strategy_name == 'Basic Lay':
            place_basic_lay_bet(signal)
        elif strategy_name == 'Ladder Lays':
            place_ladder_lay_bet(signal)
        elif strategy_name == 'Scalping (Lay to Back)':
            place_scalp_trade(signal)
        elif strategy_name == 'Scalping (Back to Lay)':
            place_scalp_trade(signal, direction='back_to_lay')
        elif strategy_name == 'Green-Up':
            place_greenup_exit(signal)
        elif strategy_name == 'In-Play':
            place_inplay_lay_bet(signal)

        return signal

    def update_matched_bets(self, signal, liability):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        signal_fields = [
            'marketId', 'selectionId', 'stake', 'odds', 'bet_type', 'placed_at', 'status',
            'matched_liability', 'unmatched_liability', 'pnl', 'liability', 'result',
            'timestamp', 'matched_timestamp', 'betfair_bet_id', 'horse_name',
            'strategy_name', 'bot_name', 'date', 'can_accept_liability',
            'time_session', 'race_name', 'market_name', 'event_name', 'anchor_odd',
            'odds_check_1', 'odds_check_2', 'odds_check_3', 'odds_check_4',
            'odds_check_5', 'odds_check_6', 'customerOrderRef', 'bet_settled',
            'marketStartTime', 'meta_json', 'test_mode'
        ]

        values = [signal.get(field, None) for field in signal_fields]
        columns = ', '.join(signal_fields)
        placeholders = ', '.join(['?'] * len(signal_fields))

        cursor.execute(f"""
            INSERT INTO bets ({columns})
            VALUES ({placeholders})
        """, values)

        conn.commit()
        conn.close()
