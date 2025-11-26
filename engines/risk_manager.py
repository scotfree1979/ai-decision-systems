# risk_manager.py (Final Explicit Risk Manager with Scalping and Liability Adjustments + ICE/FIRE Awareness)

from upgrade_import_patch import *
from config_paths import DB_PATH
# from utils.api_tools import get_simulated_now  # temporarily removed – function missing
from signal_monitor import bot_signal_queues



TEST_MODE = False

import logging
from datetime import datetime
from enhanced_real_time_bot_feed import log_risk_management

class RiskManager:
    def __init__(
        self,
        max_liability_per_race,
        max_daily_loss_per_bot,
        global_max_exposure,
        scalping_max_exposure,
        scalping_liability_multiplier,
        max_concurrent_races,
        stop_loss_threshold,
        profit_lock_threshold,
        budget_manager=None
    ):
        self.max_liability_per_race = max_liability_per_race
        self.max_daily_loss_per_bot = max_daily_loss_per_bot
        self.global_max_exposure = global_max_exposure
        self.scalping_max_exposure = scalping_max_exposure
        self.scalping_liability_multiplier = scalping_liability_multiplier
        self.max_concurrent_races = max_concurrent_races
        self.stop_loss_threshold = stop_loss_threshold
        self.profit_lock_threshold = profit_lock_threshold
        self.budget_manager = budget_manager

        self.active_races = {
            "OGHeadTrader": 0,
            "OGPreoff": 0,
            "OGInPlay": 0
        }
        self.max_active_races_per_bot = 3
        self.allowed_high_liability_signals = ['Pre-off Scalping Bets', 'Rapid In-Play Scalping']

    def evaluate_signal(self, signal):
        bot_name = signal['bot_name']
        odds = signal['odds']
        stake = signal['stake']
        time_session = signal.get('time_session', 'anytime')
        signal_type = signal.get('signal_type')
        anchor_odds = signal.get('anchor_odds')
        marketStartTime = signal.get('marketStartTime')
        market_status = signal.get('market_status', 'ACTIVE')
        strategy_name = signal.get('strategy_name', 'Unknown')

        meta = signal.get('meta', {})
        ice = meta.get('ice', False)
        fire = meta.get('fire', False)

        liability = stake * (odds - 1)

        if signal_type in self.allowed_high_liability_signals:
            if odds <= 11.0:
                log_risk_management("risk_approval", strategy_name, f"Scalping bet allowed | Bot: {bot_name} | Odds: {odds} | Liability: £{liability:.2f}")
                return signal
            else:
                log_risk_management("risk_rejection", strategy_name, f"Scalping odds too high ({odds}).")
                return None

        if odds >= 20.0:
            log_risk_management("risk_rejection", strategy_name, f"Odds too high: {odds}.")
            return None

        if anchor_odds and abs(odds - anchor_odds) > 5:
            log_risk_management("risk_rejection", strategy_name, f"Unrealistic odds jump from {anchor_odds} to {odds}.")
            return None

        if time_session == 'race_start':
            adjusted_liability_limit = self.max_liability_per_race * 0.8
        elif time_session == 'post_20_min':
            adjusted_liability_limit = self.max_liability_per_race * 0.9
        else:
            adjusted_liability_limit = self.max_liability_per_race

        if ice:
            adjusted_liability_limit *= 1.2
            log_risk_management("risk_adjustment", strategy_name, f"❄️ ICE detected → Liability increased to £{adjusted_liability_limit:.2f}")
        if fire:
            adjusted_liability_limit *= 0.75
            log_risk_management("risk_adjustment", strategy_name, f"🔥 FIRE detected → Liability reduced to £{adjusted_liability_limit:.2f}")

        if liability > adjusted_liability_limit:
            log_risk_management("risk_rejection", strategy_name, f"Liability £{liability:.2f} exceeds session limit £{adjusted_liability_limit:.2f}.")
            return None

        if marketStartTime:
            race_start_time = datetime.strptime(marketStartTime, "%Y-%m-%dT%H:%M:%S.%fZ")
            if (race_start_time - datetime.utcnow()).total_seconds() < 30:
                log_risk_management("risk_rejection", strategy_name, "Too close to race start.")
                return None

        if self.active_races.get(bot_name, 0) >= self.max_active_races_per_bot:
            log_risk_management("risk_rejection", strategy_name, f"{bot_name} reached active race limit.")
            return None

        if market_status == 'SUSPENDED':
            log_risk_management("risk_rejection", strategy_name, "Market suspended.")
            return None

        if signal.get('selection_status') == 'NON_RUNNER':
            log_risk_management("risk_rejection", strategy_name, "Selection is a non-runner.")
            return None

        required_fields = ['marketId', 'selectionId', 'odds', 'stake', 'bot_name', 'time_session']
        if not all(signal.get(field) for field in required_fields):
            log_risk_management("risk_rejection", strategy_name, "Missing essential signal data.")
            return None

        log_risk_management("risk_approval", strategy_name, f"Bot: {bot_name}, Odds: {odds}, Liability: £{liability:.2f}")
        return signal

    def increment_active_races(self, bot_name):
        self.active_races[bot_name] = self.active_races.get(bot_name, 0) + 1
        logging.info(f"🔄 {bot_name} active races increased to {self.active_races[bot_name]}.")

    def decrement_active_races(self, bot_name):
        if self.active_races.get(bot_name, 0) > 0:
            self.active_races[bot_name] -= 1
            logging.info(f"🔄 {bot_name} active races decreased to {self.active_races[bot_name]}.")
