from __future__ import annotations
import sqlite3
from typing import Optional, Dict, Tuple
from engines.config_paths import connect_db
import json
from datetime import datetime 
# ───────────────────────────────────────────────────────────────
# 📍 TARGET: engines/mastery/posteriors.py
# 🔎 SEARCH: CREATE TABLE IF NOT EXISTS mastery_posteriors
# 📆 PATCHED: 2025-10-02
# ───────────────────────────────────────────────────────────────

DDL = """
CREATE TABLE IF NOT EXISTS mastery_posteriors (
  bin_key TEXT NOT NULL,
  target_ticks INTEGER NOT NULL,
  success_count INTEGER NOT NULL DEFAULT 0,
  fail_count INTEGER NOT NULL DEFAULT 0,
  mae_ticks REAL NOT NULL DEFAULT 0.0,
  letter TEXT,
  band TEXT,
  epic_stories INTEGER DEFAULT 0,
  stoploss_hit INTEGER DEFAULT 0,
  hedge_hit INTEGER DEFAULT 0,
  net_pnl REAL NOT NULL DEFAULT 0.0,
  source TEXT,
  source_run TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  priority INTEGER NOT NULL DEFAULT 99,
  weight_applied REAL NOT NULL DEFAULT 1.0,
  day_of_week TEXT DEFAULT 'unk',
  surface_type TEXT DEFAULT 'unk',
  winners TEXT DEFAULT '[]',         -- ✅ JSON list of winners
  PRIMARY KEY (
    bin_key, target_ticks, letter, band, source_run, day_of_week, surface_type
  )
);
"""

# === PATCH START ===
# 📍 TARGET: engines/mastery/posteriors.py
# 🔎 SEARCH: ^def emit_snapshot
# ⛏️ ACTION: insert helper before emit_snapshot()

def get_market_pnl(day: str, marketId: str, selectionId: str|None=None) -> float:
    """
    Return settled P&L from settlements.db for given marketId[/selectionId].
    Falls back to 0.0 if no record.
    """
    import sqlite3
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
# === PATCH END ===


# ───────────────────────────────────────────────────────────────
# 📍 TARGET: engines/mastery/posteriors.py
# 🔎 SEARCH: def ensure_schema(db):
# 📆 PATCHED: 2025-10-01T23:55Z
# ───────────────────────────────────────────────────────────────

def ensure_schema(db: sqlite3.Connection):
    cur = db.cursor()

    # --- base DDL (always safe, ensures table exists with winners col) ---
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS mastery_posteriors(
        bin_key TEXT NOT NULL,
        target_ticks INTEGER NOT NULL,
        success_count INTEGER NOT NULL DEFAULT 0,
        fail_count INTEGER NOT NULL DEFAULT 0,
        mae_ticks REAL NOT NULL DEFAULT 0.0,
        letter TEXT,
        band TEXT,
        epic_stories INTEGER,
        stoploss_hit INTEGER,
        hedge_hit INTEGER,
        net_pnl REAL NOT NULL DEFAULT 0.0,
        source TEXT,
        source_run TEXT,
        updated_at TEXT NOT NULL,
        priority INTEGER NOT NULL DEFAULT 99,
        weight_applied REAL NOT NULL DEFAULT 1.0,
        day_of_week TEXT DEFAULT 'unk',
        surface_type TEXT DEFAULT 'unk',
        winners TEXT DEFAULT '[]',
        PRIMARY KEY (bin_key, target_ticks, letter, band, source_run, day_of_week, surface_type)
    );
    """)


    # --- migrations for older DBs (auto-heal missing columns) ---
    try:
        cols = [r[1] for r in cur.execute("PRAGMA table_info(mastery_posteriors)")]

        missing = []
        for col, ddl in [
            ("priority",       "INTEGER NOT NULL DEFAULT 99"),
            ("weight_applied", "REAL NOT NULL DEFAULT 1.0"),
            ("day_of_week",    "TEXT DEFAULT 'unk'"),
            ("surface_type",   "TEXT DEFAULT 'unk'"),
            ("winners",        "TEXT DEFAULT '[]'"),
            ("letter",         "TEXT"),
            ("band",           "TEXT")
        ]:
            if col not in cols:
                cur.execute(f"ALTER TABLE mastery_posteriors ADD COLUMN {col} {ddl}")
                missing.append(col)

        if missing:
            print(f"[posteriors] auto-migrated mastery_posteriors → added {missing}")

    except Exception as e:
        print(f"[posteriors] schema migrate warn: {e}")


    db.commit()



def _row_or_default(row: Optional[sqlite3.Row]) -> Dict:
    if not row:
        return {
            "p1_alpha":0.0,"p1_beta":0.0,"p2_alpha":0.0,"p2_beta":0.0,"p3_alpha":0.0,"p3_beta":0.0,
            "fill_alpha":0.0,"fill_beta":0.0,"mae_n":0,"mae_mean":0.0,"mae_m2":0.0
        }
    return {k: row[k] for k in row.keys()}

def _has_col(conn: sqlite3.Connection, tab: str, col: str) -> bool:
    return col in [r[1] for r in conn.execute(f"PRAGMA table_info({tab})")]


def get(conn: sqlite3.Connection, bin_key: str, source: str | None = None) -> Dict:
    ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    if source and _has_col(conn, "mastery_posteriors", "source"):
        row = conn.execute("SELECT * FROM mastery_posteriors WHERE bin_key=? AND source=?",
                           (bin_key, source)).fetchone()
    else:
        row = conn.execute("SELECT * FROM mastery_posteriors WHERE bin_key=?",
                           (bin_key,)).fetchone()
    return _row_or_default(row)

# === PATCH START: mastery_posteriors safe upsert (backward compatible) =========
def _upsert(conn: sqlite3.Connection, bin_key: str, vals: Dict, source: str | None = None) -> None:
    """
    Insert/update mastery_posteriors safely.
    Keeps legacy has_source check but merges duplicate bin_keys.
    """
    ensure_schema(conn)
    cur = conn.cursor()
    has_source = _has_col(conn, "mastery_posteriors", "source")

    params = {
        "bin_key": bin_key,
        "p1_alpha": vals.get("p1_alpha", 0.0),
        "p1_beta":  vals.get("p1_beta", 0.0),
        "p2_alpha": vals.get("p2_alpha", 0.0),
        "p2_beta":  vals.get("p2_beta", 0.0),
        "p3_alpha": vals.get("p3_alpha", 0.0),
        "p3_beta":  vals.get("p3_beta", 0.0),
        "fill_alpha": vals.get("fill_alpha", 0.0),
        "fill_beta":  vals.get("fill_beta", 0.0),
        "mae_n": vals.get("mae_n", 0.0),
        "mae_mean": vals.get("mae_mean", 0.0),
        "mae_m2": vals.get("mae_m2", 0.0),
        "net_pnl": vals.get("net_pnl", 0.0),
        "priority": vals.get("priority", 99),
        "weight_applied": vals.get("weight_applied", 1.0),
        "day_of_week": vals.get("day_of_week", "unk"),
        "surface_type": vals.get("surface_type", "unk"),
        "winners": vals.get("winners", "[]"),
        "letter": vals.get("letter", ""),
        "band": vals.get("band", ""),
        "source": source or "PLAYBOOKS",
        "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # single universal statement — ON CONFLICT handles both insert/update
    cur.execute(f"""
        INSERT INTO mastery_posteriors(
            bin_key, p1_alpha, p1_beta, p2_alpha, p2_beta,
            p3_alpha, p3_beta, fill_alpha, fill_beta,
            mae_n, mae_mean, mae_m2,
            net_pnl, priority, weight_applied,
            day_of_week, surface_type, winners,
            updated_at, letter, band
            {', source' if has_source else ''}
        ) VALUES (
            :bin_key, :p1_alpha, :p1_beta, :p2_alpha, :p2_beta,
            :p3_alpha, :p3_beta, :fill_alpha, :fill_beta,
            :mae_n, :mae_mean, :mae_m2,
            :net_pnl, :priority, :weight_applied,
            :day_of_week, :surface_type, :winners,
            :updated_at, :letter, :band
            {", :source" if has_source else ""}
        )
        ON CONFLICT(bin_key) DO UPDATE SET
            p1_alpha = p1_alpha + excluded.p1_alpha,
            p1_beta  = p1_beta  + excluded.p1_beta,
            p2_alpha = p2_alpha + excluded.p2_alpha,
            p2_beta  = p2_beta  + excluded.p2_beta,
            p3_alpha = p3_alpha + excluded.p3_alpha,
            p3_beta  = p3_beta  + excluded.p3_beta,
            fill_alpha = fill_alpha + excluded.fill_alpha,
            fill_beta  = fill_beta  + excluded.fill_beta,
            mae_mean   = (mae_mean + excluded.mae_mean)/2.0,
            mae_m2     = (mae_m2 + excluded.mae_m2)/2.0,
            net_pnl    = net_pnl + excluded.net_pnl,
            priority   = MIN(priority, excluded.priority),
            weight_applied = weight_applied + excluded.weight_applied,
            updated_at = excluded.updated_at,
            letter     = COALESCE(excluded.letter, letter),
            band       = COALESCE(excluded.band, band)
            {", source = COALESCE(excluded.source, source)" if has_source else ""}
    """, params)
# === PATCH END =================================================================


def _update_mae(mae_n: int, mae_mean: float, mae_m2: float, x: Optional[float]) -> Tuple[int,float,float]:
    if x is None:
        return mae_n, mae_mean, mae_m2
    n1 = mae_n + 1
    delta = x - mae_mean
    mean1 = mae_mean + delta / max(1, n1)
    m2_1  = mae_m2 + delta * (x - mean1)
    return n1, mean1, m2_1

# === PATCH START ===
# 📍 TARGET: engines/mastery/posteriors.py
# 🔎 SEARCH: def update_after_outcome
# 📆 PATCHED: 2025-10-02
# ───────────────────────────────────────────────────────────────

def update_after_outcome(conn: sqlite3.Connection,
                         bin_key: str,
                         target_ticks: int,
                         success: bool,
                         fill_observed: Optional[bool] = None,
                         mae_ticks: Optional[float] = None,
                         *,
                         letter: str = "",
                         band: str = "",
                         epic_stories: int = 0,
                         stoploss_hit: bool = False,
                         hedge_hit: bool = False,
                         net_pnl: float = 0.0,
                         source: str | None = None,
                         source_run: str | None = None,
                         priority: int = 99,
                         weight_applied: float = 1.0,
                         day_of_week: str = "unk",
                         surface_type: str = "unk",
                         winners: Optional[str] = None,   # ✅ new arg
                         auto_commit: bool = False) -> None:
    """
    Update mastery_posteriors using only real settlement PnL.

    bulk loaders (replay/consolidator):
        auto_commit=False  → caller commits per batch
    live writes (router etc.):
        auto_commit=True   → commit immediately
    """
    ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    has_source = _has_col(conn, "mastery_posteriors", "source")

    row = (conn.execute("SELECT * FROM mastery_posteriors WHERE bin_key=? AND source=?",
                        (bin_key, source)).fetchone()
           if (has_source and source) else
           conn.execute("SELECT * FROM mastery_posteriors WHERE bin_key=?",
                        (bin_key,)).fetchone())
    cur = _row_or_default(row)

    # increment priors
    if target_ticks not in (1, 2, 3):
        target_ticks = 1
    cur[f"p{target_ticks}_alpha"] = float(cur[f"p{target_ticks}_alpha"]) + (1.0 if success else 0.0)
    cur[f"p{target_ticks}_beta"]  = float(cur[f"p{target_ticks}_beta"])  + (0.0 if success else 1.0)

    # enrich
    cur["letter"]        = letter
    cur["band"]          = band
    cur["epic_stories"]  = int(cur.get("epic_stories") or 0) + epic_stories
    cur["day_of_week"]   = day_of_week
    cur["surface_type"]  = surface_type
    cur["priority"]      = priority
    cur["weight_applied"]= weight_applied
    cur["net_pnl"]       = float(cur.get("net_pnl") or 0.0) + float(net_pnl or 0.0)

    if stoploss_hit:
        cur["stoploss_hit"] = int(cur.get("stoploss_hit") or 0) + 1
    if hedge_hit:
        cur["hedge_hit"]    = int(cur.get("hedge_hit") or 0) + 1

    if source_run:
        cur["source_run"] = source_run

    # winners enrichment
    if winners:
        try:
            existing = json.loads(cur.get("winners") or "[]")
            if winners not in existing:
                existing.append(winners)
            cur["winners"] = json.dumps(existing)
        except Exception:
            cur["winners"] = json.dumps([winners])

    # --- Core Value alignment weighting (Goal Adapter integration) ---
    try:
        # fetch latest alignment from mastery_state (0–1)
        import sqlite3
        con_goal = sqlite3.connect("data/autoscalp_gui.db")
        row = con_goal.execute("""
            SELECT goal_alignment
              FROM mastery_state
             ORDER BY ts DESC
             LIMIT 1
        """).fetchone()
        con_goal.close()
        if row and row[0] is not None:
            goal_align = float(row[0])
            # scale incoming weight: high alignment → stronger reinforcement
            cur["weight_applied"] = float(cur.get("weight_applied") or 1.0) * (0.5 + goal_align / 2.0)
        else:
            # if no record yet, fallback to neutral (1.0×)
            cur["weight_applied"] = float(cur.get("weight_applied") or 1.0)
    except Exception as e:
        print(f"[posteriors] goal-alignment weight warn: {e}")


    # save
    _upsert(conn, bin_key, cur, source=source)

    if auto_commit:
        conn.commit()
# === PATCH END ===

# === PATCH START ===
# 📍 TARGET: engines/mastery/posteriors.py
# 🔎 SEARCH: ^def emit_snapshot
# 📆 PATCHED: 2025-10-14
# ───────────────────────────────────────────────────────────────

def load_context_digest(path: str = "data/reports/digest_context_latest.json") -> list[tuple[str,str,str,str,float]]:
    """
    Load the canonical contextual P&L digest produced by replay_digest.py
    and yield (letter, venue, country, fav_band, mto_band, pnl) tuples
    for posterior training or analysis.

    Returns:
        list of tuples ready for bulk insertion or model updates.
    """
    import os, json
    if not os.path.exists(path):
        print(f"[posteriors] no digest_context_latest.json at {path}")
        return []

    try:
        with open(path, "r") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[posteriors] load_context_digest warn: {e}")
        return []

    out = []
    for k, v in data.items():
        try:
            # keys look like "('A', 'Yarmouth', 'GB', 'Fav1', '5-15m')"
            key = k.strip("()")
            parts = [p.strip(" '") for p in key.split(",")]
            if len(parts) < 5:
                # pad missing values
                parts += ["Unknown"] * (5 - len(parts))
            letter, venue, country, fav_band, mto_band = parts[:5]
            pnl = float(v or 0.0)
            out.append((letter, venue, country, fav_band, mto_band, pnl))
        except Exception:
            continue

    print(f"[posteriors] loaded {len(out)} contextual P&L rows from digest_context_latest.json")
    return out
# === PATCH END ===
# === PATCH START ===
# 📍 TARGET: engines/mastery/posteriors.py
# 🔎 SEARCH: ^def update_from_digest
# 📆 PATCHED: 2025-10-15T00:32Z
# ───────────────────────────────────────────────────────────────
def update_from_digest(path: str = "data/reports/digest_context_latest.json",
                       auto_commit: bool = True) -> int:
    """
    Schema-verified loader that reads digest_context_latest.json
    and merges contextual P&L into mastery_posteriors.

    Uses only real columns:
        bin_key (PK), net_pnl, venue, fav_rank_bin, distance_band, updated_at, source.
    Each run updates or inserts ~180 contextual bins from the latest digest.
    """
    import json, os, sqlite3
    if not os.path.exists(path):
        print(f"[posteriors] update_from_digest: missing file {path}")
        return 0

    # ── Load digest JSON ──────────────────────────────────────────────
    with open(path, "r") as f:
        data = json.load(f)

    rows = []
    for k, v in data.items():
        try:
            parts = [p.strip(" '") for p in k.strip("()").split(",")]
            if len(parts) < 5:
                parts += ["Unknown"] * (5 - len(parts))
            letter, venue, country, fav_band, mto_band = parts[:5]
            pnl = float(v or 0.0)
            rows.append((letter, venue, country, fav_band, mto_band, pnl))
        except Exception:
            continue
    print(f"[posteriors] loaded {len(rows)} contextual rows from digest")

    # ── Upsert safely into mastery_posteriors ─────────────────────────
    con = sqlite3.connect("data/autoscalp_gui.db")
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # Verify required columns exist (defensive)
    cols = [r[1] for r in cur.execute("PRAGMA table_info(mastery_posteriors)")]
    need = {"bin_key","net_pnl","venue","fav_rank_bin","distance_band"}
    if not need.issubset(set(cols)):
        print(f"[posteriors] update_from_digest abort: missing {need - set(cols)}")
        con.close()
        return 0

    written = 0
    for letter, venue, country, fav_band, mto_band, pnl in rows:
        try:
            bin_key = f"{letter}|{venue}|{country}|{fav_band}|{mto_band}"
            # check current value
            row = cur.execute(
                "SELECT net_pnl FROM mastery_posteriors WHERE bin_key=?",
                (bin_key,)
            ).fetchone()
            current = float(row["net_pnl"]) if row else 0.0
            new_val = pnl
            # single-column PK → INSERT OR REPLACE
            cur.execute("""
                INSERT OR REPLACE INTO mastery_posteriors(
                    bin_key, net_pnl, updated_at, venue, fav_rank_bin, distance_band, source
                )
                VALUES (?, ?, datetime('now'), ?, ?, ?, 'DIGEST')
            """, (bin_key, new_val, venue, fav_band, mto_band))
            written += 1
        except Exception as e:
            print(f"[posteriors] digest upsert warn: {e}")
            continue

    if auto_commit:
        con.commit()
    con.close()
    print(f"[posteriors] update_from_digest: wrote {written} contextual bins")
    return written
# === PATCH END ===

# === PATCH START ===
# 📍 TARGET: engines/mastery/posteriors.py
# 🔎 SEARCH: ^# end of file
# ⛏️ ACTION: add snapshot writer

def emit_snapshot(path: Optional[str] = None) -> str:
    """
    Emit a JSON snapshot of mastery_posteriors for Mastery bootstrap.
    Returns the path written.
    """
    out = {}
    con = connect_db(ro=True); con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT * FROM mastery_posteriors").fetchall()
        for r in rows:
            out[str(r["bin_key"])] = dict(r)
    finally:
        con.close()

    if not path:
        ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        base = os.path.join("data","posteriors")
        os.makedirs(base,exist_ok=True)
        path = os.path.join(base,f"mastery_snapshot_{ts}.json")

    with open(path,"w") as f:
        json.dump(out,f,indent=2)
    return path
# === PATCH END ===

