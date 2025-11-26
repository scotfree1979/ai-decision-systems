# ✅ og_inplay_bot.py - Updated to accept 4 arguments in OGInPlayBot constructor

import time
from datetime import datetime
from risk_manager import RiskManager
from upgrade.upgrade_20250526 import place_basic_lay_bet, place_ladder_lay_bet, place_scalp_trade, place_greenup_exit, place_inplay_lay_bet
from og_bot_family import og_inplay_bot
import logging
from .og_inplay_logic import inplay_loop

class OGInPlayBot:
    def __init__(self, risk_manager, budget_manager, headers, bot_signal_queue):
        self.risk_manager = risk_manager
        self.budget_manager = budget_manager
        self.headers = headers
        self.bot_signal_queue = bot_signal_queue
        logging.info("In-Play Bot: Initialized successfully with full config.")

    def start_loop(self):
        logging.info("In-Play Bot: Loop starting.")
        inplay_loop()


class OGInPlayBot:
    def __init__(self, risk_manager, head_trader):
        self.risk_manager = risk_manager
        self.head_trader = head_trader
        self.strategies = {
            'fast_pace_collapse': True,
            'drifter_recovery': True,
            'late_surge_fade': True,
            'odds_spike_reversal': True
        }
        self.horses_traded = set()

    def log_event(self, message):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_message = f"[{timestamp}] {message}"
        print(log_message)

    def monitor_race(self, market_data, marketId, headers):
        analysis = self.head_trader.analyze_market(market_data)
        if self.strategies['fast_pace_collapse']:
            self.fast_pace_collapse(market_data, analysis, marketId, headers)
        if self.strategies['drifter_recovery']:
            self.drifter_recovery(market_data, analysis, marketId, headers)
        if self.strategies['late_surge_fade']:
            self.late_surge_fade(market_data, analysis, marketId, headers)
        if self.strategies['odds_spike_reversal']:
            self.odds_spike_reversal(market_data, analysis, marketId, headers)

    def fast_pace_collapse(self, market_data, analysis, marketId, headers):
        for horse in market_data:
            if horse['name'] not in self.horses_traded and horse['early_odds_drop'] >= 0.3 and 1.8 <= horse['current_odds'] <= 3.5:
                self.place_lay(horse, 'Fast Pace Collapse', 0.3, marketId, headers)

    def drifter_recovery(self, market_data, analysis, marketId, headers):
        for horse in market_data:
            if horse['name'] not in self.horses_traded and horse['initial_odds_drop'] >= 0.25 and horse['odds_drift'] >= 0.1 and 2.0 <= horse['current_odds'] <= 4.0:
                self.place_lay(horse, 'Drifter Recovery', 0.25, marketId, headers)

    def late_surge_fade(self, market_data, analysis, marketId, headers):
        for horse in market_data:
            if horse['name'] not in self.horses_traded and horse['late_odds_drop'] >= 0.35 and 1.8 <= horse['current_odds'] <= 4.2:
                self.place_lay(horse, 'Late Surge Fade', 0.2, marketId, headers)

    def odds_spike_reversal(self, market_data, analysis, marketId, headers):
        for horse in market_data:
            if horse['name'] not in self.horses_traded and horse['rapid_odds_spike'] >= 0.4 and 1.8 <= horse['current_odds'] <= 3.0:
                self.place_lay(horse, 'Odds Spike Reversal', 0.15, marketId, headers)

    def place_lay(self, horse, strategy_name, liability_pct, marketId, headers):
        liability = self.risk_manager.max_liability_per_race * liability_pct
        current_odds = horse['current_odds']

        if self.risk_manager.verify_limits(liability):
            self.horses_traded.add(horse['name'])

            self.log_event(f"🚨 Lay Placed | Horse: '{horse['name']}' | Strategy: '{strategy_name}'\n"
                           f"- Trigger Condition met.\n"
                           f"- Lay Odds: {current_odds:.2f}\n"
                           f"- Liability: £{liability:.2f}")

            place_inplay_lay_bet(horse['selectionId'], current_odds, liability, marketId, headers)

            self.risk_manager.insert_trade_to_db(
                horse_name=horse['name'], odds=current_odds, liability=liability,
                bet_type='Lay', strategy_name=strategy_name, bot_name='OGInPlayBot'
            )

            self.risk_manager.update_global_exposure(liability)
            self.risk_manager.check_race_liability(liability)
