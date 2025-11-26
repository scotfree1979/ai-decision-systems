# engines/live/loop.py
from __future__ import annotations
import threading, time, sqlite3
from datetime import datetime, timezone
from engines.config_paths import auto_conn as _auto_conn  # canonical DAL


# Optional integration with your live settlements pipeline
try:
    from engines.live.settlements import (
        fetch_cleared_orders_api as _fetch_range_api,   # (from_iso, to_iso)
        reconcile_orders as _reconcile_orders,          # () -> (updated, pairs)
        fetch_market_metadata_api as _fetch_meta        # (market_ids)
    )
except Exception as e:
    print(f"[SETTLE_LOOP] warn: cannot import settlements live module: {e}")
    _fetch_range_api = _reconcile_orders = _fetch_meta = None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _cancel_unmatched_parents_at_off() -> int:
    # DAL-safe writer: always open with rw=True
    con = _auto_conn(rw=True)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute("""
            UPDATE orders
               SET status='CANCELLED',
                   entry_status=CASE
                        WHEN UPPER(COALESCE(entry_status,''))='PENDING'
                        THEN 'CANCELLED'
                        ELSE COALESCE(entry_status,'CANCELLED')
                   END,
                   closed_at=COALESCE(closed_at, datetime('now','utc')),
                   notes = TRIM(COALESCE(notes,'') || ' off_cancel')
             WHERE hedge_of IS NULL
               AND closed_at IS NULL
               AND UPPER(COALESCE(status, entry_status,'')) IN ('PLACED','PENDING')
               AND COALESCE(entry_matched_stake,0.0)=0.0
               AND marketId IN (
                    SELECT marketId FROM markets_schedule
                     WHERE datetime(off_at_utc) <= datetime('now','utc')
               )
        """)
        con.commit()
        return cur.rowcount or 0
    except Exception:
        try: con.rollback()
        except Exception: pass
        return 0
    finally:
        try: con.close()
        except Exception: pass


def _cancel_unmatched_children_for_closed_markets() -> int:
    # DAL-safe writer: always open with rw=True
    con = _auto_conn(rw=True)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute("""
            UPDATE orders
               SET status='CANCELLED',
                   exit_status=COALESCE(exit_status,'CANCELLED'),
                   closed_at=COALESCE(closed_at, datetime('now','utc')),
                   notes = TRIM(COALESCE(notes,'') || ' race_end_cancel')
             WHERE hedge_of IS NOT NULL
               AND closed_at IS NULL
               AND COALESCE(entry_matched_stake,0.0)=0.0
               AND marketId IN (
                    SELECT marketId
                      FROM bf_market_book
                     WHERE UPPER(status)='CLOSED'
               )
        """)
        con.commit()
        return cur.rowcount or 0
    except Exception:
        try: con.rollback()
        except Exception: pass
        return 0
    finally:
        try: con.close()
        except Exception: pass


def _pull_and_reconcile_today() -> tuple[int,int]:
    """Optional Betfair reconciliation pass using your settlements.py"""
    if not (_fetch_range_api and _reconcile_orders):
        return (0, 0)
    to_dt = _now_utc()
    from_dt = to_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    fetched = updated = 0
    try:
        report = _fetch_range_api(from_dt, to_dt) or {}
        mids = list({r.get("marketId") for r in (report.get("cleared_orders") or []) if r.get("marketId")})
        if mids and _fetch_meta:
            _fetch_meta(mids)
        fetched = len(report.get("cleared_orders") or [])
    except Exception:
        fetched = 0
    try:
        updated = int(_reconcile_orders() or 0)
    except Exception:
        updated = 0
    return (fetched, updated)

def settle_once(verbose: bool = True) -> None:
    n_off = _cancel_unmatched_parents_at_off()
    n_ch  = _cancel_unmatched_children_for_closed_markets()
    n_fetch, n_upd = _pull_and_reconcile_today()
    if verbose:
        print(f"[SETTLE] off_cancel={n_off} child_cancel={n_ch} fetched={n_fetch} reconciled={n_upd}")

def _loop(period_s: int = 300) -> None:
    while True:
        try:
            settle_once(verbose=True)
        except Exception as e:
            print(f"[SETTLE] loop warn: {e}")
        time.sleep(max(30, int(period_s)))

def _start_settlement_loop(period_s: int = 300) -> None:
    t = getattr(_start_settlement_loop, "_t", None)
    if t and t.is_alive():
        return
    import threading
    t = threading.Thread(target=_loop, args=(period_s,), daemon=True, name="settlement-loop")
    _start_settlement_loop._t = t
    t.start()

if __name__ == "__main__":
    import argparse, time
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--period", type=int, default=300)
    args = ap.parse_args()
    if args.once:
        settle_once(verbose=True)
    else:
        _start_settlement_loop(args.period)
        while True:
            time.sleep(3600)
