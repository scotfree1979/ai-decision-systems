# ✅ og_bot_family_launcher.py – Unified Launcher for OG Trading Ecosystem

import threading
import logging

from og_bot_family.og_head_trader_bot import HeadTraderBot
from og_bot_family.og_preoff_bot import PreOffBot
from og_bot_family.og_inplay_bot import OGInPlayBot
from og_brain_logic import place_bets_from_brain
from database_hijack_monitor import enqueue_write  # ✅ Updated hijack usage

# ✅ Main launcher for OG Trading Ecosystem
# ➕ Injects all shared dependencies
# 🧠 HeadTraderBot leads execution, trades all timeframes, and sets strategic tone
# 🤖 PreOffBot and OGInPlayBot act as tactical specialists under HeadTrader guidance
# 🧠 OG Brain launches in dual-mode:
#    🔹 Left Hemisphere = Strategic brain assisting HeadTrader (real-time execution)
#    🔹 Right Hemisphere = Analytical observer studying raw market patterns

def launch_bot_family(bot_signal_queues, session_token=None):
    try:
        logging.info("🚀 [Launcher] Launching Head Trader Bot")
        og_head_trader_bot = HeadTraderBot(
            bot_signal_queues=bot_signal_queues,
            session_token=session_token
        )
        threading.Thread(target=og_head_trader_bot.orchestrate_bots, daemon=True).start()

        logging.info("🚀 [Launcher] Launching OGPreoff Bot")
        og_preoff_bot = PreOffBot(
            bot_signal_queues=bot_signal_queues,
            head_trader=og_head_trader_bot,
            session_token=session_token
        )
        threading.Thread(target=og_preoff_bot.start_loop, daemon=True).start()

        logging.info("🚀 [Launcher] Launching OGInPlayBot")
        og_inplay_bot = OGInPlayBot(
            bot_signal_queues=bot_signal_queues,
            head_trader=og_head_trader_bot,
            session_token=session_token
        )
        threading.Thread(target=og_inplay_bot.start_loop, daemon=True).start()

        logging.info("🧠 [Launcher] Launching Left Hemisphere (Execution Brain)")
        threading.Thread(target=place_bets_from_brain, name="OGLeftHemisphere", daemon=True).start()

        logging.info("🧠 [Launcher] Right Hemisphere logic is handled internally by OG Brain – unified loop in place_bets_from_brain")  # 🔜 Future: observer thread

        logging.info("✅ [Launcher] All bots and brain hemispheres have been initialized.")

    except Exception as e:
        logging.error(f"❌ [Launcher] Failed to launch bots: {e}")
