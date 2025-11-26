#!/usr/bin/env python3
import os, json, sqlite3
from datetime import datetime, timedelta, timezone




# Paths
AUTO_DB = "data/autoscalp_gui.db"
REPORT_DIR = "data/reports"
POST_DIR = "data/posteriors"
LATEST_REP = sorted(
    [f for f in os.listdir("data") if f.startswith("replay_report_")],
    reverse=True
)[0]
REPORT_PATH = os.path.join("data", LATEST_REP)


def load_json(path):
    with open(path, "r") as f:
        return json.load(f)
def get_market_pnl(day: str, marketId: str, selectionId: str|None=None) -> float:
    """Return settled P&L from settlements.db for given marketId[/selectionId]."""
    con = sqlite3.connect("data/settlements.db"); con.row_factory = sqlite3.Row
    try:
        if selectionId:
            row = con.execute("""
                SELECT SUM(profit) AS net
                  FROM bf_cleared_orders
                 WHERE marketId=? AND selectionId=? AND date(settledDate)=?
            """, (marketId, selectionId, day)).fetchone()
        else:
            row = con.execute("""
                SELECT net FROM v_settle_mkt_day
                 WHERE marketId=? AND day=?
            """, (marketId, day)).fetchone()
        return float(row["net"] or 0.0) if row else 0.0
    finally:
        con.close()

def calc_trade_pnl(entry_odds, stake, side, won: bool) -> float:
    """
    Simple settlement P&L:
      BACK win: (odds-1) * stake
      BACK lose: -stake
      LAY win (selection loses): +stake
      LAY lose (selection wins): -(odds-1) * stake
    """
    try:
        if side == "BACK":
            return (float(entry_odds)-1.0) * float(stake) if won else -float(stake)
        elif side == "LAY":
            return float(stake) if not won else -(float(entry_odds)-1.0) * float(stake)
    except Exception:
        return 0.0
    return 0.0

# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: replay_digest.py
# 🔎 SEARCH: ^def _log_outcome\(.*\):\n(?:[ \t].*\n)+?logger\.info\(f"\[digest\].*"\)
# ─────────────────────────────────────────────────────────────────────────────
def _log_outcome(mid, sid, horse_name, pnl, outcome, logger):
    """
    Log outcome with runner_history liability context (form, net_pnl).
    """
    logger.info(f"[digest] OUTCOME {mid}/{sid} {horse_name} → {outcome} pnl={pnl}")

    try:
        import sqlite3, os
        from engines.config_paths import autoscalp_db
        con = sqlite3.connect(autoscalp_db(), timeout=5)
        row = con.execute(
            "SELECT net_pnl, form_string FROM runner_history WHERE runner_name=? AND selectionId=?",
            (horse_name, sid)
        ).fetchone()
        con.close()
        if row:
            logger.info(f"[FORM] {mid}/{sid} {horse_name} net_pnl={row[0]:.2f} form={row[1]}")
    except Exception as e:
        logger.warning(f"[FORM] {mid}/{sid} {horse_name} lookup failed: {e}")
# === PATCH END ===



def normalize_pnl(row_or_value):
    """
    Shim: unifies P&L references (net_pl, net_pnl, pnl, numeric).
    Always returns a float.
    """
    try:
        if isinstance(row_or_value, dict):
            return float(
                row_or_value.get("net_pl") or
                row_or_value.get("net_pnl") or
                row_or_value.get("pnl") or 0.0
            )
        elif hasattr(row_or_value, "__getitem__"):  # sqlite3.Row
            return float(
                row_or_value.get("net_pl") or
                row_or_value.get("net_pnl") or
                row_or_value.get("pnl") or 0.0
            )
        return float(row_or_value or 0.0)
    except Exception:
        return 0.0

# === PATCH START: Playbooks Integration → mastery_outcomes_raw =================
# 📍 TARGET: replay_digest.py
# 🔎 SEARCH: def main\(\):
# 📆 PATCHED: 2025-10-16T23:59Z — integrate Playbooks results into raw outcomes
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def main():
    outcomes = load_json(REPORT_PATH)

# === PATCH START: Playbooks → mastery_outcomes_raw (auto-schema fix) ===========
# 📍 TARGET: replay_digest.py
# 🔎 SEARCH: === Playbooks Integration (Digest Stage) ===
# 📆 PATCHED: 2025-10-17T01:05Z — adds missing cols + safe insert
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # --- NEW: Playbooks Integration (auto-schema) -----------------------------
    try:
        print("\n=== Playbooks Integration (Digest Stage) ===")
        AUTO_DB = "data/autoscalp_gui.db"
        with sqlite3.connect(AUTO_DB, timeout=10) as con:
            con.row_factory = sqlite3.Row
            cur = con.cursor()

            # Ensure table exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mastery_outcomes_raw(
                    day TEXT, marketId TEXT, selectionId TEXT,
                    letter TEXT, class_band TEXT,
                    realized_ticks REAL,
                    success INTEGER, stoploss_hit INTEGER, hedge_hit INTEGER,
                    entry_px REAL, pnl REAL,
                    weight REAL, priority INTEGER,
                    created_at TEXT DEFAULT (datetime('now','utc'))
                );
            """)

            # Dynamically add missing columns
            existing = {r[1] for r in cur.execute("PRAGMA table_info(mastery_outcomes_raw);")}
            if "confidence" not in existing:
                cur.execute("ALTER TABLE mastery_outcomes_raw ADD COLUMN confidence REAL;")
            if "source" not in existing:
                cur.execute("ALTER TABLE mastery_outcomes_raw ADD COLUMN source TEXT DEFAULT 'REPLAY';")
            if "class_band" not in existing:
                cur.execute("ALTER TABLE mastery_outcomes_raw ADD COLUMN class_band TEXT;")
            con.commit()

            # Pull canonical Playbooks rows
            rows = cur.execute("""
                SELECT day, marketId, selectionId,
                       letter,
                       oc_stage_entry AS band,
                       odds_ticks     AS realized_ticks,
                       CASE WHEN net_pl>0 THEN 1 ELSE 0 END AS success,
                       CASE WHEN net_pl<0 THEN 1 ELSE 0 END AS stoploss_hit,
                       pair_complete  AS hedge_hit,
                       entry_odds     AS entry_px,
                       net_pl         AS pnl,
                       confidence
                  FROM playbooks_settled
            """).fetchall()

            if not rows:
                print("[digest] no Playbooks rows found; skipping raw update.")
            else:
                print(f"[digest] inserting {len(rows):,} Playbooks rows into mastery_outcomes_raw …")
                payload = [
                    {
                        "day": r["day"],
                        "marketId": r["marketId"],
                        "selectionId": r["selectionId"],
                        "letter": r["letter"],
                        "class_band": r["band"],
                        "realized_ticks": r["realized_ticks"],
                        "success": r["success"],
                        "stoploss_hit": r["stoploss_hit"],
                        "hedge_hit": r["hedge_hit"],
                        "entry_px": r["entry_px"],
                        "pnl": r["pnl"],
                        "confidence": r["confidence"],
                        "weight": 1.0,
                        "priority": 99
                    }
                    for r in rows
                ]

                cur.executemany("""
                    INSERT INTO mastery_outcomes_raw(
                        day, marketId, selectionId, letter, class_band,
                        realized_ticks, success, stoploss_hit, hedge_hit,
                        entry_px, pnl, confidence, weight, priority, source
                    )
                    VALUES (
                        :day, :marketId, :selectionId, :letter, :class_band,
                        :realized_ticks, :success, :stoploss_hit, :hedge_hit,
                        :entry_px, :pnl, :confidence, :weight, :priority, 'PLAYBOOKS'
                    );
                """, payload)
                con.commit()
                print("[digest] ✅ Playbooks data appended to mastery_outcomes_raw with source=PLAYBOOKS.")

    except Exception as e:
        print(f"[digest] playbooks→raw warn: {e}")
# === PATCH END =================================================================

    # ───────────────────────────────────────────────────────────────────────────
    # Continue with the rest of digest() logic below …
# === PATCH END =================================================================

    # --- Build v_digest_sources (TEMP view) ---
    root = "data"
    gui = os.path.join(root, "autoscalp_gui.db")
    bets = os.path.join(root, "bets.db")
    setts = os.path.join(root, "settlements.db")

    con = sqlite3.connect(gui)
    con.row_factory = sqlite3.Row
    con.execute(f"ATTACH '{bets}' AS betsdb")
    con.execute(f"ATTACH '{setts}' AS setdb")
    con.executescript("""
    DROP VIEW IF EXISTS v_digest_sources;
    CREATE TEMP VIEW v_digest_sources AS
    SELECT
        o.id AS order_id,
        o.marketId,
        o.selectionId,
        o.side AS order_side,
        o.role,
        o.entry_stake,
        o.exit_stake,
        o.entry_odds,
        o.exit_odds,
        o.net_pl AS order_net_pl,
        o.realized_pnl AS order_realized,
        o.stop_loss_triggered AS stoploss,
        o.greened_up AS greened,
        o.entry_status,
        o.exit_status,
        o.hedge_of,
        b.horse_name,
        b.marketStartTime AS race_off,
        b.anchor_odd,
        s.sizeSettled,
        s.profit,
        s.commission,
        s.settledDate
    FROM orders o
    LEFT JOIN betsdb.bets b
           ON b.marketId = o.marketId
          AND CAST(b.selectionId AS TEXT)=CAST(o.selectionId AS TEXT)
    LEFT JOIN setdb.bf_cleared_orders s
           ON s.marketId = o.marketId
          AND CAST(s.selectionId AS TEXT)=CAST(o.selectionId AS TEXT);
    """)
    print("[digest] TEMP view v_digest_sources ready.")

    # --- Integrity check ---
    counts = con.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN profit IS NOT NULL THEN 1 ELSE 0 END) AS settled,
            SUM(CASE WHEN profit IS NULL THEN 1 ELSE 0 END) AS unsettled
        FROM v_digest_sources
    """).fetchone()
    print(f"[check] total={counts['total']} settled={counts['settled']} unsettled={counts['unsettled']}")

    # --- Core metrics from view ---
    pnl_today = con.execute("""
        SELECT ROUND(SUM(profit),2) AS pnl FROM v_digest_sources
         WHERE date(settledDate)=date('now','utc')
    """).fetchone()["pnl"] or 0.0

    win_rows = con.execute("""
        SELECT COUNT(*) AS n FROM v_digest_sources WHERE profit>0
    """).fetchone()["n"]
    loss_rows = con.execute("""
        SELECT COUNT(*) AS n FROM v_digest_sources WHERE profit<=0 AND profit IS NOT NULL
    """).fetchone()["n"]
    total_rows = con.execute("SELECT COUNT(*) AS n FROM v_digest_sources").fetchone()["n"]

    # Derive basic outcome counters for Replay Learning Report
    # These replicate the older JSON logic but now use live orders data
    hedges = con.execute("""
        SELECT COUNT(*) FROM orders
         WHERE role='CHILD' AND (entry_status='EXECUTED' OR exit_status='EXECUTED')
    """).fetchone()[0]

    stops = con.execute("""
        SELECT COUNT(*) FROM orders
         WHERE stop_loss_triggered=1
    """).fetchone()[0]

    # wins already reflected in profit>0 counts, but mirror for clarity
    wins = win_rows


    winrate = 100.0 * win_rows / total_rows if total_rows else 0.0

    print(f"PnL total: £{pnl_today:,.2f}")
    print(f"Winrate (settled): {winrate:.2f}%  wins={win_rows} losses={loss_rows}")

    # --- Hedge / Stoploss / Letter metrics ---
    # Hedge = child orders that actually executed (true secondary trades)
    hedges = con.execute("""
        SELECT COUNT(*) AS n FROM orders
         WHERE role='CHILD'
           AND (entry_status='EXECUTED' OR exit_status='EXECUTED')
    """).fetchone()["n"]

    # Stoploss = parent trades that exited negatively (flagged as triggered)
    stoploss = con.execute("""
        SELECT COUNT(*) AS n FROM orders
         WHERE role='PARENT' AND stop_loss_triggered=1
    """).fetchone()["n"]

    # Letters fired = strategy codes active today
    letters = {}
    for r in con.execute("""
        SELECT
            UPPER(COALESCE(source, 'UNK')) AS letter,
            COUNT(*) AS n
          FROM orders
         WHERE date(opened_at)=date('now','utc')
         GROUP BY UPPER(COALESCE(source, 'UNK'))
    """).fetchall():
        letters[r["letter"]] = r["n"]

    print(f"Hedges: {hedges:,} | Stoploss: {stoploss:,}")
    print(f"Letters fired: {letters}")

    # Feed computed stats forward for later summary use
    avg_net_pl = pnl_today / total_rows if total_rows else 0.0
    c_avg_pl   = avg_net_pl  # initial mirror; will update later if child avg differs
    total      = total_rows
    wins       = win_rows

    # ───────────────────────────────────────────────────────────────
    # Extended Live Metrics (exposure, efficiency, risk)
    # ───────────────────────────────────────────────────────────────

    # Ensure parent/child totals exist before metrics
    pc_init = con.execute("""
        SELECT 
          SUM(CASE WHEN role='PARENT' THEN 1 ELSE 0 END) AS parents,
          SUM(CASE WHEN role='CHILD'  THEN 1 ELSE 0 END) AS children
        FROM orders
    """).fetchone()
    total_parents = int(pc_init["parents"] or 0)
    total_children = int(pc_init["children"] or 0)

    # 1️⃣ Live exposure (open stake still active)
    exposure_row = con.execute("""
        SELECT
          SUM(entry_stake) AS total_exposure,
          COUNT(*) AS open_positions
        FROM orders
        WHERE exit_status IS NULL OR exit_status=''
    """).fetchone()
    live_exposure = float(exposure_row["total_exposure"] or 0.0)
    open_positions = int(exposure_row["open_positions"] or 0)

    # 2️⃣ Average stake size (parents vs children)
    stake_stats = con.execute("""
        SELECT
          AVG(CASE WHEN role='PARENT' THEN entry_stake END) AS avg_parent_stake,
          AVG(CASE WHEN role='CHILD'  THEN entry_stake END) AS avg_child_stake
        FROM orders
        WHERE entry_stake > 0
    """).fetchone()
    avg_parent_stake = float(stake_stats["avg_parent_stake"] or 0.0)
    avg_child_stake  = float(stake_stats["avg_child_stake"] or 0.0)

    # 3️⃣ Hedge efficiency (children matched ÷ parents placed)
    hedge_eff = (100.0 * total_children / total_parents) if total_parents else 0.0

    # 4️⃣ Unrealized P&L (orders still open)
    unrealized_row = con.execute("""
        SELECT SUM(CASE WHEN exit_status IS NULL OR exit_status=''
                        THEN COALESCE(net_pl,0) ELSE 0 END) AS unrealized_pnl
          FROM orders
    """).fetchone()
    unrealized_pnl = float(unrealized_row["unrealized_pnl"] or 0.0)

    # 5️⃣ Execution latency (time difference between parent open and child open)
    latency_row = con.execute("""
        SELECT AVG(
            julianday(c.opened_at) - julianday(p.opened_at)
        ) * 86400 AS avg_latency_s
          FROM orders p
          JOIN orders c ON c.hedge_of = p.id
         WHERE p.role='PARENT' AND c.role='CHILD'
    """).fetchone()
    avg_latency_s = float(latency_row["avg_latency_s"] or 0.0)

    # 6️⃣ Strategy breadth (letters active today)
    letters_active = len(letters)

    # ───────────────────────────────────────────────────────────────
    # Print summary
    # ───────────────────────────────────────────────────────────────
    print("\n=== Extended Metrics ===")
    print(f"Open positions      : {open_positions}")
    print(f"Live exposure       : £{live_exposure:,.2f}")
    print(f"Avg parent stake    : £{avg_parent_stake:,.2f}")
    print(f"Avg child stake     : £{avg_child_stake:,.2f}")
    print(f"Hedge efficiency    : {hedge_eff:.1f}%")
    print(f"Unrealized P&L      : £{unrealized_pnl:,.2f}")
    print(f"Avg hedge latency   : {avg_latency_s:.1f}s")
    print(f"Letters active      : {letters_active}")


    # --- Parent/Child mismatches ---
    pc = con.execute("""
        SELECT 
          SUM(CASE WHEN role='PARENT' THEN 1 ELSE 0 END) AS parents,
          SUM(CASE WHEN role='CHILD'  THEN 1 ELSE 0 END) AS children
        FROM v_digest_sources
    """).fetchone()
    total_parents = pc["parents"] or 0
    total_children = pc["children"] or 0
    mismatch_total = abs(total_parents - total_children)
    matched_conv = (100.0 * total_children / total_parents) if total_parents else 0.0

# ───────────────────────────────────────────────────────────────
# 📍 TARGET: replay_digest.py
# 🔎 SEARCH: print(f"=== Parent/Child Summary ===")
# 📆 PATCHED: 2025-10-14
# ───────────────────────────────────────────────────────────────
    # === Canonical P&L Summary (from calc_pnl_context) ===
    from engines.pnl_engine import calc_pnl_context

    try:
        print("\n=== Canonical P&L Summary ===")
        context = calc_pnl_context(day='yesterday')   # full context dictionary
        total_pnl = round(sum(context.values()), 2)

        # per-letter roll-up
        per_letter = {}
        for (letter, *rest), pnl in context.items():
            per_letter[letter] = per_letter.get(letter, 0.0) + pnl

        print(f"Total P&L (canonical): £{total_pnl:,.2f}")
        for letter, pnl in sorted(per_letter.items()):
            print(f"  {letter:<2} → £{pnl:8.2f}")

        # optional: dump a table for posteriors
        out_path = os.path.join(REPORT_DIR, "digest_context_latest.json")
        os.makedirs(REPORT_DIR, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(
                {str(k): v for k, v in context.items()},
                f, indent=2
            )
        print(f"[digest] contextual P&L saved → {out_path}")

    except Exception as e:
        print(f"[digest] canonical P&L warn: {e}")


    print("\n=== Parent/Child Summary ===")
    print(f"  Parents total : {total_parents}")
    print(f"  Children total: {total_children}")
    print(f"  Mismatch total: {mismatch_total}")
    print(f"  Match conversion: {matched_conv:.1f}%")

    # --- Generate market_map dynamically (replaces old undefined var) ---
    market_map = {}
    for r in con.execute("""
        SELECT marketId, selectionId, role, entry_odds, entry_status, exit_status, opened_at
        FROM orders
    """):
        key = (r["marketId"], r["selectionId"])
        market_map.setdefault(key, {"parents": [], "children": []})
        market_map[key]["parents" if r["role"] == "PARENT" else "children"].append(dict(r))

    con.commit()



    # Opportunities
    opps = con.execute("""
        SELECT SUM(opportunities) AS opps,
               SUM(taken) AS taken,
               SUM(conversion) AS conv
          FROM indicators_opportunities
         WHERE day IN (SELECT MAX(day) FROM indicators_opportunities)
    """).fetchone()

    # Error tags
    tags = con.execute("""
        SELECT tag, COUNT(*) AS n
          FROM loss_tags
         WHERE day IN (SELECT MAX(day) FROM loss_tags)
         GROUP BY tag
    """).fetchall()

    # --- Resolve day and fetch orders ---
    day_row = con.execute("SELECT MAX(date(opened_at)) AS d FROM orders").fetchone()
    day = (day_row["d"] if day_row and day_row["d"]
           else datetime.now().astimezone().date().isoformat())
    dow = datetime.strptime(day, "%Y-%m-%d").strftime("%A")  # Monday, Tuesday, ...

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 📍 TARGET: replay_digest.py
    # 🔎 SEARCH: # --- Balance P&L via settlements truth ---
    # 📆 PATCHED: 2025-10-06
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # --- Balance P&L via settlements truth ---
    pnl_total = 0.0
    try:
        row = con.execute("""
            SELECT SUM(net) AS net
              FROM setdb.v_settle_day
             WHERE day = (SELECT MAX(day) FROM setdb.v_settle_day)
        """).fetchone()
        if row and row["net"] is not None:
            pnl_total = float(row["net"])
    except Exception as e:
        print(f"[digest] settlements pnl warn: {e}")
    print(f"PnL total: £{pnl_total:,.2f}")
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


    orders = con.execute("""
        SELECT marketId, selectionId, role, customerOrderRef,
               entry_odds, entry_stake, entry_status, exit_status,
               COALESCE(net_pl,0) AS net_pl, opened_at
          FROM orders
         WHERE date(opened_at)=(SELECT MAX(date(opened_at)) FROM orders)
    """).fetchall()

    # --- Parent/Child Mismatch Summary (with settlement truth) ---
    rows = con.execute("""
        SELECT o.marketId, o.selectionId,
               SUM(CASE WHEN o.role='PARENT' THEN 1 ELSE 0 END) AS parents,
               SUM(CASE WHEN o.role='CHILD'  THEN 1 ELSE 0 END) AS children,
               SUM(s.profit) AS net_pnl
        FROM orders o
        LEFT JOIN setdb.bf_cleared_orders s
          ON o.marketId = s.marketId
         AND o.selectionId = s.selectionId
        GROUP BY o.marketId, o.selectionId
    """).fetchall()

    total_parents   = sum(r["parents"]  for r in rows)
    total_children  = sum(r["children"] for r in rows)
    mismatch_total  = sum(1 for r in rows if r["parents"] != r["children"])
    market_sum      = sum(float(r["net_pnl"] or 0.0) for r in rows)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 📍 TARGET: replay_digest.py
    # 🔎 SEARCH: # --- Parent/Child Mismatch Summary (with settlement truth) ---
    # 📆 PATCHED: 2025-10-06
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print("\n=== Parent/Child Mismatch Summary ===")

    pc = con.execute("""
        SELECT 
          SUM(CASE WHEN role='PARENT' THEN 1 ELSE 0 END) AS parents,
          SUM(CASE WHEN role='CHILD'  THEN 1 ELSE 0 END) AS children
        FROM orders
        WHERE date(opened_at)=(SELECT MAX(date(opened_at)) FROM orders)
    """).fetchone()

    total_parents  = pc["parents"] or 0
    total_children = pc["children"] or 0
    mismatch_total = abs(total_parents - total_children)
    matched_conv   = (100.0 * total_children / total_parents) if total_parents else 0.0

    print(f"  Parents total : {total_parents}")
    print(f"  Children total: {total_children}")
    print(f"  Mismatched sids: {mismatch_total}")
    print(f"  Matched Conversion : {matched_conv:.1f}%")

    rows = con.execute("""
        SELECT o.role,
               SUM(s.profit) AS net_pnl
        FROM orders o
        LEFT JOIN setdb.bf_cleared_orders s
          ON o.marketId=s.marketId AND o.selectionId=s.selectionId
        WHERE date(o.opened_at)=(SELECT MAX(date(opened_at)) FROM orders)
        GROUP BY o.role
    """).fetchall()

    parents_pnl  = 0.0
    children_pnl = 0.0


    avg_parent_pnl = (parents_pnl / total_parents) if total_parents else 0.0
    print(f"  Net P&L        : £{parents_pnl:.2f} (avg £{avg_parent_pnl:.2f} per parent)")
    print(f"  Parent-set P&L   : £{parents_pnl:.2f}")
    print(f"  Children-set P&L : £{children_pnl:.2f}")
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 📍 TARGET: replay_digest.py
    # 🔎 SEARCH: rows = scon.execute(""" SELECT o.role ...
    # 📆 PATCHED: 2025-10-06
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # settlement truth per parent/child (join autoscalp orders → settlements)
    acon = sqlite3.connect(AUTO_DB)
    acon.row_factory = sqlite3.Row
    acon.execute("ATTACH 'data/settlements.db' AS setdb")


    rows = acon.execute("""
        SELECT o.role,
               SUM(s.profit) AS net_pnl
        FROM orders o
        LEFT JOIN setdb.bf_cleared_orders s
          ON o.marketId=s.marketId AND o.selectionId=s.selectionId
        WHERE date(o.opened_at)=(SELECT MAX(date(opened_at)) FROM orders)
        GROUP BY o.role
    """).fetchall()
    acon.close()

    parents_pnl  = 0.0
    children_pnl = 0.0
    for r in rows:
        if r["role"] == "PARENT": parents_pnl  = float(r["net_pnl"] or 0.0)
        if r["role"] == "CHILD":  children_pnl = float(r["net_pnl"] or 0.0)

    avg_parent_pnl = (parents_pnl / total_parents) if total_parents else 0.0
    print(f"  Net P&L        : £{parents_pnl:.2f} (avg £{avg_parent_pnl:.2f} per parent)")
    print(f"  Parent-set P&L   : £{parents_pnl:.2f}")
    print(f"  Children-set P&L : £{children_pnl:.2f}")

# ───────────────────────────────────────────────────────────────
# 📍 TARGET: replay_digest.py
# 🔎 SEARCH: print(f"  Net P&L       : £{total_net_pl:.2f} (avg £{avg_net_pl:.2f} per parent)")
# 📆 PATCHED: 2025-10-01
# ───────────────────────────────────────────────────────────────

    # --- PATCH START: pull settled PnL from settlements.db ---
    try:
        SETTLE_DB = "data/settlements.db"
        scon = sqlite3.connect(SETTLE_DB); scon.row_factory = sqlite3.Row
        day = datetime.now().astimezone().date().isoformat()


        settle = scon.execute("""
            SELECT SUM(net) AS net, AVG(net) AS avg_net
              FROM v_settle_mkt_day
             WHERE day = ?
        """, (day,)).fetchone()
        scon.close()

        if settle and settle["net"] is not None:
            total_net_pl = float(settle["net"])
            avg_net_pl   = float(settle["avg_net"])
    except Exception as e:
        print(f"[digest] settlements warn: {e}")
    # --- PATCH END ---

    # set defaults so they're always defined
    c_net_pl, c_avg_pl = 0.0, 0.0

    # --- PATCH START: pull child PnL from settlements.db ---
    try:
        scon = sqlite3.connect("data/settlements.db"); scon.row_factory = sqlite3.Row
        day = datetime.now().astimezone().date().isoformat()  # timezone-aware UTC
        settle = scon.execute("""
            SELECT SUM(net) AS net, AVG(net) AS avg_net
              FROM v_settle_mkt_day
             WHERE day = ?
        """, (day,)).fetchone()
        scon.close()
        if settle and settle["net"] is not None:
            c_net_pl = float(settle["net"])
            c_avg_pl = float(settle["avg_net"])
    except Exception as e:
        print(f"[digest] settlements child warn: {e}")
    # --- PATCH END ---


    # --- Child outcomes summary ---
    child_outcomes = con.execute("""
        SELECT
            COUNT(*) AS total_children,
            SUM(CASE WHEN entry_status='EXECUTED' OR exit_status='EXECUTED' THEN 1 ELSE 0 END) AS matched,
            SUM(CASE WHEN entry_status='CANCELLED' OR exit_status='CANCELLED' THEN 1 ELSE 0 END) AS cancelled,

            SUM(net_pl) AS total_pnl,
            AVG(net_pl) AS avg_pnl
        FROM orders
        WHERE role='CHILD'
          AND date(opened_at)=(SELECT MAX(date(opened_at)) FROM orders)
    """).fetchone()

    total_children = child_outcomes['total_children'] or 0
    matched        = child_outcomes['matched'] or 0
    cancelled      = child_outcomes['cancelled'] or 0
    c_net_pl       = normalize_pnl(child_outcomes['total_pnl'])
    c_avg_pl       = normalize_pnl(child_outcomes['avg_pnl'])

    print(f"  Total children : {total_children}")
    print(f"  Matched        : {matched}")
    print(f"  Cancelled      : {cancelled}")
    print(f"  Net P&L        : £{c_net_pl:.2f} (avg £{c_avg_pl:.2f} per child)")


    if total_children > 0:
        cancel_rate = 100.0 * cancelled / total_children
        print(f"  Cancel rate    : {cancel_rate:.1f}%")


  

    # --- Replay Focus flags ---
    print("\nReplay Focus:")
    if total_parents > 0:
        if (mismatch_total / total_parents) > 0.20:
            print("  ⚠️ High unpaired rate → Check why parents aren’t generating children.")
        if (total_children > 0) and ('cancel_rate' in locals()) and (cancel_rate > 20.0):
            print("  ⚠️ High child cancel rate.")
        if avg_net_pl < 0:
            print("  ⚠️ Negative average P&L per parent → tighten filters / stoploss rules.")



    # --- Parent/Child Match Quality Summary ---
    try:
        con = sqlite3.connect(AUTO_DB); con.row_factory = sqlite3.Row
        totals = con.execute("""
            WITH parent_status AS (
                SELECT customerOrderRef,
                       COUNT(*) FILTER (WHERE role='CHILD' AND (entry_status='EXECUTED' OR exit_status='EXECUTED')) AS matched_children,
                       COUNT(*) FILTER (WHERE role='PARENT') AS parent_count
                  FROM orders
                 WHERE date(opened_at)=(SELECT MAX(date(opened_at)) FROM orders)
                 GROUP BY customerOrderRef
            )
            SELECT COUNT(*) AS total_parents,
                   SUM(CASE WHEN matched_children>0 THEN 1 ELSE 0 END) AS matched_parents
              FROM parent_status
        """).fetchone()
        con.close()

        total_parents = totals["total_parents"] or 0
        matched_parents = totals["matched_parents"] or 0
        unpaired_parents = total_parents - matched_parents
        pct_match = (100.0 * matched_parents / total_parents) if total_parents else 0.0

        print("\n=== Parent/Child Match Performance ===")
        parents_total = total_parents
        parents_matched = min(total_children, total_parents)   # each child implies one matched parent
        parents_unpaired = max(0, total_parents - total_children)
        match_rate = (100.0 * parents_matched / total_parents) if total_parents else 0.0
        print(f"  Parents total     : {parents_total}")
        print(f"  Parents matched   : {parents_matched}")
        print(f"  Parents unpaired  : {parents_unpaired}")
        print(f"  ✅ Match Rate      : {match_rate:.1f}%")
        if match_rate == 100.0:
            print("  🎯 Gold standard: All parents matched with children.")


        # --- Persist match metrics + parent/child tags into loss_tags ---
        try:
            con3 = sqlite3.connect(AUTO_DB)
            cur = con3.cursor()
            today = outcomes[0]["day"] if outcomes else datetime.now().date().isoformat()
            seen = set()
            persisted = []
            taglist = []

            # 1) Persist match-rate bucket
            bucket = int(matched_conv // 10) * 10
            tag = f"match_rate:{bucket}-{bucket+10}"
            cur.execute("""
                INSERT INTO loss_tags(day, marketId, selectionId, tag, created_at)
                VALUES (?,?,?,?,datetime('now','utc'))
                ON CONFLICT(day, marketId, selectionId, tag) DO NOTHING
            """, (day, "ALL", "0", tag))
            persisted.append({"mid": "ALL", "sid": "0", "tag": tag})

            # 2) Parent/child tags
            for (mid, sid), st in market_map.items():
                pcount, ccount = len(st["parents"]), len(st["children"])
                pnl = get_market_pnl(today, mid, sid)

                
                if pcount > 0 and pcount == ccount:
                    taglist.append("fully_hedged")
                    tick_diffs = []
                    for p in st["parents"]:
                        for c in st["children"]:
                            try:
                                diff = abs((p["entry_odds"] or 0) - (c["entry_odds"] or 0))
                                if diff < 0.1:
                                    tick_diffs.append(diff)
                            except:
                                pass
                    if len(tick_diffs) == pcount:
                        taglist.append("perfect_match")
                elif pcount > ccount:
                    taglist.append("unpaired_parent")
                    if pnl < 0:
                        taglist.append("unpaired_parent_loss")
                    if st["parents"] and st["children"]:
                        last_parent = max([p["opened_at"] for p in st["parents"]])
                        last_child  = max([c["opened_at"] for c in st["children"]])
                        if last_parent > last_child:
                            taglist.append("late_unpaired")

                # persist tags for this mid/sid
                for tag in taglist:
                    key = (today, mid, sid, tag)
                    if key in seen:
                        continue
                    seen.add(key)
                    cur.execute("""
                        INSERT INTO loss_tags(day, marketId, selectionId, tag, created_at)
                        VALUES (?,?,?,?,datetime('now','utc'))
                        ON CONFLICT(day, marketId, selectionId, tag) DO NOTHING
                    """, (today, str(mid), str(sid), tag))
                    persisted.append({"mid": mid, "sid": sid, "tag": tag})

            con3.commit()
            con3.close()

            if persisted:
                print(f"\n[loss_tags updated — persisted {len(persisted)} tags]")
                for row in persisted[:20]:
                    print(f"  mid={row['mid']} sid={row['sid']} tag={row['tag']}")
                if len(persisted) > 20:
                    print(f"  … and {len(persisted)-20} more")
            else:
                print("\n[loss_tags updated — no tags persisted]")

        except Exception as e:
            print(f"[digest] loss_tags persist warn: {e}")

    except Exception as e:
        #print(f"[digest] loss_tags persist warn: {e}")
# ───────────────────────────────────────────────────────────────
# 📍 TARGET: replay_digest.py
# 🔎 SEARCH: # --- PATCH START: distance band + race breakdown ---
# 📆 PATCHED: 2025-10-01T23:55Z
# ───────────────────────────────────────────────────────────────

        # --- PATCH START: distance band + race breakdown ---
        try:
            BETS_DB = "data/bets.db"
            SETTLE_DB = "data/settlements.db"

            con2 = sqlite3.connect(BETS_DB); con2.row_factory = sqlite3.Row
            scon = sqlite3.connect(SETTLE_DB); scon.row_factory = sqlite3.Row

            day = datetime.now().astimezone().date().isoformat()
            rows = con2.execute("""
                SELECT b.marketId, b.market_name,
                       SUM(s.net) AS net
                  FROM bets b
                  LEFT JOIN st_runner_day_totals s
                    ON b.marketId = s.marketId
                 WHERE s.day = ?
                 GROUP BY b.marketId, b.market_name
            """, (day,)).fetchall()

            con2.close(); scon.close()

            # derive distance buckets
            def band_from_name(name: str) -> str:
                name = (name or "").lower()
                if name.startswith("5f") or name.startswith("6f"): return "sprint"
                if name.startswith("7f") or name.startswith("1m"): return "middle"
                if name.startswith("1m4f") or name.startswith("1m5f") or name.startswith("1m6f"): return "staying"
                if name.startswith("2m") or name.startswith("3m"): return "marathon"
                return "other"

            bands, races = {}, {}
            for r in rows:
                mname = r["market_name"] or "unknown"
                band = band_from_name(mname)
                bands[band] = bands.get(band, 0.0) + float(r["net"] or 0.0)
                races[mname] = races.get(mname, 0.0) + float(r["net"] or 0.0)

            print("\n🔎 Grouped by Distance Band")
            for band, net in sorted(bands.items(), key=lambda x: x[0]):
                print(f"  {band:<10} Net P&L £{net:8.2f}")
                if net < 0:
                    focus.append(f"Distance band loss: {band}")
                    focus_examples.append(("band", band, "distance_loss"))

            print("\n🔎 Individual Race Breakdown")
            for mname, net in sorted(races.items(), key=lambda x: x[0]):
                print(f"  {mname:<40} Net P&L £{net:8.2f}")
                if net < 0:
                    focus.append(f"Race loss: {mname}")
                    focus_examples.append(("race", mname, "race_loss"))
        except Exception as e:
            print(f"[digest] distance_band warn: {e}")
        # --- PATCH END ---


    # === Posterior deltas ===
    latest = os.path.join(POST_DIR, "mastery_snapshot_latest.json")
    prev   = sorted([f for f in os.listdir(POST_DIR) if f.startswith("mastery_snapshot_") and not f.endswith("latest.json")])
    deltas = []
    if prev and os.path.exists(latest):
        prev_snap = load_json(os.path.join(POST_DIR, prev[-1]))
        latest_snap = load_json(latest)
        for k,v in latest_snap.items():
            try:
                old = normalize_pnl(prev_snap.get(k,{}))
                new = normalize_pnl(v)

                diff = new - old
                if abs(diff) > 0.0:
                    deltas.append((diff,k))
            except Exception: pass
        deltas.sort(key=lambda x: -abs(x[0]))
        deltas = deltas[:5]

    # === Print Report ===
    print("\n📊 Replay Learning Report")
    day = outcomes[0]["day"] if outcomes else "?"
    print(f"Day: {day}")
    print(f"Outcomes: {total:,}")
    if total > 0:
        print(f"Win rate: {100.0 * wins / total:.1f}%")
    else:
        print("Win rate: N/A (no settled rows)")

    print(f"PnL total: £{pnl:,.2f}")
    print(f"Hedges: {hedges:,} | Stoploss: {stops:,}")
    print(f"Letters fired: {letters}")

# === PATCH START ===
# 📍 TARGET: replay_digest.py
# 🔎 SEARCH: ^\s*# --- Balance rollups ---
# 📆 PATCHED: 2025-10-15T01:35Z
# ───────────────────────────────────────────────────────────────
    # --- Balance rollups (safe-close version) ---
    try:
        print("\n=== Balance Rollups ===")
        with sqlite3.connect("data/autoscalp_gui.db", timeout=5) as con2:
            con2.row_factory = sqlite3.Row
            row = con2.execute("""
                SELECT MIN(balance) AS start_bal, MAX(balance) AS end_bal
                  FROM bank_state
                 WHERE day >= date('now','-100 day')
            """).fetchone()
            if row and row["start_bal"] and row["end_bal"]:
                pnl_100d = row["end_bal"] - row["start_bal"]
                print(f"100-day P&L: £{pnl_100d:.2f}")
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            print("[digest] balance rollup warn: database busy (retry later)")
        else:
            print(f"[digest] balance rollup warn: {e}")
    except Exception as e:
        print(f"[digest] balance rollup warn: {e}")

    # ensure any open handles from earlier are closed before mastery benchmarks
    try: con.close()
    except Exception: pass

    # --- Mastery Benchmarks / Winners (safe-close version) ---
    try:
        with sqlite3.connect("data/autoscalp_gui.db", timeout=5) as con3:
            con3.row_factory = sqlite3.Row
            total = con3.execute("SELECT COUNT(*) FROM v_digest_sources").fetchone()[0]
            print(f"\n=== Mastery Benchmarks ===")
            print(f"Records in digest sources: {total}")
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            print("[digest] mastery benchmarks warn: database busy (retry later)")
        else:
            print(f"[digest] mastery benchmarks warn: {e}")
    except Exception as e:
        print(f"[digest] mastery benchmarks warn: {e}")

    # --- Winners / Liabilities (safe-close version) ---
    try:
        with sqlite3.connect("data/autoscalp_gui.db", timeout=5) as con4:
            con4.row_factory = sqlite3.Row
            cur = con4.cursor()
            winners = cur.execute("""
                SELECT b.horse_name, SUM(s.profit) AS net_pnl
                  FROM betsdb.bets b
                  JOIN setdb.bf_cleared_orders s
                    ON b.marketId=s.marketId AND b.selectionId=s.selectionId
                 GROUP BY b.horse_name
                 ORDER BY net_pnl DESC LIMIT 3
            """).fetchall()
            if winners:
                print("\nTop Winners:")
                for w in winners:
                    print(f"  ✅ {w['horse_name']:<25} £{w['net_pnl']:.2f}")
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            print("[digest] winners/liabilities warn: database busy (retry later)")
        else:
            print(f"[digest] winners/liabilities warn: {e}")
    except Exception as e:
        print(f"[digest] winners/liabilities warn: {e}")
# === PATCH END ===



    if opps and opps["opps"]:
        taken_pct = 100.0*(opps["taken"] or 0)/(opps["opps"] or 1)
        conv_pct  = 100.0*(opps["conv"] or 0)/(opps["taken"] or 1)
        print("\n🔹 Opportunities")
        print(f"  Observed: {int(opps['opps'] or 0)}")
        print(f"  Taken: {int(opps['taken'] or 0)} ({taken_pct:.1f}%)")
        print(f"  Conversion: {conv_pct:.1f}%")



    if tags:
        print("\n🔹 Error Tags (latest day)")
        for r in tags:   # tags is a list of sqlite3.Row objects
            print(f"  {r['tag']:<15} {r['n']}")


    if deltas:
        print("\n🔹 Top 5 Bin Adjustments")
        for diff,k in deltas:
            print(f"  {k:<30} {diff:+.2f}")

    # --- Amalgamated Replay Focus (parents + children + tags + P&L) ---
    print("\n=== Replay Focus Areas ===")

    focus = []
    focus_examples = []

    # Parent-level issues
    if total_parents > 0:
        if mismatch_total / total_parents > 0.2:
            focus.append("High unpaired parents")
        if cancelled / total_parents > 0.1:
            focus.append("Too many parent cancels")
        if avg_net_pl < 0:
            focus.append("Negative average P&L per parent")
      

    # Child-level issues
    if total_children > 0:
        if cancel_rate > 20.0:
            focus.append("High child cancel rate")
        if c_avg_pl < 0:
            focus.append("Negative average P&L per child")



# ───────────────────────────────────────────────────────────────
# 📍 TARGET: replay_digest.py
# 🔎 SEARCH: # --- PATCH START: focus markets with negative settled PnL ---
# 📆 PATCHED: 2025-10-01
# ───────────────────────────────────────────────────────────────

    # --- PATCH START: focus markets with negative settled PnL ---
    try:
        scon = sqlite3.connect("data/settlements.db"); scon.row_factory = sqlite3.Row
        day = datetime.now().astimezone().date().isoformat()
        negs = scon.execute("""
            SELECT marketId, net
              FROM v_settle_mkt_day
             WHERE day = ? AND net < 0
             ORDER BY net ASC
             LIMIT 20
        """, (day,)).fetchall()
        scon.close()

        if negs:
            for r in negs:
                msg = f"Loss market {r['marketId']} net=£{r['net']:.2f}"
                focus.append(msg)
                # just collect for persistence later (sid=0 for market-level)
                focus_examples.append((r['marketId'], 0, "market_loss"))

    except Exception as e:
        print(f"[digest] settlements focus warn: {e}")
    # --- PATCH END ---



    if not focus:
        print("  ✅ No major focus issues — replay performed cleanly.")
    else:
        for f in focus:
            print("  ⚠️", f)


    # Show concrete examples (from outcomes JSON)
    try:
        print("\nExamples (mids/sids to review):")
        for o in outcomes[:10]:   # show first 10 only
            print(f"  mid={o['mid']} sid={o['sid']} letter={o['letter']} "
                  f"pnl={o['pnl']} success={o['success']} stop={o['stoploss']}")
    except Exception:
        pass

# ───────────────────────────────────────────────────────────────
# 📍 TARGET: replay_digest.py (or better: schema init code where loss_tags is created)
# 🔎 SEARCH: INSERT INTO loss_tags(day, marketId, selectionId, tag, created_at)
# 📆 PATCHED: 2025-10-01
# ───────────────────────────────────────────────────────────────

    # --- PATCH START: ensure UNIQUE index for loss_tags ---
    try:
        con_idx = sqlite3.connect(AUTO_DB)
        cur_idx = con_idx.cursor()
        cur_idx.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_loss_tags_day_mid_sid_tag
            ON loss_tags(day, marketId, selectionId, tag)
        """)
        con_idx.commit(); con_idx.close()
    except Exception as e:
        print(f"[digest] loss_tags index warn: {e}")
    # --- PATCH END ---

# === PATCH START ===
# 📍 TARGET: replay_digest.py
# 🔎 SEARCH: # --- PATCH START: markets with negative settled PnL ---
# 📆 PATCHED: 2025-10-02
# ───────────────────────────────────────────────────────────────

    # --- Market-level settled losses (unified) ---
    try:
        scon = sqlite3.connect("data/settlements.db"); scon.row_factory = sqlite3.Row
        day = datetime.now().astimezone().date().isoformat()
        negs = scon.execute("""
            SELECT marketId, net
              FROM v_settle_mkt_day
             WHERE day = ? AND net < 0
             ORDER BY net ASC
             LIMIT 50
        """, (day,)).fetchall()
        scon.close()

        if negs:
            for r in negs:
                msg = f"Loss market {r['marketId']} net=£{r['net']:.2f}"
                focus.append(msg)
                focus_examples.append((r['marketId'], 0, "market_loss"))
    except Exception as e:
        print(f"[digest] settlements market_loss warn: {e}")
# === PATCH END ===

# ───────────────────────────────────────────────────────────────
# 📍 TARGET: replay_digest.py
# 🔎 SEARCH: # --- Persist only problem mids/sids into loss_tags (closes the loop) ---
# 📆 PATCHED: 2025-10-02
# ───────────────────────────────────────────────────────────────

    # --- PATCH START: persist enriched problem + success tags ---
    try:
        con3 = sqlite3.connect(AUTO_DB)
        cur = con3.cursor()
        day = outcomes[0]["day"] if outcomes else datetime.now().astimezone().date().isoformat()

        focus_examples = []
        success_examples = []

        # === PROBLEM TAGS ====================================================
        for o in outcomes:
            # 1. Stoploss or fail
            if o.get("stoploss") or not o.get("success"):
                focus_examples.append((o["mid"], o["sid"], "stoploss_or_fail"))

            # 2. Parent/child mismatches (handled via unpaired/cancelled/loss)
            # Use totals computed above
# ───────────────────────────────────────────────────────────────
# 📍 TARGET: replay_digest.py
# 🔎 SEARCH: if mismatch_total / total_parents > 0.2:
# 📆 PATCHED: 2025-10-03
# ───────────────────────────────────────────────────────────────

        if mismatch_total / total_parents > 0.2:
            print("  ⚠️ High mismatch rate → Many parents didn’t generate children.")

            for o in outcomes:
                focus_examples.append((o["mid"], o["sid"], "unpaired_parent"))
            if cancelled / total_parents > 0.1:
                for o in outcomes:
                    focus_examples.append((o["mid"], o["sid"], "parent_cancelled"))
            if avg_net_pl < 0:
                for o in outcomes:
                    focus_examples.append((o["mid"], o["sid"], "parent_loss"))

        if total_children > 0:
            if cancel_rate > 20.0:
                for o in outcomes:
                    focus_examples.append((o["mid"], o["sid"], "child_cancelled"))
            if c_avg_pl < 0:
                for o in outcomes:
                    focus_examples.append((o["mid"], o["sid"], "child_loss"))

        # 3. Markets with negative settled PnL
        try:
            scon = sqlite3.connect("data/settlements.db"); scon.row_factory = sqlite3.Row
            negs = scon.execute("""
                SELECT marketId, net
                  FROM v_settle_mkt_day
                 WHERE day = ? AND net < 0
                 LIMIT 50
            """, (day,)).fetchall()
            scon.close()
            for r in negs:
                focus_examples.append((r["marketId"], r["selectionId"], "market_loss"))
        except Exception as e:
            print(f"[digest] settlements persist warn: {e}")

        # 4. High odds trades (>= 500)
        for o in outcomes:
            try:
                if float(o.get("entry_px", 0)) >= 500:
                    focus_examples.append((o["mid"], o["sid"], "high_odds"))
            except Exception:
                pass

        # 5. Tick distance rules
        for o in outcomes:
            t = int(o.get("target_ticks", 0) or 0)
            if t == 1: focus_examples.append((o["mid"], o["sid"], "tick1"))
            if t == 2: focus_examples.append((o["mid"], o["sid"], "tick2"))
            if t == 3: focus_examples.append((o["mid"], o["sid"], "tick3"))

        # 6. Direction breakdown
        for o in outcomes:
            code = (o.get("code") or "").upper()
            pnl  = float(o.get("pnl", 0.0) or 0.0)
            if code == "STEAM" and pnl < 0:
                focus_examples.append((o["mid"], o["sid"], "steam_loss"))
            elif code == "DRIFT" and pnl < 0:
                focus_examples.append((o["mid"], o["sid"], "drift_loss"))

        # 7. Enrichments (timing / exposure / fills / holds)
        for o in outcomes:
            if o.get("late_entry"):    focus_examples.append((o["mid"], o["sid"], "late_entry"))
            if o.get("early_entry"):   focus_examples.append((o["mid"], o["sid"], "early_entry"))
            if o.get("overexposed"):   focus_examples.append((o["mid"], o["sid"], "overexposed"))
            if o.get("underexposed"):  focus_examples.append((o["mid"], o["sid"], "underexposed"))
            if o.get("no_exit"):       focus_examples.append((o["mid"], o["sid"], "no_exit"))
            if o.get("partial_fill"):  focus_examples.append((o["mid"], o["sid"], "partial_fill"))
            if o.get("inplay_entry"):  focus_examples.append((o["mid"], o["sid"], "inplay_entry"))
            if o.get("vol_spike"):     focus_examples.append((o["mid"], o["sid"], "volatility_spike"))
            if o.get("long_hold"):     focus_examples.append((o["mid"], o["sid"], "long_hold"))
            if o.get("multi_parent"):  focus_examples.append((o["mid"], o["sid"], "multi_parent"))
            if o.get("class_band_loss"): focus_examples.append((o["mid"], o["sid"], "class_band_loss"))
            if o.get("fav_loss"):      focus_examples.append((o["mid"], o["sid"], "fav_loss"))
            if o.get("field_loss"):    focus_examples.append((o["mid"], o["sid"], "field_loss"))
            if o.get("distance_loss"): focus_examples.append((o["mid"], o["sid"], "distance_loss"))
            if o.get("race_loss"):     focus_examples.append((o["mid"], o["sid"], "race_loss"))

        # === SUCCESS TAGS ====================================================
        for o in outcomes:
            if o.get("success"):
                if o.get("pnl",0) > 0:
                    success_examples.append((o["mid"], o["sid"], "win_trade"))
                if (o.get("code") or "").upper() == "STEAM" and o.get("pnl",0) > 0:
                    success_examples.append((o["mid"], o["sid"], "steam_win"))
                if (o.get("code") or "").upper() == "DRIFT" and o.get("pnl",0) > 0:
                    success_examples.append((o["mid"], o["sid"], "drift_win"))
                t = int(o.get("target_ticks",0) or 0)
                if t == 1 and o.get("pnl",0) > 0: success_examples.append((o["mid"], o["sid"], "tick1_win"))
                if t == 2 and o.get("pnl",0) > 0: success_examples.append((o["mid"], o["sid"], "tick2_win"))
                if t == 3 and o.get("pnl",0) > 0: success_examples.append((o["mid"], o["sid"], "tick3_win"))

        # === MERGE & PERSIST =================================================
        all_examples = focus_examples + success_examples
        seen = set()
        persisted = []
        for (mid, sid, tag) in all_examples:
            key = (day, mid, sid, tag)
            if key in seen: continue
            seen.add(key)
            cur.execute("""
                INSERT INTO loss_tags(day, marketId, selectionId, tag, created_at)
                VALUES (?,?,?,?,datetime('now','utc'))
                ON CONFLICT(day, marketId, selectionId, tag) DO NOTHING
            """, (
                day,
                str(mid),
                str(sid) if sid not in (None,"","band","race") else "0",
                tag
            ))
            persisted.append({"day": day, "mid": mid, "sid": sid, "tag": tag})

        con3.commit(); con3.close()

        if persisted:
            print(f"\n[loss_tags updated — persisted {len(persisted)} tags]")
            for row in persisted[:20]:
                print(f"  mid={row['mid']} sid={row['sid']} tag={row['tag']}")
            if len(persisted) > 20:
                print(f"  … and {len(persisted)-20} more")
        else:
            print("\n[loss_tags updated — no tags persisted]")

    except Exception as e:
        print(f"[digest] loss_tags persist warn: {e}")
    # --- PATCH END ---



# ───────────────────────────────────────────────────────────────
# 📍 TARGET: replay_digest.py
# 🔎 SEARCH: # --- Summarize problem tags for report ---
# 📆 PATCHED: 2025-10-02T01:10Z
# ───────────────────────────────────────────────────────────────

        # --- Summarize + persist tags (problems + successes) ---
        tag_counts = {}
        for _mid, _sid, tag in focus_examples:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

        # --- NEW: build success_examples ---
        success_examples = []
        for o in outcomes:
            if o.get("success"):
                if o.get("pnl",0) > 0:
                    success_examples.append((o["mid"], o["sid"], "win_trade"))
                if (o.get("code") or "").upper() == "STEAM" and o.get("pnl",0) > 0:
                    success_examples.append((o["mid"], o["sid"], "steam_win"))
                if (o.get("code") or "").upper() == "DRIFT" and o.get("pnl",0) > 0:
                    success_examples.append((o["mid"], o["sid"], "drift_win"))
                t = int(o.get("target_ticks",0) or 0)
                if t in (1,2,3) and o.get("pnl",0) > 0:
                    success_examples.append((o["mid"], o["sid"], f"tick{t}_win"))

        # merge into tag_counts
        for _mid, _sid, tag in success_examples:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

        # master list of expected tags
        all_tags = [
            # problems
            "stoploss_or_fail", "unpaired_parent", "parent_cancelled", "parent_loss",
            "child_cancelled", "child_loss", "market_loss", "high_odds",
            "tick1", "tick2", "tick3", "steam_loss", "drift_loss",
            "late_entry", "early_entry", "overexposed", "underexposed",
            "no_exit", "partial_fill", "inplay_entry", "volatility_spike",
            "long_hold", "multi_parent", "class_band_loss", "fav_loss", "field_loss",
            "distance_loss", "race_loss",
            # successes
            "win_trade", "steam_win", "drift_win", "tick1_win", "tick2_win", "tick3_win"
        ]

        print("\n🔹 Problem / Success Tag Summary (this run)")
        for t in all_tags:
            n = tag_counts.get(t, 0)
            if n > 0:
                print(f"  {t:<20} {n}")

    # --- Parent/Child quality summary ---
    try:
        con = sqlite3.connect(AUTO_DB); con.row_factory = sqlite3.Row
        pc_tags = con.execute("""
            SELECT tag, COUNT(*) AS n
              FROM loss_tags
             WHERE day IN (SELECT MAX(day) FROM loss_tags)
               AND tag IN ('fully_hedged','perfect_match','unpaired_parent','unpaired_parent_loss','late_unpaired')
             GROUP BY tag
             ORDER BY tag
        """).fetchall()
        con.close()

        if pc_tags:
            print("\n🔹 Parent/Child Match Quality (latest day)")
            for r in pc_tags:
                print(f"  {r['tag']:<20} {r['n']}")
    except Exception as e:
        print(f"[digest] parent/child summary warn: {e}")


# === PATCH START ===
# 📍 TARGET: replay_digest.py
# 🔎 SEARCH: # === Mastery Benchmarks ===
# 📆 PATCHED: 2025-10-02
# ───────────────────────────────────────────────────────────────

    # === Mastery Benchmarks ===
# ───────────────────────────────────────────────────────────────
# 📍 TARGET: replay_digest.py
# 🔎 SEARCH: # === Mastery Benchmarks ===
# 📆 PATCHED: 2025-10-04
# ───────────────────────────────────────────────────────────────

        # --- PATCH START: enrich mastery_posteriors with distance + fav bins ---
        def band_from_name(name: str) -> str:
            """Map race distance string into band labels."""
            name = (name or "").lower()
            if name.startswith("5f") or name.startswith("6f"):
                return "sprint"
            if name.startswith("7f") or name.startswith("1m"):
                return "middle"
            if name.startswith("1m4f") or name.startswith("1m5f") or name.startswith("1m6f"):
                return "staying"
            if name.startswith("2m") or name.startswith("3m"):
                return "marathon"
            return "other"

        def derive_fav_rank_bin(market_outcomes):
            """Assign fav_rank_bin based on lowest odds snapshot (anchor/oc0)."""
            sorted_runners = sorted(
                market_outcomes,
                key=lambda o: float(o.get("anchor_odds") or o.get("oc0") or 9999)
            )
            bins = {}
            for idx, runner in enumerate(sorted_runners, 1):
                sid = runner.get("sid")
                if idx == 1:
                    bins[sid] = "fav1"
                elif idx <= 4:
                    bins[sid] = f"top{idx}"
                else:
                    bins[sid] = "field"
            return bins

        try:
            # Build per-market groupings from outcomes JSON
            market_groups = {}
            for o in outcomes:
                mid = o.get("mid"); sid = o.get("sid")
                if not mid: 
                    continue
                if mid not in market_groups:
                    market_groups[mid] = []
                market_groups[mid].append(o)

            # Update mastery_posteriors with distance & fav bins
            con2 = sqlite3.connect(AUTO_DB); con2.row_factory = sqlite3.Row
            cur2 = con2.cursor()
            for mid, runners in market_groups.items():
                fav_bins = derive_fav_rank_bin(runners)
                band = band_from_name(runners[0].get("market_name", ""))
                for r in runners:
                    bin_key = r.get("bin_key")
                    if not bin_key:
                        continue
                    fav_bin = fav_bins.get(r.get("sid"), "field")
                    cur2.execute("""
                        UPDATE mastery_posteriors
                           SET fav_rank_bin=?, distance_band=?
                         WHERE bin_key=?""",
                        (fav_bin, band, bin_key))
            con2.commit(); con2.close()
        except Exception as e:
            print(f"[digest] mastery enrichment warn: {e}")
        # --- PATCH END ---


    try:
        print("\n=== Mastery Benchmarks ===")
        total = con.execute("SELECT COUNT(*) FROM v_digest_sources").fetchone()[0]
        stoploss_hits = sum(1 for o in outcomes if o.get("stoploss"))
        hedge_hits    = sum(1 for o in outcomes if o.get("hedge"))
        wins          = sum(1 for o in outcomes if o.get("success"))

        stoploss_mastery = 100.0 * (1 - stoploss_hits/total)
        hedge_mastery    = 100.0 * (hedge_hits/total)
        winrate          = 100.0 * (wins/total)

        print(f"Stop-loss mastery   : {stoploss_mastery:.1f}%")
        print(f"Hedge mastery       : {hedge_mastery:.1f}%")
        print(f"Winrate             : {winrate:.1f}%")

        # open a fresh connection here
        con2 = sqlite3.connect(AUTO_DB); con2.row_factory = sqlite3.Row

        # add distance / fav / DOW / surface rollups
        q1 = con2.execute("SELECT distance_band, SUM(net_pnl) AS pnl, COUNT(*) AS n FROM mastery_posteriors GROUP BY distance_band").fetchall()
        q2 = con2.execute("SELECT fav_rank_bin, SUM(net_pnl) AS pnl, COUNT(*) AS n FROM mastery_posteriors GROUP BY fav_rank_bin").fetchall()
        q3 = con2.execute("SELECT day_of_week, SUM(net_pnl) AS pnl, COUNT(*) AS n FROM mastery_posteriors GROUP BY day_of_week").fetchall()
        q4 = con2.execute("SELECT surface_type, SUM(net_pnl) AS pnl, COUNT(*) AS n FROM mastery_posteriors GROUP BY surface_type").fetchall()

        con2.close()

        print("\nDistance mastery:")
        for r in q1: print(f"  {r['distance_band']:<8} pnl={r['pnl']:.2f} n={r['n']}")
        print("\nFav/field mastery:")
        for r in q2: print(f"  {r['fav_rank_bin']:<8} pnl={r['pnl']:.2f} n={r['n']}")
        print("\nDay-of-week mastery:")
        for r in q3: print(f"  {r['day_of_week']:<8} pnl={r['pnl']:.2f} n={r['n']}")
        print("\nSurface mastery:")
        for r in q4: print(f"  {r['surface_type']:<8} pnl={r['pnl']:.2f} n={r['n']}")

    except Exception as e:
        print(f"[digest] mastery benchmarks warn: {e}")

    # --- Winners & Liabilities ---
    try:
        scon = sqlite3.connect("data/settlements.db"); scon.row_factory = sqlite3.Row
        bcon = sqlite3.connect("data/bets.db"); bcon.row_factory = sqlite3.Row

        winners = con.execute("""
            SELECT b.horse_name,
                   COUNT(*) AS runs,
                   SUM(CASE WHEN s.profit > 0 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN s.profit <= 0 THEN 1 ELSE 0 END) AS losses,
                   SUM(s.profit) AS net_pnl
              FROM betsdb.bets b
              JOIN setdb.bf_cleared_orders s
                ON b.marketId=s.marketId AND b.selectionId=s.selectionId
             GROUP BY b.horse_name
             ORDER BY net_pnl DESC
             LIMIT 5
        """).fetchall()

        liabs = con.execute("""
            SELECT b.horse_name,
                   COUNT(*) AS runs,
                   SUM(CASE WHEN s.profit > 0 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN s.profit <= 0 THEN 1 ELSE 0 END) AS losses,
                   SUM(s.profit) AS net_pnl
              FROM betsdb.bets b
              JOIN setdb.bf_cleared_orders s
                ON b.marketId=s.marketId AND b.selectionId=s.selectionId
             GROUP BY b.horse_name
             ORDER BY net_pnl ASC
             LIMIT 5
        """).fetchall()

        if winners:
            print("\nTop 5 Winners:")
            for r in winners:
                print(f"  ✅ {r['horse_name']} runs={r['runs']} wins={r['wins']} net_pnl={r['net_pnl']:.2f}")

        if liabs:
            print("\nBottom 5 Liabilities:")
            for r in liabs:
                print(f"  ⚠️ {r['horse_name']} runs={r['runs']} wins={r['wins']} net_pnl={r['net_pnl']:.2f}")

        bcon.close(); scon.close()
    except Exception as e:
        print(f"[digest] winners/liabilities warn: {e}")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━




    # Deduplicate mids/sids/tags and persist both problem + success
    try:
        con3 = sqlite3.connect(AUTO_DB)
        cur = con3.cursor()
        seen = set(); persisted = []
        for (mid, sid, tag) in focus_examples + success_examples:
            key = (day, mid, sid, tag)
            if key in seen: continue
            seen.add(key)
            cur.execute("""
                INSERT INTO loss_tags(day, marketId, selectionId, tag, created_at)
                VALUES (?,?,?,?,datetime('now','utc'))
                ON CONFLICT(day, marketId, selectionId, tag) DO NOTHING
            """, (day, str(mid), str(sid), tag))
            persisted.append({"day": day, "mid": mid, "sid": sid, "tag": tag})
        con3.commit(); con3.close()
        print(f"[loss_tags updated — persisted {len(persisted)} tags]")
    except Exception as e:
        print(f"[digest] loss_tags persist warn: {e}")




    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(REPORT_DIR, exist_ok=True)
    out_path = os.path.join(REPORT_DIR, f"digest_{ts}.txt")
    with open(out_path,"w") as f: f.write("") # optional file save
    print(f"\n[Digest saved → {out_path}]")

if __name__=="__main__":
    main()
