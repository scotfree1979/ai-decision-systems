from upgrade_import_patch import *
import logging
from upgrade.upgrade_20250526 import place_basic_lay_bet, place_inplay_lay_bet, place_greenup_exit
from rules.head_trader_rules import *
import time


class PreOffBot:
    def __init__(self, risk_manager, budget_manager, headers, bot_signal_queue):
        self.risk_manager = risk_manager
        self.budget_manager = budget_manager
        self.headers = headers
        self.bot_signal_queue = bot_signal_queue
        logging.info('PreOffBot initialized clearly with Risk Manager and Budget Manager.')
        logging.info(f"🆔 [PreOffBot] Queue object ID: {id(self.bot_signal_queue)}")

    def start_loop(self):
        logging.info('🧵 [PreOffBot] Loop explicitly started.')

        while True:
            try:
                logging.info("🧵 [PreOffBot] Loop is alive and checking queue.")
                logging.info(f"📊 [PreOffBot] Queue size: {self.bot_signal_queue.qsize()}")

                signal = self.bot_signal_queue.get()
                logging.info(f"📩 [PreOffBot] Pulled signal from queue: {signal}")
                logging.info(f"🔑 [PreOffBot] session_token: {signal.get('session_token')}")

                approved_signal = self.risk_manager.evaluate_signal(signal)
                if not approved_signal:
                    matched_liability, unmatched_liability, _ = self.budget_manager.get_bot_status_from_db("OGPreoff")
                    approved_signal = self.budget_manager.handle_rejected_signal(
                        signal, "Budget limit reached"
                    )

                if approved_signal:
                    strategy_name = approved_signal.get('strategy_name', 'Basic Lay')

                    if strategy_name == 'Basic Lay':
                        logging.info(f"📤 [PreOffBot] Routing to basic lay function.")
                        place_basic_lay_bet(approved_signal)
                        logging.info(f"✅ Basic lay placed by PreOffBot for market {approved_signal['marketId']}")

                    elif strategy_name == 'Ladder Lays':
                        logging.info(f"📤 [PreOffBot] Routing to ladder lay function.")
                        place_ladder_lay_bet(approved_signal)
                        logging.info(f"🪜 Ladder lay placed by PreOffBot for market {approved_signal['marketId']}")

                    elif strategy_name == 'Scalping':
                        logging.info(f"📤 [PreOffBot] Routing to scalp trade function.")
                        place_scalp_trade(approved_signal)
                        logging.info(f"💹 Scalping trade placed by PreOffBot for market {approved_signal['marketId']}")

                    elif strategy_name == 'Green-Up':
                        logging.info(f"📤 [PreOffBot] Routing to green-up function.")
                        place_greenup_exit(approved_signal)
                        logging.info(f"✅ Green-Up completed by PreOffBot for market {approved_signal['marketId']}")

                self.bot_signal_queue.task_done()

            except Exception as e:
                logging.error(f"❌ PreOffBot loop crashed: {e}")
            time.sleep(0.5)
