# engines/mastery/context_observer_v7.py
# CONTEXT OBSERVER v7 — SCHEMA CONTRACT (LOCKED)
# Table: context_observations_raw
# Columns:
#   ctx_ref, marketId, selectionId,
#   engine, engine_family, letter, side,
#   odds_at_decision, stake_at_decision,
#   band, fav_rank, minutes_to_off, in_play,
#   ts_created, ctx_json
#
# ⚠️ DO NOT RENAME COLUMNS
# ⚠️ DO NOT QUERY UNDECLARED FIELDS
# ⚠️ ALWAYS VERIFY WITH PRAGMA


from datetime import datetime, timezone
import sqlite3
from engines.mastery.event_sink import subscribe
from engines.config_paths import autoscalp_db

# ------------------------------------------------------------------
# DB helpers (LOCAL ONLY)
# ------------------------------------------------------------------

def _db():
    con = sqlite3.connect(autoscalp_db())
    con.row_factory = sqlite3.Row
    return con


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------
# ctx_ref builder (AUTHORITATIVE)
# ------------------------------------------------------------------

def _ctx_ref(ev: dict) -> str | None:
    try:
        return (
            f"{ev.get('run_id')}|"
            f"{ev.get('marketId')}|"
            f"{ev.get('selectionId')}|"
            f"{ev.get('engine')}|"
            f"{ev.get('customerOrderRef')}"
        )
    except Exception:
        return None


# ------------------------------------------------------------------
# EVENT HANDLER
# ------------------------------------------------------------------

def _on_event(ev: dict):
    if not isinstance(ev, dict):
        return

    etype = ev.get("type") or ev.get("event")
    if not etype:
        return

    ref = _ctx_ref(ev)
    if not ref:
        return

    # ==============================================================
    # 1) CONTEXT SNAPSHOT (PARENT QUEUED)
    # ==============================================================
    if etype == "parent_queued":
        try:
            con = _db()
            con.execute(
                """
                INSERT OR IGNORE INTO context_observations_raw(
                    ctx_ref,
                    ts,
                    run_id,
                    marketId,
                    selectionId,
                    engine,
                    engine_family,
                    letter,
                    side,
                    odds,
                    stake,
                    minutes_to_off,
                    in_play,
                    band,
                    fav_rank,
                    customerOrderRef
                )
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    ref,
                    _now(),
                    ev.get("run_id"),
                    ev.get("marketId"),
                    ev.get("selectionId"),
                    ev.get("engine"),
                    _engine_family(ev.get("engine")),
                    ev.get("letter"),
                    ev.get("side"),
                    ev.get("entry_odds") or ev.get("odds"),
                    ev.get("entry_stake") or ev.get("stake"),
                    ev.get("minutes_to_off"),
                    int(bool(ev.get("in_play"))),
                    ev.get("band"),
                    ev.get("fav_rank"),
                    ev.get("customerOrderRef"),
                ),
            )
            con.commit()
            con.close()
        except Exception:
            pass

    # ==============================================================
    # 2) OUTCOMES (HEDGE / STOPLOSS / SETTLED)
    # ==============================================================
    elif etype in ("HEDGE_EXIT", "STOPLOSS_EXIT", "AUTO_SETTLED", "CANCELLED", "FAILED"):
        try:
            pnl = ev.get("realized_pnl") or ev.get("pnl")
            success = 1 if pnl is not None and pnl > 0 else 0

            con = _db()
            con.execute(
                """
                INSERT OR IGNORE INTO context_outcomes(
                    ctx_ref,
                    ts,
                    exit_kind,
                    pnl,
                    success
                )
                VALUES (?,?,?,?,?)
                """,
                (
                    ref,
                    _now(),
                    etype,
                    pnl,
                    success,
                ),
            )
            con.commit()
            con.close()
        except Exception:
            pass


# ------------------------------------------------------------------
# ENGINE → FAMILY NORMALISATION
# ------------------------------------------------------------------

def _engine_family(engine: str | None) -> str:
    if not engine:
        return "LEGACY"
    e = engine.upper()
    if e.startswith("MSC_"):
        return "MSC"
    if e == "OVERWATCHER":
        return "RISK"
    return "LEGACY"


# ------------------------------------------------------------------
# BOOTSTRAP
# ------------------------------------------------------------------

def start_context_observer():
    subscribe(_on_event)
    print("[CONTEXT] 🧠 Context Observer v7 online")
