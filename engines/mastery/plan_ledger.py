# engines/mastery/plan_ledger.py
from __future__ import annotations
import sqlite3, uuid, json
from datetime import datetime, timezone
from typing import Optional, Dict, Any

# Use the GUI DB so we can link to orders.id directly
try:
    from engines.decision_engine.decide_once.helpers import open_auto_db as _adb
except Exception:
    # Fallback if helpers moved
    from engines.config_paths import auto_conn as _auto_conn, autoscalp_db
    def _adb(ro: bool = False):
        con = _auto_conn(autoscalp_db())
        try: con.row_factory = sqlite3.Row
        except Exception: pass
        return con


# ---------- DB helpers ----------
def _conn() -> sqlite3.Connection:
    con = _adb()
    try: con.row_factory = sqlite3.Row
    except Exception: pass
    return con

def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

# ---------- schema ----------
# === PATCH F START (schema: epic columns) ===
def ensure_schema() -> None:
    con = _conn()
    try:
        con.execute("""
        CREATE TABLE IF NOT EXISTS plan_ledger(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          day TEXT NOT NULL,
          run_id TEXT,
          plan_id TEXT UNIQUE,
          marketId TEXT NOT NULL,
          selectionId TEXT NOT NULL,
          -- plan core
          family TEXT,
          letter TEXT,
          note TEXT,
          direction TEXT,
          target_ticks INTEGER,
          px REAL,
          size REAL,
          confidence REAL,
          plan_json TEXT,
          -- EPIC
          epic_id TEXT,
          epic_stories INTEGER,
          epic_rank INTEGER,
          -- status/links
          status TEXT,
          why TEXT,
          parent_order_id INTEGER,
          child_order_id  INTEGER,
          decided_at TEXT,
          updated_at TEXT
        )""")


        con.execute("CREATE INDEX IF NOT EXISTS idx_plan_ledger_midsid ON plan_ledger(marketId, selectionId)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_plan_ledger_status ON plan_ledger(status)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_plan_ledger_parent ON plan_ledger(parent_order_id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_plan_ledger_child  ON plan_ledger(child_order_id)")
        con.commit()
    finally:
        con.close()
# === PATCH F END ===

def _ensure_letter_credits(con):
    con.execute("""
      CREATE TABLE IF NOT EXISTS letter_credits(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day TEXT,
        marketId TEXT,
        letter TEXT,
        band TEXT,
        delta REAL,
        net REAL,
        updated_at TEXT DEFAULT (datetime('now','utc'))
      )
    """)
    con.commit()

def record_credit_update(letter: str, marketId: str, delta: float, band: str) -> float:
    """Append delta, return new net balance for this letter today."""
    from engines.config_paths import connect_db
    today = datetime.utcnow().strftime("%Y-%m-%d")
    con = connect_db(ro=False)
    try:
        _ensure_letter_credits(con)
        cur = con.execute("SELECT net FROM letter_credits WHERE day=? AND letter=? ORDER BY id DESC LIMIT 1",
                          (today, letter)).fetchone()
        prev = float(cur["net"]) if cur and cur["net"] is not None else 0.0
        new_net = prev + float(delta or 0.0)
        con.execute("INSERT INTO letter_credits(day,marketId,letter,band,delta,net) VALUES(?,?,?,?,?,?)",
                    (today, marketId, letter, band, delta, new_net))
        con.commit()
        return new_net
    finally:
        con.close()

def get_market_net(marketId: str) -> float:
    """Return sum of deltas for a market across all letters (today)."""
    from engines.config_paths import connect_db
    today = datetime.utcnow().strftime("%Y-%m-%d")
    con = connect_db(ro=True)
    try:
        row = con.execute("SELECT SUM(delta) AS s FROM letter_credits WHERE day=? AND marketId=?",
                          (today, marketId)).fetchone()
        return float(row["s"] or 0.0)
    finally:
        con.close()


# ---------- IDs ----------
def _new_plan_id(day: str, mid: str, sid: str, letter: str, run_id: Optional[str], pass_no: Optional[int]) -> str:
    base = f"{day}:{mid}:{sid}:{(letter or 'A')}"
    if pass_no is not None:
        base += f":p{int(pass_no)}"
    return f"{base}:{uuid.uuid4().hex[:6]}"

# ---------- record ----------
from datetime import datetime, timezone
from engines.decision_engine.decide_once.helpers import open_auto_db as _adb, q_retry as _q

# === PATCH G START (record_plan writes EPIC) ===
def record_plan(plan: dict) -> dict:
    """
    Persist that a plan was proposed. Writes EPIC into decisions.meta_json and,
    when plan_ledger table exists, also inserts a row keyed by plan_id.
    """
    try:
        if not plan or not plan.get("marketId") or not plan.get("selectionId"):
            return {"status":"no_trade", "why":"invalid"}
        con = _adb(ro=False); con.row_factory = sqlite3.Row

        # decisions mirror (always)
        meta = {
            "why": plan.get("plan_why") or plan.get("why") or "",
            "proposed_odds": plan.get("px"),
            "proposed_stake": plan.get("size"),
            "family": plan.get("family"),
            "letter": plan.get("letter"),
            # EPIC mirror
            "epic_id": plan.get("epic_id"),
            "epic_stories": plan.get("epic_stories"),
            "epic_rank": plan.get("epic_rank"),
        }
        _q(con, """
            INSERT INTO decisions(run_id, marketId, selectionId, decided_at, meta_json)
            VALUES(?, ?, ?, datetime('now','utc'), ?)
        """, (str(plan.get("run_id") or ""), str(plan["marketId"]), str(plan["selectionId"]), json.dumps(meta)))
        plan_id = con.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        con.commit()

        # optional: also into plan_ledger if available
        try:
            ensure_schema()
            con2 = _conn()
            con2.execute("""
                INSERT OR IGNORE INTO plan_ledger(
                    day, run_id, plan_id, marketId, selectionId,
                    family, letter, direction, target_ticks, px, size, confidence,
                    plan_json, epic_id, epic_stories, epic_rank,
                    status, why, decided_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now','utc'),datetime('now','utc'))
            """, (
                datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                str(plan.get("run_id") or ""),
                str(plan_id),
                str(plan["marketId"]), str(plan["selectionId"]),
                str(plan.get("family") or ""), str(plan.get("letter") or ""),
                str(plan.get("direction") or ""), int(plan.get("target_ticks") or 0),
                float(plan.get("px") or 0.0), float(plan.get("size") or 0.0),
                float(plan.get("confidence") or 0.0),
                json.dumps(plan, separators=(",",":")),
                str(plan.get("epic_id") or plan.get("marketId") or ""),
                int(plan.get("epic_stories") or 0),
                int(plan.get("epic_rank") or 0),
                "open" if plan.get("enter") else "no_trade",
                str(plan.get("why") or plan.get("plan_why") or "")
            ))
            con2.commit(); con2.close()
        except Exception:
            pass

        con.close()
        return {"status":"ok", "plan_id": plan_id}
    except Exception as e:
        return {"status":"err", "why": type(e).__name__}
# === PATCH G END ===


def mark_fill(plan_id: int, matched: float, proposed: float) -> None:
    """Optional: store matched ratio as an update row; harmless if decisions is the backing store."""
    try:
        con = _adb(ro=False)
        meta = json.dumps({"matched": matched, "proposed": proposed, "ratio": (float(matched)/float(proposed) if proposed else 0.0)})
        _q(con, "UPDATE decisions SET meta_json=? WHERE id=?", (meta, int(plan_id)))
        con.commit(); con.close()
    except Exception:
        pass
# === PATCH END ===

# ---------- marks ----------
def mark_open_parent(plan_id: str, parent_order_id: int) -> None:
    ensure_schema()
    con = _conn()
    try:
        con.execute("""
          UPDATE plan_ledger
             SET parent_order_id = COALESCE(parent_order_id, ?),
                 status = COALESCE(NULLIF(status, ''), 'placed'),
                 updated_at = ?
           WHERE plan_id=?
        """, (int(parent_order_id), _utc_now(), str(plan_id)))
        con.commit()
    finally:
        con.close()

def mark_child(plan_id: str, child_order_id: int) -> None:
    ensure_schema()
    con = _conn()
    try:
        con.execute("""
          UPDATE plan_ledger
             SET child_order_id = COALESCE(child_order_id, ?),
                 updated_at = ?
           WHERE plan_id=?""",
        (int(child_order_id), _utc_now(), str(plan_id)))
        con.commit()
    finally:
        con.close()

def mark_no_trade(plan_id: str, reason: str) -> None:
    ensure_schema()
    con = _conn()
    try:
        con.execute("""
          UPDATE plan_ledger
             SET status='no_trade', why=?, updated_at=?
           WHERE plan_id=?""",
        (str(reason), _utc_now(), str(plan_id)))
        con.commit()
    finally:
        con.close()
