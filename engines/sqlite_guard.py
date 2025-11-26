from __future__ import annotations
import sqlite3, time
from typing import Optional
from engines.config_paths import autoscalp_db

def ensure_dashboard_indexes(max_wait_s: float = 10.0, quiet: bool = True) -> bool:
    """
    Create the indexes the GUI/feeder rely on. Retries briefly on SQLITE_BUSY/locked.
    Returns True if indexes exist (created or already present).
    """
    start = time.time()
    last_err: Optional[Exception] = None
    while (time.time() - start) < max_wait_s:
        try:
            con = sqlite3.connect(autoscalp_db(), timeout=30, check_same_thread=False)
            con.execute("PRAGMA journal_mode=WAL;")
            con.execute("PRAGMA busy_timeout=5000;")
            con.execute("PRAGMA synchronous=NORMAL;")
            # These CREATEs require an exclusive write lock; retry if locked
            con.execute("CREATE INDEX IF NOT EXISTS idx_inbound_oc_cache_mid_sid_id ON inbound_oc_cache(marketId, selectionId, id)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_oc_series_mid_sid_ts ON oc_series(marketId, selectionId, snapshot_ts)")
            con.commit()
            try:
                con.execute("PRAGMA optimize;")
            except Exception:
                pass
            con.close()
            return True
        except sqlite3.OperationalError as e:
            last_err = e
            msg = str(e).lower()
            if "locked" in msg or "busy" in msg:
                time.sleep(0.2)
                continue
            # other sqlite op errors → give up
            break
        except Exception as e:
            last_err = e
            break
    if not quiet:
        print(f"[indexes] ensure failed: {last_err}")
    return False
