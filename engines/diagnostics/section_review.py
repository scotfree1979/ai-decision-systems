# engines/diagnostics/section_review.py
from engines.decision_engine.decide_once.lanes import check_decision_engine
from engines.live.live_router import check_live_router
from engines.risk.budget_manager import check_budget_manager
from engines.live.overwatcher import check_overwatcher_state
from schema_guard import check_schema_sync
import sqlite3, os

def run_section_review():
    path = os.path.expanduser("~/Dev/analytics_beta_dev/data/autoscalp_gui.db")
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    results = {
        "decisions": check_decision_engine(con),
        "router": check_live_router(con),
        "budget": check_budget_manager(con),
        "overwatcher": check_overwatcher_state(),
        "schema": check_schema_sync(),
    }
    print(f"[REVIEW] Summary → {results}")
    return results
