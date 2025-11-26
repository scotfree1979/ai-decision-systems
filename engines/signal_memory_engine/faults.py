import time
from db.signal_memory_db_writer import save_snapshot_to_ram
from signal_memory_engine.ram_reader import get_static_snapshot

def record_trade_fault(self, market_id, selection_id, fault_type, context=None):
    key = (market_id, selection_id)

    fault = {
        "timestamp": time.time(),
        "type": fault_type,
        "context": context or "N/A"
    }

    snapshot = get_static_snapshot().get(key, {}).get("snapshot", {})
    snapshot.setdefault("faults", []).append(fault)
    save_snapshot_to_ram(market_id, selection_id, snapshot)

    print(f"""🧠 TRADE FAULT:
    🏇 Runner:    {selection_id}
    🌟 Market:    {market_id}
    ❌ Fault:     {fault_type}
    📄 Context:   {context}
    """)
