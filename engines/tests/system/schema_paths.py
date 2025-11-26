# engines/tests/system/schema_paths.py
"""
System Test: DB Schema & Path Routing
Validates that database migration tables exist and
that config_paths.autoscalp_db() resolves to a usable file.
"""

import os, sqlite3
from engines import config_paths as cp

class Result:
    """Lightweight result object for test harness."""
    def __init__(self, ok: bool, notes=None, metrics=None):
        self.ok = ok
        self.notes = notes or []
        self.metrics = metrics or {}
    @classmethod
    def pass_(cls, notes=None, metrics=None):
        return cls(True, notes, metrics)
    @classmethod
    def fail(cls, notes=None, metrics=None):
        return cls(False, notes, metrics)

def run(db_paths, provide, deps):
    # call real migrations (creates folders/files for active mode)
    from engines.db_migrations import ensure_tables
    ensure_tables()

    # now validate both DBs exist & have minimal schema
    import os, sqlite3
    bets_path = os.path.abspath(db_paths.bets)
    auto_path = os.path.abspath(db_paths.autoscalp)

    def _table_exists(con, t):
        return bool(con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)
        ).fetchone())

    with sqlite3.connect(bets_path, timeout=5) as con:
        assert _table_exists(con, "bets")
        assert _table_exists(con, "oc_series")

    with sqlite3.connect(auto_path, timeout=5) as con:
        assert _table_exists(con, "inbound_oc_cache")
        assert _table_exists(con, "inbound_bets_min")

    # return Result just like other tests (runner expects .ok/.notes)
    from dataclasses import dataclass, field
    @dataclass
    class Result: ok: bool; notes: list = field(default_factory=list); metrics: dict = field(default_factory=dict)
    return Result(True, ["PASS", "DB paths resolved and minimal schema present"],
                  {"bets_db": bets_path, "autoscalp_db": auto_path})

