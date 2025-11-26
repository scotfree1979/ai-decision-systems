

# engines/session_secrets.py
from __future__ import annotations
from engines.config_paths import connect_db
import sqlite3

APP_KEY_KEY = "betfair_app_key"
SESSION_KEY = "betfair_session_token"

def _ensure_kv(conn):
    conn.execute("""
      CREATE TABLE IF NOT EXISTS app_kv(
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TEXT DEFAULT (datetime('now'))
      );
    """)

def set_secret(key: str, value: str):
    with connect_db(ro=False) as conn:
        _ensure_kv(conn)
        conn.execute("""
          INSERT INTO app_kv(key,value,updated_at) VALUES(?, ?, datetime('now'))
          ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')
        """, (key, value))
        conn.commit()

def get_secret(key: str) -> str | None:
    try:
        with connect_db(ro=True) as conn:
            row = conn.execute("SELECT value FROM app_kv WHERE key=?", (key,)).fetchone()
            return row[0] if row else None
    except sqlite3.OperationalError:
        with connect_db(ro=False) as w:
            _ensure_kv(w); w.commit()
        return None

# 📍 TARGET: engines/session_secrets.py
# 🔎 SEARCH: def load_betfair_creds
def load_betfair_creds() -> tuple[str, str]:
    """
    Return (app_key, session_token).
    AppKey is ALWAYS from daily_config.
    SessionToken comes from DB if present.
    """
    import sqlite3
    from engines.config_paths import autoscalp_db
    try:
        con = sqlite3.connect(autoscalp_db()); con.row_factory = sqlite3.Row
        row = con.execute("SELECT value FROM app_kv WHERE key='betfair_session_token'").fetchone()
        con.close()
        tok = (row["value"] if row else "").strip()
    except Exception:
        tok = ""
    try:
        from engines.daily_config import APP_KEY as DK_APP
        app = (DK_APP or "").strip()
    except Exception:
        app = ""
    return app, tok


# 📍 TARGET: engines/session_secrets.py
# 🔎 SEARCH: def save_betfair_creds
def save_betfair_creds(app_key: str, session_token: str) -> None:
    """
    Persist ONLY the session token to DB.
    AppKey is canonical in daily_config and not stored here.
    """
    import sqlite3
    from engines.config_paths import autoscalp_db
    con = sqlite3.connect(autoscalp_db())
    con.execute("CREATE TABLE IF NOT EXISTS app_kv (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("""
        INSERT INTO app_kv(key,value) VALUES('betfair_session_token',?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (session_token or "",))
    con.commit(); con.close()


# convenience for Step 1
def set_betfair_creds(app_key: str, session_token: str) -> None:
    set_secret(APP_KEY_KEY, (app_key or "").strip())
    set_secret(SESSION_KEY, (session_token or "").strip())

def set_betfair_creds_all(app_key: str, session_token: str) -> None:
    """
    Write App Key + Session Token into BOTH autoscalp_gui.db and bets.db (app_kv).
    This makes stray readers (whichever DB they use) see the fresh values.
    """
    import sqlite3
    from engines.config_paths import autoscalp_db, DB_PATH

    def _upsert(db_path: str, app: str, tok: str) -> None:
        conn = sqlite3.connect(db_path, timeout=15.0, isolation_level=None)
        try:
            with conn:
                try:
                    conn.execute("PRAGMA journal_mode=WAL;")
                    conn.execute("PRAGMA foreign_keys=ON;")
                except Exception:
                    pass
                # ensure table
                conn.execute("""
                  CREATE TABLE IF NOT EXISTS app_kv(
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT DEFAULT (datetime('now'))
                  );
                """)
                # upserts
                conn.execute("""
                  INSERT INTO app_kv(key,value,updated_at) VALUES('betfair_app_key', ?, datetime('now'))
                  ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')
                """, (app or "",))
                conn.execute("""
                  INSERT INTO app_kv(key,value,updated_at) VALUES('betfair_session_token', ?, datetime('now'))
                  ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')
                """, (tok or "",))
        finally:
            conn.close()

    # Write to GUI DB first (canonical), then bets.db
    _upsert(autoscalp_db(), app_key, session_token)
    _upsert(DB_PATH,         app_key, session_token)








