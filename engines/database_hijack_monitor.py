#!/usr/bin/env python3
# ============================================================
# Database Hijack — FINAL, STABLE, SINGLE-SOURCE IMPLEMENTATION
# ============================================================
# Responsibilities:
#   • Override sqlite3.connect() safely (factory override)
#   • Keep AlphaX pure: classify only, never open DBs
#   • GUI Step 1/2/3 → ALWAYS local DB writes via DAL
#   • Everything else → AlphaX → DAL routing
#   • Zero recursion, zero leaks, zero readonly errors
# ============================================================

from __future__ import annotations
import sqlite3, os, time, threading, queue, inspect

# ORIGINAL connect (never overwritten again)
_ORIG_CONNECT = sqlite3.connect

# AlphaX APIs
from engines.alphax_gateway import (
    alphax_route,
    alphax_entrypoint,
    _HP,
    _LP,
)

# DAL openers
from engines.config_paths import (
    open_auto_db,
    open_bets_db,
    open_mastery_db,
    open_settlements_db,
    LOCAL_AUTO,
    LOCAL_BETS,
)

# ============================================================
#   GLOBAL WRITE ENQUEUE (AlphaX schedules → DAL executes)
# ============================================================
def install_global_sqlite_hijack():
    """
    Install sqlite3.connect hijack correctly.
    - DAL (engines/config_paths.py) bypasses hijack completely
    - Everywhere else gets factory=_HijackedConnection
    """

    # avoid double-install
    if getattr(sqlite3, "_autosc_hijacked", False):
        return

    def _connect(db_path, *args, **kwargs):
        import inspect, os

        # BYPASS: DAL must never be hijacked
        for frame in inspect.stack():
            fn = frame.filename.replace("\\", "/").lower()
            if "engines/config_paths.py" in fn:
                return _ORIG_CONNECT(db_path, *args, **kwargs)

        # NORMAL HIJACK
        return _ORIG_CONNECT(
            db_path,
            *args,
            factory=_HijackedConnection,
            **kwargs
        )

    # IMPORTANT — install override *here*, not inside _connect
    sqlite3.connect = _connect
    sqlite3._autosc_hijacked = True



# ============================================================
#   HIJACKED CONNECTION (REAL WORKING SUBCLASS)
# ============================================================
class _HijackedConnection(sqlite3.Connection):
    def execute(self, sql, params=()):
        """
        Thin wrapper around sqlite3.Connection.execute.

        Design:
          - No AlphaX routing here.
          - No extra connections opened.
          - No implicit close() after execute.
          - SELECT / INSERT / UPDATE / DELETE all behave like stock sqlite3.

        All cross-cutting behaviour (async writes, DAL routing, cloud/local)
        is handled by:
          • enqueue_read / enqueue_write in this module
          • engines.alphax_gateway._lp_loop (for queued writes)
          • engines.config_paths.open_* (DAL)
        """
        return super().execute(sql, params)

    def executemany(self, sql, seq_of_params):
        """Keep executemany semantics identical as well."""
        return super().executemany(sql, seq_of_params)



# ------------------------------------------------------------
# Activate immediately and permanently
# ------------------------------------------------------------
# DO NOT auto-hijack at import time.
# GUI/Step 1 will call this manually.
def activate_hijack():
    install_global_sqlite_hijack()
    print("[HIJACK] ACTIVE — using factory=_HijackedConnection")



# ============================================================
#   LEGACY SHIMS (unchanged)
# ============================================================
def enqueue_read(sql: str, params=(), *, db=None):
    meta = alphax_route((sql or "").lower(), params)
    fam = meta["family"]

    WAL = {
        "auto": open_auto_db,
        "bets": open_bets_db,
        "mastery": open_mastery_db,
        "settlements": open_settlements_db,
    }[fam]

    con = WAL(ro=True, rw=False)
    try:
        return con.execute(sql, params).fetchall()
    except:
        return []
    finally:
        try: con.close()
        except: pass


def enqueue_write(sql: str, params=(), priority=5, *, db=None):
    meta = alphax_route((sql or "").lower(), params)
    fam = meta["family"]
    pr = meta["priority"]

    WAL = {
        "auto": open_auto_db,
        "bets": open_bets_db,
        "mastery": open_mastery_db,
        "settlements": open_settlements_db,
    }[fam]

    if pr == 1:
        con = WAL(rw=True, ro=False)
        try:
            return con.execute(sql, params)
        finally:
            try: con.commit()
            except: pass
            try: con.close()
            except: pass

    _LP.put((pr, time.time(), sql, tuple(params or ())))


def launch_db_writer():
    return None
