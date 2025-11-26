import time
import sqlite3
from signal_memory_engine.playbook import finalise_playbook
from signal_memory_engine.ram_reader import get_static_snapshot
from config_paths import DB_PATH


def resolve_runner_pnl(self, market_id, selection_id):
    entry = None
    hedge = None

    for t in reversed(self.trade_log):
        if t["market_id"] == market_id and t["selection_id"] == selection_id:
            if t["side"] in ["LAY", "BACK"] and not entry:
                entry = t
            elif t["side"] in ["LAY", "BACK"] and entry:
                hedge = t
                break

    if not entry:
        result = "error"
        reason = "No entry recorded"
    elif not hedge:
        result = "partial"
        reason = "Hedge bet missing"
    else:
        entry_odds = entry["odds"]
        hedge_odds = hedge["odds"]
        tick_diff = self.get_tick_difference(entry_odds, hedge_odds)
        result = "success" if tick_diff >= 1 else "poor_edge"
        reason = f"Entry @ {entry_odds} → Hedge @ {hedge_odds} ({tick_diff} ticks)"

    self.record_signal_outcome(
        market_id=market_id,
        selection_id=selection_id,
        result=result,
        explanation=reason
    )

    if entry and hedge:
        entry_profit = self.estimate_liability(entry["odds"], entry["stake"], entry["side"])
        hedge_profit = self.estimate_liability(hedge["odds"], hedge["stake"], hedge["side"])
        net_profit = hedge_profit - entry_profit
    else:
        net_profit = 0.0

    finalise_playbook(self, market_id, selection_id, result, net_profit)
    return True
