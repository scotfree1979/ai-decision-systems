# engines/clean_launch.py
# v0 clean launch: GUI-first → single writer → fetch+seed (2-day) → flush → SME OC bands only

import os
import sys
import json
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Iterable, Tuple, Set

# --- bootstrap so this file runs from anywhere ---
_here = os.path.dirname(os.path.abspath(__file__))            # .../analytics_beta/engines
_root = os.path.dirname(_here)                                # .../analytics_beta
if _root not in sys.path:
    sys.path.insert(0, _root)

# --- imports ---
from engines.upgrade_import_patch import get_session_token
from get_markets import get_markets_and_insert
from engines.utils.api_tools import fetch_live_odds
from engines.database_hijack_monitor import (
    launch_db_writer,
    enqueue_write,
    enqueue_read,
    priority_queue as DBQ,
)

# Fixed daily refresh slots (local wall-clock HH:MM)
SCHEDULE_SLOTS: Set[str] = {"00:05", "06:00", "22:00", "23:55"}

# -----------------------------
# Helpers
# -----------------------------

def _iso_utc_now() -> str:
    return datetime.utcnow().isoformat()


def _parse_start_date_iso(start: Optional[str]) -> Optional[str]:
    if not start:
        return None
    try:
        s = start.replace("Z", "+00:00") if "Z" in start else start
        return datetime.fromisoformat(s).date().isoformat()
    except Exception:
        return None


def _filter_markets_by_dates(markets: Iterable[dict], target_dates: Set[str]) -> list:
    out = []
    for m in markets or []:
        d = _parse_start_date_iso(m.get("marketStartTime"))
        if d and d in target_dates:
            out.append(m)
    return out


def _upsert_runner_row(market: dict, runner: dict, anchor_lay: Optional[float]) -> None:
    """
    Write metadata + (optional) anchor + OC0 init into bets.
    Uses UPDATE-if-exists semantics (fill-only-when-NULL); inserts if row doesn’t exist.
    """
    market_id = market.get("marketId")
    selection_id = runner.get("selectionId")
    if not market_id or selection_id is None:
        return

    # --- fields ---
    horse_name   = runner.get("runnerName") or runner.get("horse_name")
    market_name  = market.get("marketName") or market.get("market_name")
    event_name   = (market.get("event") or {}).get("name") or market.get("event_name")
    race_name    = market.get("race_name") or market_name
    start_time   = market.get("marketStartTime")
    date_str     = _parse_start_date_iso(start_time) or datetime.utcnow().date().isoformat()
    now_iso      = _iso_utc_now()
    placed_at    = _iso_utc_now() if anchor_lay is not None else None
    oc0_band     = json.dumps([anchor_lay]) if anchor_lay is not None else None
    meta_json    = json.dumps({"market": market, "runner": runner}, default=str)

    # Does row exist?
    try:
        rows = enqueue_read(
            "SELECT 1 FROM bets WHERE marketId = ? AND selectionId = ? LIMIT 1",
            (market_id, selection_id),
        )
        exists = bool(rows)
    except Exception:
        # If read fails, fall back to insert-path (safe default)
        exists = False

    if exists:
        # Fill-only-when-NULL (no clobbering later enrichments). COALESCE keeps existing if not NULL.
        update_sql = (
            "UPDATE bets SET "
            "horse_name = COALESCE(horse_name, ?), "
            "race_name = COALESCE(race_name, ?), "
            "market_name = COALESCE(market_name, ?), "
            "event_name = COALESCE(event_name, ?), "
            "marketStartTime = COALESCE(marketStartTime, ?), "
            "date = COALESCE(date, ?), "
            "timestamp = COALESCE(timestamp, ?), "
            "meta_json = COALESCE(meta_json, ?), "
            "anchor_odd = COALESCE(anchor_odd, ?), "
            "placed_at = COALESCE(placed_at, ?), "
            "OC0 = COALESCE(OC0, ?), "
            "OC0_band = COALESCE(OC0_band, ?) "
            "WHERE marketId = ? AND selectionId = ?"
        )
        enqueue_write(
            update_sql,
            [
                horse_name,
                race_name,
                market_name,
                event_name,
                start_time,
                date_str,
                now_iso,
                meta_json,
                anchor_lay,
                placed_at,
                anchor_lay,
                oc0_band,
                market_id,
                selection_id,
            ],
        )
    else:
        # Insert minimal + available values; many other columns exist but are optional.
        insert_sql = (
            "INSERT INTO bets ("
            "marketId, selectionId, horse_name, race_name, market_name, event_name, "
            "marketStartTime, date, timestamp, meta_json, anchor_odd, placed_at, OC0, OC0_band"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        )
        enqueue_write(
            insert_sql,
            [
                market_id,
                selection_id,
                horse_name,
                race_name,
                market_name,
                event_name,
                start_time,
                date_str,
                now_iso,
                meta_json,
                anchor_lay,
                placed_at,
                anchor_lay,
                oc0_band,
            ],
        )


def _fetch_seed_for_dates(target_dates: Set[str]) -> None:
    token = get_session_token()
    if not token:
        raise RuntimeError("No session token set. Paste it in the GUI before Launch.")

    # 1) Fetch markets (your function may already insert; we still use returned payload for metadata)
    markets = get_markets_and_insert() or []
    markets = _filter_markets_by_dates(markets, target_dates)

    # 2) For each runner, fetch anchor (top LAY) + upsert row with metadata in one pass
    for m in markets:
        market_id = m.get("marketId")
        for r in (m.get("runners") or []):
            selection_id = r.get("selectionId")
            if not market_id or selection_id is None:
                continue
            try:
                odds = fetch_live_odds(token, market_id, selection_id)
            except Exception:
                odds = None
            lay = (odds or {}).get("lay")
            _upsert_runner_row(m, r, lay)


def _flush_writes_blocking():
    # Block until DB writer queue is empty and all items are committed
    try:
        DBQ.join()
    except Exception:
        pass


# -----------------------------
# Public entrypoint (called by GUI)
# -----------------------------

def launch_data_collection(days: Tuple[int, ...] = (0, 1), enable_scheduler: bool = True) -> None:
    """Run v0 data-only pipeline once, then optionally start the scheduler."""
    # Start single writer (idempotent)
    launch_db_writer()

    # Compute target dates (UTC)
    today_utc = datetime.utcnow().date()
    target_dates = { (today_utc + timedelta(days=d)).isoformat() for d in days }

    # One-shot fetch+seed for the requested dates
    _fetch_seed_for_dates(target_dates)
    _flush_writes_blocking()  # ensure DB is fully populated before SME starts

    # Start SME in data-only mode (only OC bands)
    try:
        try:
            from signal_memory_engine.signal_memory import SignalMemoryEngine  # common location
        except Exception:
            from signal_memory_engine import SignalMemoryEngine  # fallback if class is top-level
        sme = SignalMemoryEngine()
        threading.Thread(target=sme.record_oc0_and_band, name="SME_OC_Bands", daemon=True).start()
    except Exception as e:
        raise RuntimeError(f"Failed to start SME OC-band loop: {e}")

    # Optional: start periodic refresh scheduler (same 2-day job)
    if enable_scheduler:
        t = threading.Thread(
            target=_scheduler_loop,
            name="MarketRefreshScheduler",
            args=(),
            daemon=True,
        )
        t.start()


def _scheduler_loop() -> None:
    """Refresh markets/anchors at fixed wall-clock slots. Safe & idempotent."""
    already_ran: Set[str] = set()  # e.g., {"2025-08-13 06:00"}
    while True:
        try:
            now = datetime.now()
            hhmm = now.strftime("%H:%M")
            key = f"{now.date()} {hhmm}"

            if hhmm in SCHEDULE_SLOTS and key not in already_ran:
                # target: today + tomorrow at the time of run (UTC basis for dates)
                today = datetime.utcnow().date()
                target_dates = { today.isoformat(), (today + timedelta(days=1)).isoformat() }
                _fetch_seed_for_dates(target_dates)
                _flush_writes_blocking()
                already_ran.add(key)
        except Exception:
            pass
        time.sleep(20)  # light poll
