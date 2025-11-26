import time
import sqlite3
import json
from config_paths import DB_PATH
from db.signal_memory_db_writer import save_snapshot_to_ram


def record_signal_outcome(self, market_id, selection_id, result, explanation=None):
    key = (market_id, selection_id)
    snapshot = self.get_static_snapshot().get(key, {}).get("snapshot", {})

    outcome = {
        "timestamp": time.time(),
        "result": result,
        "explanation": explanation or "No explanation provided"
    }

    snapshot.setdefault("signal_outcomes", []).append(outcome)
    save_snapshot_to_ram(market_id, selection_id, snapshot)

    print(f"""🧠 SIGNAL OUTCOME:
    🏇 Runner:      {selection_id}
    🌟 Market:      {market_id}
    📈 Result:      {result}
    📝 Explanation: {explanation}
    """)


def record_trade_fault(self, market_id, selection_id, fault_type, context=None):
    key = (market_id, selection_id)
    fault = {
        "timestamp": time.time(),
        "type": fault_type,
        "context": context or "N/A"
    }

    snapshot = self.get_static_snapshot().get(key, {}).get("snapshot", {})
    snapshot.setdefault("faults", []).append(fault)
    save_snapshot_to_ram(market_id, selection_id, snapshot)

    print(f"""🧠 TRADE FAULT:
    🏇 Runner:    {selection_id}
    🌟 Market:    {market_id}
    ❌ Fault:     {fault_type}
    📄 Context:   {context}
    """)
