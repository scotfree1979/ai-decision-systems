# engines/sqlite_shim.py
from __future__ import annotations
import os, sqlite3

def open_db(path: str, *, ro: bool = False, wal: bool = True, timeout: float = 12.0) -> sqlite3.Connection:
    """
    Open a SQLite DB with sane defaults. Always sets busy_timeout/foreign_keys.
    WAL is used unless explicitly disabled.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    uri = f"file:{path}?mode={'ro' if ro else 'rwc'}"
    con = sqlite3.connect(uri, uri=True, isolation_level=None, check_same_thread=False, timeout=timeout)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=12000")
        if wal:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
        else:
            con.execute("PRAGMA journal_mode=DELETE")
    except Exception:
        pass
    return con
