# === PATCH START ===
# 📍 TARGET: engines/mastery/feedback_assimilator.py
# 📆 PATCHED: 2025-10-24T21:10Z — Stage 4 feedback assimilation engine
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import sqlite3, json
from datetime import datetime, timezone

DB_PATH = "data/autoscalp_gui.db"

# === PATCH START ===
# 📍 TARGET: engines/mastery/feedback_assimilator.py
# 📆 PATCHED: 2025-10-25T12:05Z — Stage 5 schema-aligned assimilator (binding fix)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import sqlite3, json
from datetime import datetime, timezone
from engines.config_paths import autoscalp_db

# === PATCH START ===
# 📍 TARGET: engines/mastery/feedback_assimilator.py
# 🔎 SEARCH: DB_PATH = autoscalp_db()
# 📆 PATCHED: 2025-11-21 — redirect assimilation writes to mastery_v7.db
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from engines.config_paths import mastery_v7_db
DB_PATH = mastery_v7_db()
# === PATCH END ===

# ======================================================================================================
# 📍 TARGET: engines/mastery/feedback_assimilator.py
# 🔎 SEARCH: def assimilate_feedback(
# 🧩 ACTION: REPLACE FUNCTION BODY (surgical)
# 📆 PATCHED: 2026-01-10 — Make River real (single-writer, runner-level)
#
# FIXES:
# - Single DB connection (no reuse after close)
# - Remove duplicate market-level update
# - Update confidence + live_pnl_ratio
# - Assign bucket from letter
# - Ensure updated_at advances
# ======================================================================================================

def assimilate_feedback(limit_minutes: int = 10) -> int:
    from engines.mastery.canonical_digest import weight_for_letter
    from engines.mastery.train_mastery_v7 import _bucket_for_letter

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # --------------------------------------------------
    # 1) Load recent River-relevant events
    # --------------------------------------------------
    rows = cur.execute(f"""
        SELECT event_type, json_payload
          FROM mastery_cache
         WHERE event_type IN (
               'feedback_tick',
               'cashout_tick',
               'msc_outcome',
               'msc_cashout',
               'msc_settlement'
         )
           AND ts >= datetime('now','-{int(limit_minutes)} minute','utc')
         ORDER BY ts DESC
    """).fetchall()

    if not rows:
        con.close()
        return 0

    # --------------------------------------------------
    # 2) Aggregate FINAL market outcomes only
    # --------------------------------------------------
    markets: dict[str, dict[str, list[float]]] = {}

    for r in rows:
        try:
            data = json.loads(r["json_payload"])
        except Exception:
            continue

        mid = data.get("marketId")
        if not mid:
            continue

        if not (data.get("market_status") == "CLOSED" or data.get("is_complete") is True):
            continue

        m = markets.setdefault(mid, {"cashout": [], "liability": []})
        if "cashout_total" in data:
            m["cashout"].append(float(data["cashout_total"]))
        if "liability_total" in data:
            m["liability"].append(float(data["liability_total"]))

    if not markets:
        con.close()
        return 0

    # --------------------------------------------------
    # 3) Runner-level River write (AUTHORITATIVE)
    # --------------------------------------------------
    updated = 0

    for mid, vals in markets.items():

        cash = sum(vals["cashout"]) / max(1, len(vals["cashout"]))
        liab = sum(vals["liability"]) / max(1, len(vals["liability"]))
        pnl_ratio = (cash / liab) if liab else 0.0

        letter = str(mid)[5:6].upper() if len(mid) > 5 else "?"
        pnl_ratio *= weight_for_letter(letter)

        # River confidence update (bounded, deterministic)
        river_conf = max(0.0, min(1.0, 0.5 + pnl_ratio * 0.25))
        bucket = _bucket_for_letter(letter)

        runners = cur.execute(
            "SELECT selectionId FROM market_data WHERE marketId=?",
            (mid,)
        ).fetchall()

        for r in runners:
            sid = str(r["selectionId"])
            bin_key = f"{mid}|{sid}"

            cur.execute("""
                INSERT INTO mastery_posteriors
                    (bin_key, letter, bucket,
                     confidence, live_pnl_ratio, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now','utc'))
                ON CONFLICT(bin_key)
                DO UPDATE SET
                    letter         = excluded.letter,
                    bucket         = excluded.bucket,
                    confidence     = excluded.confidence,
                    live_pnl_ratio = excluded.live_pnl_ratio,
                    updated_at     = excluded.updated_at
            """, (
                bin_key,
                letter,
                bucket,
                float(river_conf),
                float(pnl_ratio),
            ))

            updated += 1

    con.commit()
    con.close()

    # --------------------------------------------------
    # 4) Emit River assimilation event
    # --------------------------------------------------
    try:
        from engines.mastery import event_sink
        event_sink.emit(
            "feedback_assimilated",
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "rows": updated,
                "source": "RIVER",
            },
        )
    except Exception:
        pass

    return updated
