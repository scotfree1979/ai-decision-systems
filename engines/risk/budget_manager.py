import threading, time, sqlite3, datetime, json
from engines.config_paths import autoscalp_db, bets_db
from engines.mastery import event_sink   # EventSync emission

# Updated global cap
MAX_RUNNER_LIAB = 90.0

def check_budget_manager(con):
    try:
        total = con.execute("SELECT SUM(entry_stake) FROM orders WHERE exit_status IS NULL;").fetchone()[0] or 0
        print(f"[CHECK] BudgetManager: open exposure £{total:.2f}")
        return total < 5000  # sanity cap check
    except Exception as e:
        print(f"[CHECK] BudgetManager: FAIL → {e}")
        return False

# === PATCH START ===
# 📍 TARGET: engines/risk/budget_manager.py
# 📆 PATCHED: 2025-11-10Z — Live Letter Indexing Layer (A1/A2/A3 Risk Controller)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import threading

class _LetterLedger:
    """Tracks sequential letter allocations (A1/A2/A3) and their liabilities."""
    def __init__(self):
        self._lock = threading.Lock()
        self._ledger = {}   # { letter: [liabilities...] }

    def refresh(self, con):
        """Rebuild current ledger from live orders."""
        with self._lock:
            self._ledger.clear()
            rows = con.execute("""
                SELECT letter, ABS(entry_stake*(entry_odds-1)) AS liab
                  FROM orders
                 WHERE role='PARENT' AND exit_status IS NULL
            """).fetchall()
            for r in rows:
                l = (r["letter"] or "").upper()
                if not l:
                    continue
                self._ledger.setdefault(l, []).append(float(r["liab"] or 0.0))
            # enforce stable order per letter
            for k in self._ledger:
                self._ledger[k].sort(reverse=True)

    def get_state(self, letter: str):
        """Return [(A1_liab, …)] for a letter, or [] if none."""
        with self._lock:
            return self._ledger.get(letter.upper(), []).copy()

    def seq_label(self, letter: str, idx: int) -> str:
        """Return label like A1, F2, etc."""
        return f"{letter.upper()}{idx+1}"

    def all_labels(self):
        """Return dict: { 'A1': liab, 'A2': liab, ... }"""
        out = {}
        with self._lock:
            for l, vals in self._ledger.items():
                for i, v in enumerate(vals):
                    out[f"{l}{i+1}"] = v
        return out

LETTER_LEDGER = _LetterLedger()
# === PATCH END ===


# === PATCH START ===
# 📍 TARGET: engines/risk/budget_manager.py:_cleanup_settled_markets
# 🔎 SEARCH: def _cleanup_settled_markets
# 📆 PATCHED: 2025-11-21 — DAL writer for AUTO DB, raw reader for SETTLE DB

from engines.config_paths import auto_conn as _auto_conn
from engines.live.settlements import settlements_db_path

def _cleanup_settled_markets() -> int:
    """
    Auto-cleanup: find markets that have finished (off_at_utc < now-15 min)
    and whose bets are fully settled in settlements.db, then mark all
    orders as SETTLED and clear exposure.
    """
    import sqlite3, datetime

    now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
    auto_db_con = _auto_conn(rw=True)              # DAL writer
    set_db_path = settlements_db_path()
    cleaned = 0

    with auto_db_con as a, sqlite3.connect(set_db_path) as s:
        a.row_factory = sqlite3.Row
        s.row_factory = sqlite3.Row

        # markets older than 15 min
        mids = [
            r["marketId"] for r in
            a.execute("""
                SELECT marketId FROM markets_schedule
                 WHERE off_at_utc IS NOT NULL
                   AND datetime(off_at_utc) <= datetime('now','-15 minutes')
            """)
        ]
        if not mids:
            return 0

        # keep only those that are fully settled in bf_cleared_orders
        mids_settled = {
            r["marketId"] for r in s.execute(
                "SELECT DISTINCT marketId FROM bf_cleared_orders "
                "WHERE settledDate IS NOT NULL"
            )
        }
        mids = [m for m in mids if m in mids_settled]
        if not mids:
            return 0

        from engines.alphax_gateway import enqueue_write

        for mid in mids:
            # === PATCH START: AlphaX Queue writer for order settlement ===
            sql = """
                UPDATE orders
                   SET exit_status='SETTLED',
                       closed_at=datetime('now','utc'),
                       net_pl = COALESCE(net_pl,0.0)
                 WHERE marketId=? AND UPPER(COALESCE(exit_status,'')) NOT IN ('SETTLED','CANCELLED')
            """
            enqueue_write(sql, (mid,), priority=3)
            # === PATCH END ===

        # No direct DB commit — AlphaXQ handles it.
        cleaned = len(mids)


    if cleaned:
        print(f"[BUDGET] auto-cleared {cleaned} orders from settled markets (liability reset)")

    return cleaned
# === PATCH END ===


# === PATCH START ===
# 📍 TARGET: engines/risk/budget_manager.py:_compute_liabilities
# 📆 PATCHED: 2025-10-30Z — unified event emission for ALERT/EMERGENCY/CAP signals
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from engines.mastery import event_sink

def _emit_liability_signal(mid: str, sid: str, liab: float, level: str, limit: float = 90.0):
    """Helper to emit standardised liability_signal events to Mastery."""
    try:
        payload = {
            "type": "liability_signal",
            "marketId": mid,
            "selectionId": sid,
            "liability": round(float(liab), 2),
            "level": level,
            "limit": limit,
            "ts": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        }
        event_sink.on_decision(payload)
        print(f"[BUDGET] → {level} mid={mid} sid={sid} £{liab:.2f} (limit £{limit:.2f})")
    except Exception as e:
        print(f"[BUDGET] warn: event emit failed ({e})")
# === PATCH END ===

# === PATCH START ===
# 📍 TARGET: engines/risk/budget_manager.py:_compute_liabilities
# 🔎 SEARCH: def _compute_liabilities
# 📆 PATCHED: 2025-11-21 — DAL RO for AUTO DB

from engines.config_paths import auto_conn as _auto_conn

def _compute_liabilities():
    """
    Compute live liabilities per (marketId, selectionId)
    using per-runner results from cashout_calc().
    """
    _cleanup_settled_markets()

    import datetime
    from engines.cashout_calc import cashout_calc

    MAX_RUNNER_LIAB = 90.0
    ALERT_LIAB = 45.0
    EMERGENCY_LIAB = 75.0

    # DAL-safe AUTO read
    con = _auto_conn(rw=False)
    con.row_factory = sqlite3.Row

    live = cashout_calc(con, write_to_db=False)
    con.close()

    liabs = {}

    for mid, info in live.items():
        sid = None

        # per-runner map present → use it
        if isinstance(info.get("runner_pnls"), dict):
            for sid, pnl in info["runner_pnls"].items():
                liab = abs(float(pnl or 0.0))
                liabs[(mid, str(sid))] = liab

        # fallback: market-level
        else:
            liab = float(info.get("liability", 0.0))
            worst_sid = str(info.get("worst_runner") or "0")
            liabs[(mid, worst_sid)] = liab

        # market-level traffic-light signal
        market_liab = float(info.get("liability", 0.0))
        level = None

        if market_liab >= MAX_RUNNER_LIAB:
            level = "CAP"
        elif market_liab >= EMERGENCY_LIAB:
            level = "EMERGENCY"
        elif market_liab >= ALERT_LIAB:
            level = "ALERT"

        if level:
            _emit_liability_signal(mid, sid, liab, level, MAX_RUNNER_LIAB)

    return liabs
# === PATCH END ===

# === PATCH START ===
# 📍 TARGET: engines/risk/budget_manager.py:_persist_signal
# 🔎 SEARCH: def _persist_signal
# 📆 PATCHED: 2025-11-21 — DAL writer for AUTO DB

from engines.config_paths import auto_conn as _auto_conn

def _persist_signal(mid: str, sid: str, liab: float, reason: str = "signal"):
    """Persist and emit a liability signal (non-blocking, safe column quoting)."""
    # === PATCH START: AlphaX Queue writer for liability_signals ===
    from engines.alphax_gateway import enqueue_write

    # Table is created lazily on first write
    sql_create = """
        CREATE TABLE IF NOT EXISTS liability_signals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT,
            marketId TEXT,
            selectionId TEXT,
            liability REAL,
            "limit" REAL,
            created_at TEXT DEFAULT (datetime('now','utc'))
        );
    """
    enqueue_write(sql_create, (), priority=3)

    sql_insert = """
        INSERT INTO liability_signals(day, marketId, selectionId, liability, "limit")
        VALUES(date('now','utc'), ?, ?, ?, ?)
    """
    enqueue_write(sql_insert, (mid, sid, liab, MAX_RUNNER_LIAB), priority=3)
    # === PATCH END ===


    # Event emission (unchanged)
    try:
        event_sink.on_decision({
            "type": "liability_signal",
            "marketId": mid,
            "selectionId": sid,
            "liability": liab,
            "limit": MAX_RUNNER_LIAB,
            "reason": reason,
            "timestamp": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        })
    except Exception as e:
        print(f"[BUDGET] warn: EventSync emit failed ({e})")
# === PATCH END ===


# --- new active enforcement interface -----------------------------------
def authorise(plan: dict) -> bool:
    """
    Enforce BankState liability rule:
    - Block additive exposure if liability > MAX_RUNNER_LIAB.
    - Allow hedge/reducing trades.
    Returns True if allowed, False if blocked.
    """
    try:
        mid = str(plan.get("marketId"))
        sid = str(plan.get("selectionId"))
        side = (plan.get("direction") or "").upper()
        liabs = _compute_liabilities()
        liab = liabs.get((mid, sid), 0.0)

        if liab <= MAX_RUNNER_LIAB:
            return True  # within limits

        # over limit: decide if new trade increases or decreases exposure
        if "LAY" in side and liab > MAX_RUNNER_LIAB:
            _persist_signal(mid, sid, liab, "liability_block")
            print(f"[BUDGET] BLOCKED LAY mid={mid} sid={sid} current £{liab:.2f} > £{MAX_RUNNER_LIAB}")
            return False
        if "BACK" in side and liab > MAX_RUNNER_LIAB:
            # BACK reduces existing lay liability — allow
            return True

    except Exception as e:
        print(f"[BUDGET] warn: authorise check failed ({e})")
    return True

# === PATCH START ===
# 📍 TARGET: engines/risk/budget_manager.py:watch_exposure
# 🔎 SEARCH: bcon = sqlite3.connect(bets_db())
# 📆 PATCHED: 2025-11-21 — DAL RO for bets.db

from engines.config_paths import open_bets_db as _bets



def watch_exposure(interval_s: int = 60):
    print(f"[BUDGET] watcher started (interval={interval_s}s, limit £{MAX_RUNNER_LIAB})")

    while True:
        try:
            liabs = _compute_liabilities()
            now = datetime.datetime.now(datetime.timezone.utc)

            # DAL-safe BETS reader
            bcon = _bets()
            bcon.row_factory = sqlite3.Row
            start_map = {
                str(r["marketId"]): str(r["marketStartTime"])
                for r in bcon.execute(
                    "SELECT marketId, marketStartTime FROM bets "
                    "WHERE marketStartTime IS NOT NULL"
                )
            }
            bcon.close()

            if not liabs:
                print("[BUDGET] ⚠️ no open orders detected.")
                time.sleep(interval_s)
                continue

            breaches = []
            for (mid, sid), val in liabs.items():
                if val > MAX_RUNNER_LIAB:
                    off_str = start_map.get(str(mid))
                    mins_to_off = None
                    if off_str:
                        try:
                            off_dt = datetime.datetime.fromisoformat(off_str.replace("Z", "+00:00"))
                            mins_to_off = int((off_dt - now).total_seconds() / 60)
                        except Exception:
                            pass
                    breaches.append((mid, sid, val, mins_to_off))
                    _persist_signal(mid, sid, val)

            if breaches:
                print(f"\n[BUDGET] ⚠️ {len(breaches)} runners exceed £{MAX_RUNNER_LIAB}:")
                print(" marketId        selectionId    liability    mins_to_off")
                print("────────────────────────────────────────────────────────────")
                for mid, sid, val, mins in sorted(breaches, key=lambda x: -x[2])[:20]:
                    mins_disp = f"{mins:+}m" if mins is not None else "--"
                    print(f" {mid:<15} {sid:<12} £{val:>8.2f}    {mins_disp:>6}")
                print("────────────────────────────────────────────────────────────")
                print("[BUDGET] ⏫ flagged to Mastery & persisted in liability_signals\n")
            else:
                print(f"[BUDGET] ✅ exposure OK (no runner > £{MAX_RUNNER_LIAB})")

        except Exception as e:
            print(f"[BUDGET] watcher warn: {e}")

        time.sleep(interval_s)
# === PATCH END ===

def start_watcher():
    """Launch background liability watcher thread."""
    t = threading.Thread(target=watch_exposure, name="BudgetWatcher", daemon=True)
    t.start()
# === PATCH END ===
