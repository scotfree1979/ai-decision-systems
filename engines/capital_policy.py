# ======================================================================================================
# 📍 TARGET: engines/capital_policy.py
# 🧩 ACTION: FULL REWRITE (FINAL — SNAPSHOT CONNECTED)
# 📆 PATCHED: 2026-03-17
#
# PURPOSE
# -------
# Single source of truth for engine capital.
#
# THIS FILE DOES:
#   1. Read BankState snapshot
#   2. Extract engine_available
#   3. Split into sub-pots
#   4. Provide emission checks
#
# ENGINES MUST:
#   - Call this helper only
#   - NEVER access DB
#
# ======================================================================================================

MIN_THRESHOLD = 10.0

DEFAULT_SPLIT = {
    "EXPLORATORY": 0.2,
    "RISK":        0.5,
    "INPLAY":      0.3,
}


# ------------------------------------------------------------------
# SNAPSHOT READ (THE MISSING PIECE)
# ------------------------------------------------------------------

def get_engine_available(engine_name: str) -> float:
    """
    Read engine_available directly from BankState snapshot.

    SOURCE:
        bankstate_engine_snapshot.available
    """

    try:
        from engines.config_paths import open_auto_db
        import sqlite3

        con = open_auto_db(rw=False)
        con.row_factory = sqlite3.Row

        row = con.execute("""
            SELECT available
            FROM bankstate_engine_snapshot
            WHERE engine = ?
            ORDER BY ts DESC
            LIMIT 1
        """, (engine_name,)).fetchone()

        con.close()

        if row and row["available"] is not None:
            return float(row["available"])

    except Exception:
        pass

    return 0.0


# ------------------------------------------------------------------
# CORE — ENGINE ENTRYPOINT
# ------------------------------------------------------------------

def get_engine_pots(engine_name: str, split: dict = None) -> dict:
    """
    MAIN ENTRYPOINT FOR ENGINES

    Engines call THIS — nothing else.

    Returns:
        {
            "EXPLORATORY": float,
            "RISK": float,
            "INPLAY": float
        }
    """

    engine_available = get_engine_available(engine_name)

    return split_pots(engine_available, split)


# ------------------------------------------------------------------
# SPLIT LOGIC
# ------------------------------------------------------------------

def split_pots(engine_available: float, split: dict = None) -> dict:

    if engine_available is None or engine_available <= 0:
        return {
            "EXPLORATORY": 0.0,
            "RISK":        0.0,
            "INPLAY":      0.0,
        }

    ratios = split or DEFAULT_SPLIT
    available = float(engine_available)

    return {
        "EXPLORATORY": available * float(ratios.get("EXPLORATORY", 0.0)),
        "RISK":        available * float(ratios.get("RISK", 0.0)),
        "INPLAY":      available * float(ratios.get("INPLAY", 0.0)),
    }


# ------------------------------------------------------------------
# EMISSION CHECK
# ------------------------------------------------------------------

def can_emit(pot: float) -> bool:

    if pot is None:
        return False

    try:
        return float(pot) >= MIN_THRESHOLD
    except Exception:
        return False


# ------------------------------------------------------------------
# ENGINE GATE (DISABLED)
# ------------------------------------------------------------------

def engine_can_run(*_, **__) -> bool:
    return True