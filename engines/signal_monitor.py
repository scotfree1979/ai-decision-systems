from upgrade_import_patch import *
import logging
from queue import Queue
import queue

# ✅ Shared signal queue for injection and processing
signal_queue = queue.Queue()

bot_signal_queues = {
    'OGHeadTrader': Queue(),
    'OGPreoff': Queue(),
    'OGInPlay': Queue()
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Explicit global definitions
risk_manager_queue = Queue()
current_session = None

# Explicit bot assignments
bot_assignments = {
    'Manual Injection': ['OGHeadTrader'],
    'Market Drifter Signal': ['OGHeadTrader', 'OGPreoff'],
    'Market Steamer Signal': ['OGHeadTrader', 'OGPreoff'],
    'Odds Spike Reversal': ['OGHeadTrader'],
    'Early Pace Collapse': ['OGInPlay'],
    'Late Surge Fade': ['OGPreoff'],
    'Drifter Recovery': ['OGPreoff'],
    'Venue/Trainer/Jockey Signals': ['OGHeadTrader', 'OGPreoff'],
    'ICE/FIRE In-Play Bets': ['OGInPlay'],
    'Horse Hits Fence': ['OGInPlay'],
    'Horse Rears Up': ['OGInPlay'],
    'Missed Break': ['OGInPlay'],
    'Jockey Loses Iron': ['OGInPlay'],
    'Horse Pulling Hard': ['OGInPlay'],
    'Favorite Struggling': ['OGInPlay'],
    'Loose Horse': ['OGInPlay'],
    'Horse Heavily Ridden': ['OGInPlay'],
    'Runner Pulling Up': ['OGInPlay'],
    'Heavy Ground Reaction': ['OGInPlay'],
    'Late-Race Equipment Failure': ['OGInPlay'],
    'Front Runner Comfortable': ['OGInPlay'],
    'Horse Traveling Strongly': ['OGInPlay'],
    'In-Play Lay Bets': ['OGInPlay'],
    'Close Finish Forecasts': ['OGInPlay', 'OGPreoff']
}

# Explicit session-based signal processing
def process_signal(signal):
    global current_session

    signal_type = signal.get('signal_type')

    # Handle explicitly session-update signals
    if signal_type and signal_type.startswith('time_'):
        current_session = signal.get('time_session')
        logging.info(f"✅ Session explicitly updated to {current_session} min.")
        return

    assigned_bots = bot_assignments.get(signal_type, [])

    if not assigned_bots:
        logging.warning(f"⚠️ No explicit bot assignment for signal type: {signal_type}")
        return

    # Assign strategy based on session timing
    for bot_name in assigned_bots:
        signal_to_risk = signal.copy()
        signal_to_risk['time_session'] = signal.get('time_session') or current_session or 'anytime'
        signal_to_risk['original_signal_type'] = signal_type

        if signal_type and signal_type.startswith('time_'):
            if current_session == 5:
                signal_to_risk['strategy_name'] = 'Green-Up'
            elif current_session in [10, 15, 20]:
                signal_to_risk['strategy_name'] = 'Scalping'
            else:
                signal_to_risk['strategy_name'] = 'Basic Lay'
        else:
            signal_to_risk['strategy_name'] = 'Basic Lay'

        # Queue to risk manager
        risk_manager_queue.put(signal_to_risk)
        logging.info(f"🚨 Signal explicitly queued for Risk Manager (Bot: {bot_name}, Strategy: {signal_to_risk['strategy_name']}, Session: {current_session}): {signal_to_risk}")

        # Also queue to bot-specific queue
        bot_queue = bot_signal_queues.get(bot_name)
        if bot_queue:
            bot_queue.put(signal_to_risk)
            logging.info(f"📥 Signal also queued for {bot_name} bot queue.")
