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

def assimilate_feedback(limit_minutes: int = 10) -> int:
    """
    Stage 5 Assimilator:
    1️⃣ Read recent feedback_tick & cashout_tick events from mastery_cache.
    2️⃣ Aggregate live market bias/liability signals.
    3️⃣ Update mastery_posteriors with rolling live_pnl_ratio.
    4️⃣ Emit feedback_assimilated event via event_sink.
    """
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    # 1️⃣ Gather recent events (binding-safe, f-string for LIMIT window)
    try:
        rows = con.execute(f"""
            SELECT event_type, json_payload
              FROM mastery_cache
             WHERE event_type IN ('feedback_tick','cashout_tick')
               AND ts >= datetime('now','-{int(limit_minutes)} minute','utc')
          ORDER BY ts DESC
        """).fetchall()
    except Exception as e:
        print(f"[feedback_assimilator] query warn: {e}")
        con.close()
        return 0

    if not rows:
        con.close()
        return 0

# === PATCH START ==============================================================
# 📍 TARGET: engines/mastery/feedback_assimilator.py : assimilate_feedback()
# 🔎 SEARCH: "# 2️⃣ Aggregate by market"
# 📆 PATCHED: 2025-12-03 — v7-correct final-outcome runner-level assimilation
# 🧠 SUMMARY:
#   • Runs ONLY after market completion
#   • Aggregates runner-level outcomes (not market-level)
#   • Builds correct bin_key = marketId|selectionId
#   • Updates mastery_posteriors per bin_key (v7-compatible)
#   • Replaces mid-race “marketId==bin_key” bug
# ==============================================================================

    # 2️⃣ Aggregate by *completed market*
    # Old behaviour aggregated mid-race → WRONG.
    # New: only process markets whose outcome is FINAL.
    markets = {}
    for r in rows:
        try:
            data = json.loads(r["json_payload"])
        except Exception:
            continue

        mid = data.get("marketId")
        if not mid:
            continue

        # Skip markets not yet settled (we require final runner outcomes)
        settled = data.get("market_status") == "CLOSED" or data.get("is_complete") is True
        if not settled:
            continue

        # Collect by market for final aggregation
        m = markets.setdefault(mid, {"cashout": [], "liability": []})
        if "cashout_total" in data:
            m["cashout"].append(float(data["cashout_total"]))
        if "liability_total" in data:
            m["liability"].append(float(data["liability_total"]))

    if not markets:
        con.close()
        return 0

    # 3️⃣ Runner-level bin_key update
    cur = con.cursor()

    # ensure column exists
    cur.execute("PRAGMA table_info(mastery_posteriors)")
    cols = [r[1] for r in cur.fetchall()]
    if "live_pnl_ratio" not in cols:
        cur.execute("ALTER TABLE mastery_posteriors ADD COLUMN live_pnl_ratio REAL DEFAULT 0.0;")
        con.commit()

    # canonical weighting
    from engines.mastery.canonical_digest import weight_for_letter

    for mid, vals in markets.items():

        # Get all runners for this market
        try:
            runners = con.execute("""
                SELECT selectionId
                  FROM market_data
                 WHERE marketId=?""", (mid,)
            ).fetchall()
        except Exception:
            runners = []

        if not runners:
            continue

        # Compute final market-level ratios
        cash = sum(vals["cashout"]) / max(1, len(vals["cashout"]))
        liab = sum(vals["liability"]) / max(1, len(vals["liability"]))
        pnl_ratio = cash / liab if liab else 0.0

        # canonical weight (letter derived from market code — stable)
        letter = str(mid)[5:6].upper() if len(mid) > 5 else "?"
        pnl_ratio *= weight_for_letter(letter)

        # Now apply to EACH RUNNER (correct v7 logic)
        for row in runners:
            sid = str(row["selectionId"])
            bin_key = f"{mid}|{sid}"

            cur.execute("""
                INSERT INTO mastery_posteriors (bin_key, live_pnl_ratio, updated_at)
                     VALUES (?, ?, datetime('now','utc'))
                ON CONFLICT(bin_key)
                DO UPDATE SET
                    live_pnl_ratio = excluded.live_pnl_ratio,
                    updated_at     = datetime('now','utc');
            """, (bin_key, float(pnl_ratio)))

    con.commit()
    count = sum(len(r["cashout"]) > 0 or len(r["liability"]) > 0 for r in markets.values())
    con.close()

    # emit event unchanged…
# === PATCH END ================================================================


    # 3️⃣ Update mastery_posteriors (ensure column exists first)
    try:
        cur = con.cursor()
        cur.execute("PRAGMA table_info(mastery_posteriors);")
        cols = [r[1] for r in cur.fetchall()]
        if "live_pnl_ratio" not in cols:
            cur.execute("ALTER TABLE mastery_posteriors ADD COLUMN live_pnl_ratio REAL DEFAULT 0.0;")
            con.commit()

        # === PATCH START ===
        # 📍 TARGET: engines/mastery/feedback_assimilator.py
        # 📆 PATCHED: 2025-10-25T18:30Z — Canonical weighting integration
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        from engines.mastery.canonical_digest import weight_for_letter

        for mid, vals in markets.items():
            letter = str(mid)[5:6].upper() if len(mid) > 5 else "?"
            cash = sum(vals["cashout"]) / max(1, len(vals["cashout"]))
            liab = sum(vals["liability"]) / max(1, len(vals["liability"]))
            pnl_ratio = cash / liab if liab else 0.0

            # Apply canonical weighting to pnl_ratio
            weight = weight_for_letter(letter)
            pnl_ratio *= weight


            cur.execute("""
                INSERT INTO mastery_posteriors (bin_key, updated_at, live_pnl_ratio)
                     VALUES (?, datetime('now','utc'), ?)
                ON CONFLICT(bin_key)
                DO UPDATE SET
                     live_pnl_ratio = excluded.live_pnl_ratio,
                     updated_at = datetime('now','utc');
            """, (mid, pnl_ratio))

        con.commit()
        count = len(markets)
    except Exception as e:
        print(f"[feedback_assimilator] update warn: {e}")
        count = 0
    finally:
        con.close()

    # 4️⃣ Emit assimilation event
    try:
        from engines.mastery import event_sink
        ts = datetime.now(timezone.utc).isoformat()
        payload = {"ts": ts, "count": count, "source": "LIVE"}
        event_sink.emit("feedback_assimilated", payload)
    except Exception as e:
        print(f"[feedback_assimilator] emit warn: {e}")

    return count
# === PATCH END ===

