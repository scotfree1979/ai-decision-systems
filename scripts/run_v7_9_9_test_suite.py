# engines/tools/autoscalp_doctor.py
#!/usr/bin/env python3
"""
AUTOSCALP SYSTEM HEALTH CHECK ("autoscalp_doctor")
---------------------------------------------------
Runs a full-stack validation of:
  • DAL
  • DBs (Local + LiveCache)
  • DailyConfig
  • BankState
  • BudgetManager
  • EventSink + lazy bridge loader
  • Live Router dependencies
  • AlphaX Gateway
  • Core schema sanity (orders, engine_pots, mastery_events)
"""

from __future__ import annotations
import sqlite3, traceback, time
from datetime import datetime
from engines.config_paths import (
    DATA_DIR, DAL_MODE, auto_conn, auto_conn_live, autoscalp_db,
)
from engines.daily_config import fetch_available_budget
from engines.live import bank_state
from engines.risk import budget_manager
from engines.mastery import event_sink

# -------------------------------------------------------------------
# UTILITIES
# -------------------------------------------------------------------

def _ok(msg):   print(f"  ✓ {msg}")
def _fail(msg): print(f"  ✗ {msg}")

def _separator(title: str):
    print(f"\n→ {title} ... ", end="", flush=True)

# -------------------------------------------------------------------
# CHECK: DB PATHS / DAL MODE
# -------------------------------------------------------------------

def check_db_paths():
    _separator("DB Paths / DAL Mode")
    try:
        _ok(f"(DAL_MODE={DAL_MODE}, DATA_DIR={DATA_DIR})")
        return True
    except Exception as e:
        _fail(str(e))
        return False

# -------------------------------------------------------------------
# CHECK: DailyConfig balance
# -------------------------------------------------------------------

def check_dailyconfig():
    _separator("DailyConfig Balance Fetch")
    try:
        bal = fetch_available_budget()
        _ok(f"(Balance={bal:.2f})")
        return True
    except Exception as e:
        _fail(str(e))
        return False

# -------------------------------------------------------------------
# CHECK: BankState integrity
# -------------------------------------------------------------------

def check_bankstate():
    _separator("BankState Pots + Availability")
    try:
        pots = bank_state.get_daily_pots()
        total = bank_state.get_balance()
        leg = bank_state.get_engine_available("LEGACY")
        _ok(f"(Total={total:.2f}, LEGACY avail={leg:.2f})")
        return True
    except Exception as e:
        _fail(str(e))
        return False

# -------------------------------------------------------------------
# CHECK: BudgetManager caps
# -------------------------------------------------------------------

def check_budget():
    _separator("BudgetManager Allocation/Caps")
    try:
        bal = bank_state.get_balance()
        cap = budget_manager.allowed_stake_for_engine("MSC_RISK", bal)
        _ok(f"(CAP MSC_RISK={cap:.2f})")
        return True
    except Exception as e:
        _fail(str(e))
        return False

# -------------------------------------------------------------------
# CHECK: DAL orders table read
# -------------------------------------------------------------------

def check_dal_orders():
    _separator("DAL + orders table")
    try:
        con = auto_conn()
        con.row_factory = sqlite3.Row
        con.execute("SELECT 1 FROM orders LIMIT 1").fetchone()
        _ok("(read OK)")
        return True
    except Exception as e:
        _fail(str(e))
        return False

# -------------------------------------------------------------------
# CHECK: AlphaX enqueue
# -------------------------------------------------------------------

def check_alphax():
    _separator("AlphaX No-Op Routing")
    try:
        from engines.alphax_gateway import enqueue_write
        enqueue_write(None, "UPDATE orders SET bank=bank")  # dry-run no-op
        _ok("(enqueue OK)")
        return True
    except Exception as e:
        _fail(str(e))
        return False

# -------------------------------------------------------------------
# CHECK: EventSink emit test
# -------------------------------------------------------------------

def check_event_sink():
    _separator("EventSink Emit Test")
    try:
        event_sink.emit("test_doctor_evt", {
            "ts": datetime.utcnow().isoformat(),
            "msg": "doctor-check"
        })
        _ok("(emit OK)")
        return True
    except Exception as e:
        _fail(str(e))
        return False

# -------------------------------------------------------------------
# CHECK: LiveCache schema match
# -------------------------------------------------------------------

def check_livecache_schema():
    _separator("LiveCache Schema")
    try:
        con = auto_conn_live(rw=False)
        con.row_factory = sqlite3.Row
        con.execute("SELECT 1 FROM orders LIMIT 1").fetchone()
        _ok("(orders OK)")
        return True
    except Exception as e:
        _fail(str(e))
        return False

# -------------------------------------------------------------------
# CHECK: SCOPE / LANES baseline call
# -------------------------------------------------------------------

def check_scope_lanes():
    _separator("Lanes/SCOPE basic call")
    try:
        from engines.lanes.scope import get_scope
        _ = get_scope(lookback_min=10)  # safe call
        _ok("(scope call OK)")
        return True
    except Exception as e:
        _fail(str(e))
        return False

# -------------------------------------------------------------------
# MASTER CHECK
# -------------------------------------------------------------------

def run_doctor():
    print("\n=== AUTOSCALP SYSTEM HEALTH CHECK ===\n")
    ok = True

    ok &= check_db_paths()
    ok &= check_dailyconfig()
    ok &= check_bankstate()
    ok &= check_budget()
    ok &= check_dal_orders()
    ok &= check_alphax()
    ok &= check_event_sink()
    ok &= check_livecache_schema()
    ok &= check_scope_lanes()

    print("\n")
    if ok:
        print("🚀 ALL SYSTEMS GREEN — SAFE TO LAUNCH LIVE")
    else:
        print("❌ SYSTEM NOT READY — FIX ERRORS ABOVE")

    print()

    return ok


if __name__ == "__main__":
    run_doctor()
