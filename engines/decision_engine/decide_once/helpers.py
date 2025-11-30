# engines/decision_engine/decide_once/helpers.py (V2)
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, List
import time as _time
import sqlite3, time as _time

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/helpers.py
# 🔎 SEARCH: from datetime import datetime, timezone
# ⛏️ ACTION: insert imports + q_retry bridge right after imports

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Prefer the canonical q_retry/openers from config_paths, with safe fallbacks
try:
    from engines.config_paths import q_retry as _cp_q_retry  # (con, sql, params=(), tries=6, delay_s=0.08)
except Exception:
    _cp_q_retry = None

# --- BEGIN DB SHIM (no call-site changes) ---
import sqlite3
from engines.config_paths import auto_conn as __cp_auto_conn, q_retry as __cp_q_retry

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/helpers.py:_adb_ro_fresh
# 🔎 SEARCH: def _adb_ro_fresh(
# 📆 PATCHED: 2025-11-21 — replace raw sqlite3.connect with DAL RO reader

def _adb_ro_fresh():
    """Open AUTOSCALP GUI DB as RO via DAL."""
    from engines.config_paths import auto_conn as _auto_conn
    con = _auto_conn(rw=False)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA read_uncommitted=1;")
        con.execute("PRAGMA wal_checkpoint(PASSIVE);")
    except Exception:
        pass
    return con
# === PATCH END ===



def auto_conn(*_args, **_kwargs):
    con = __cp_auto_conn()
    try: con.row_factory = sqlite3.Row
    except Exception: pass
    return con

def q_retry(obj, sql, params=(), *_a, **_k):
    con = getattr(obj, "connection", None) or obj
    return __cp_q_retry(con, sql, params)
# --- END DB SHIM ---


_q_retry = q_retry
# === PATCH END ===
# at top-level in helpers.py
try:
    from engines.decision_engine.decide_once.rules import rulebook_allow as rulebook_allow  # noqa
    from engines.decision_engine.decide_once.rules import apply_rulebook  as apply_rulebook  # noqa
except Exception:
    # fall back to permissive if rules module missing
    def rulebook_allow(letter, pass_n, ctx, plan): return True, plan
    def apply_rulebook(letter, tag, ctx, plan):    return False, plan

# ---- CAP & ROTATION gates -----------------------------------------------
import sqlite3
from typing import Tuple

# This CAP helper wraps your existing _cap_ok but returns reason text too.
# ============================================================
# 📍 TARGET: engines/decision_engine/decide_once/helpers.py
# 🔎 SEARCH: def cap_gate_ok\(db: sqlite3\.Connection, \*, market_id: str, selection_id: str, letter: str, mode: str\) -> Tuple\[bool, str\]:
# ============================================================
def cap_gate_ok(db: sqlite3.Connection, *, market_id: str, selection_id: str, letter: str, mode: str) -> Tuple[bool, str]:
    """Central cap gate wrapper for lanes/rules. DB argument kept for signature compatibility."""
    try:
        from engines.decision_engine.decide_once.caps import cap_ok
    except Exception:
        from .caps import cap_ok  # type: ignore
    ok, reason, _metrics = cap_ok(str(market_id), str(selection_id), str(letter))
    return ok, (reason if not ok else "ok")


# Simple rotation guard: prevents repeated placements on the same (mid, sid, letter, mode)
_last_selection_key: Tuple[str, str, str, str] = ("", "", "", "")

# ============================================================
# 📍 TARGET: engines/decision_engine/decide_once/helpers.py
# 🔎 SEARCH: ^def rotation_ok\(
# ⛏️ ACTION: replace function body
# ============================================================
def rotation_ok(*, market_id: str, selection_id: str, letter: str, mode: str | None):
    """
    Rotation guard: block if (marketId, selectionId, letter) already has
    an open order today. Removes runner_activity dependency.
    """
    import sqlite3
    con = open_auto_db(); con.row_factory = sqlite3.Row
    try:
        has_status = False
        try:
            cols = [r["name"] for r in con.execute("PRAGMA table_info(orders)")]
            has_status = "status" in cols
        except Exception:
            pass

        if has_status:
            open_pred = "UPPER(COALESCE(status,'')) IN ('PENDING','PLACED','LIVE')"
        else:
            open_pred = "UPPER(COALESCE(entry_status,'')) IN ('PENDING','PLACED','LIVE')"

        r = con.execute(f"""
          SELECT COUNT(*) AS n
            FROM orders
           WHERE marketId=? AND selectionId=? AND source=?
             AND {open_pred}
             AND date(opened_at)=date('now','utc')
        """, (str(market_id), str(selection_id), str(letter))).fetchone()

        n = int(r["n"] or 0) if r else 0
        if n > 0:
            return False, "rotation_hold_same_selection"
        return True, "ok"
    finally:
        try: con.close()
        except Exception: pass

# ─────────────────────────────────────────────────────────────────────
# Ensure odds_current for the exact (mid,sid,px) we’re about to evaluate
# ─────────────────────────────────────────────────────────────────────
# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/helpers.py:ensure_tape_for_candidates
# 🔎 SEARCH: con = sqlite3.connect(autoscalp_db())
# 📆 PATCHED: 2025-11-21 — route writes through DAL

from engines.config_paths import auto_conn as _auto_conn

def ensure_tape_for_candidates(all_pairs):
    """
    Seed minimal odds_current rows using DAL-safe writer.
    """
    import time
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    day = time.strftime("%Y-%m-%d")

    con = _auto_conn(rw=True)
    con.row_factory = sqlite3.Row

    insert_sql = """
      INSERT INTO odds_current(day, marketId, selectionId, ltp, updated_ts)
      VALUES(?,?,?,?,?)
      ON CONFLICT(day, marketId, selectionId)
      DO UPDATE SET
        ltp=excluded.ltp,
        updated_ts=excluded.updated_ts
    """
    def _sid_px(item):
        # Accept (sid,px), (sid,px,extra...), or dict {'selectionId':..., 'px'/'odd'/'price'/'ltp':...}
        if isinstance(item, dict):
            sid = item.get("selectionId") or item.get("sid") or item.get("runnerId")
            px  = item.get("px") or item.get("odd") or item.get("price") or item.get("ltp")
            try: return str(sid), float(px)
            except Exception: return None, None
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            try: return str(item[0]), float(item[1])
            except Exception: return None, None
        return None, None

    ok = False
    try:
        rows = []
        for mid, cands in (all_pairs or []):
            for it in (cands or []):
                sid, px = _sid_px(it)
                if sid and px:
                    rows.append((day, str(mid), sid, px, now))
        if rows:
            con.executemany(insert_sql, rows)
        ok = True
    except Exception:
        try: con.rollback()
        except Exception: pass
    finally:
        try: con.close()
        except Exception: pass

    try:
        status_once("tape:ensure", ok, "" if ok else "failed")
    except Exception:
        pass



# ==== add in helpers.py (end of file is fine) ====

def _coerce(d: dict, key: str, cast, default):
    try:
        v = d.get(key, None)
        d[key] = cast(v) if v is not None else default
    except Exception:
        d[key] = default

def harden_ctx(ctx: dict) -> dict:
    """
    Normalize ctx so downstream code never sees None/invalid numerics.
    Also ensure price/ltp fall back to odds.
    """
    ctx = dict(ctx or {})

    # odds first (then price/ltp from odds)
    try:
        odds = float(ctx.get("odds", ctx.get("price", ctx.get("ltp", 0.0))))
    except Exception:
        odds = 0.0
    ctx.setdefault("odds", odds)

    _coerce(ctx, "minutes_to_off", float, 1e9)
    _coerce(ctx, "fav_rank",       int,   99)
    _coerce(ctx, "fav_rank_now",   int,   ctx.get("fav_rank", 99))
    _coerce(ctx, "exposure",       float, 0.0)
    _coerce(ctx, "target_ticks",   int,   1)
    _coerce(ctx, "hedge_ticks",    int,   1)

    # tape/price fallbacks
    if not ctx.get("price"):
        ctx["price"] = float(odds)
    if not ctx.get("ltp"):
        ctx["ltp"] = float(odds)

    # keep ints >=1 where expected
    try:
        ctx["target_ticks"] = max(1, int(ctx["target_ticks"]))
    except Exception:
        ctx["target_ticks"] = 1
    try:
        ctx["hedge_ticks"] = max(1, int(ctx["hedge_ticks"]))
    except Exception:
        ctx["hedge_ticks"] = 1

    return ctx

def harden_plan(plan: dict) -> dict:
    """
    Normalize plan numerics and clean optional fields so placement never trips on None.
    """
    plan = dict(plan or {})

    def _zi(v, d=0):
        try: return int(v)
        except Exception: return int(d)
    def _zf(v, d=0.0):
        try: return float(v)
        except Exception: return float(d)

    plan["target_ticks"] = max(1, _zi(plan.get("target_ticks"), 1))
    plan["hedge_ticks"]  = max(1, _zi(plan.get("hedge_ticks", plan["target_ticks"]), plan["target_ticks"]))
    plan["stop_ticks"]   = max(0, _zi(plan.get("stop_ticks"), 0))
    plan["timeout_sec"]  = max(1, _zi(plan.get("timeout_sec"), 45))
    plan["size"]         = max(2.0, _zf(plan.get("size"), 2.0))

    # optional field
    if plan.get("pyramid_add_at") is None:
        plan.pop("pyramid_add_at", None)
    else:
        plan["pyramid_add_at"] = _zi(plan["pyramid_add_at"], 0)

    # direction/edge safety
    d = (plan.get("direction") or "").upper()
    if d not in ("LAY->BACK", "BACK->LAY"):
        plan["direction"] = "LAY->BACK"
    if plan["direction"] == "BACK->LAY":
        plan.setdefault("edge", "B2L")
    else:
        plan.setdefault("edge", "L2B")

    return plan


# --- inbound schema & anchor upsert + blueprint daily runner -------------------

def _now_utc_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def _today_utc() -> str:
    # day key matches sqlite date('now','utc')
    import sqlite3
    try:
        from engines.config_paths import auto_conn
        con = auto_conn(rw=True)

        day = _q_retry(con, "SELECT date('now','utc') AS d").fetchone()["d"]
        con.close()
        return str(day)
    except Exception:
        # portable fallback
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/helpers.py
# 🔎 SEARCH: ^def _today_utc\(
# 📆 PATCHED: 2025-10-09Z — enforce UTC window for scope filtering (permanent)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _scope_date_clause() -> str:
    """
    Unified SQL snippet for 'today' comparisons in UTC.
    Use this wherever date(off_at_utc) or date(marketStartTime) is filtered.
    """
    return "date(%s)=date('now','utc')"
# === PATCH END ===


def ensure_inbound_schema() -> None:
    """
    Ensure AUTO_DB has app_kv and a unique key for (marketId,selectionId) on inbound_oc_cache.
    Safe to call often.
    """
    import sqlite3
    con = open_auto_db(ro=False)
    try:
        _q_retry(con, """
            CREATE TABLE IF NOT EXISTS app_kv (
                k TEXT PRIMARY KEY,
                v TEXT,
                ts TEXT DEFAULT (datetime('now','utc'))
            )
        """)
        # inbound cache table may already exist; ensure unique index for upsert-by-runner
        _q_retry(con, """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_inbound_oc_cache_mid_sid
            ON inbound_oc_cache(marketId, selectionId)
        """)
        con.commit()
    finally:
        try: con.close()
        except Exception: pass

def upsert_anchor_batch_from_bets(day_iso: str | None = None) -> tuple[int,int]:
    """
    Copy anchors from BETS to AUTO inbound_oc_cache for today's markets (idempotent).
    Returns (attempted, wrote).
    """
    import sqlite3, json
    day = (day_iso or _today_utc())

    # open both DBs
    try:
        from engines.config_paths import connect_db as _connect_bets
        bdb = _connect_bets(ro=True); bdb.row_factory = sqlite3.Row
    except Exception:
        # fallback to our own opener if available
        bdb = open_bets_db(ro=True); bdb.row_factory = sqlite3.Row

    adb = open_auto_db(ro=False); adb.row_factory = sqlite3.Row
    ensure_inbound_schema()

    rows = _q_retry(bdb, """
        SELECT marketId, selectionId,
               COALESCE(anchor_odd, OC0) AS px
        FROM bets
        WHERE date(substr(marketStartTime,1,10)) = ?
        GROUP BY marketId, selectionId
    """, (day,)).fetchall()

    attempted = 0
    wrote = 0
    for r in rows or []:
        attempted += 1
        mid = str(r["marketId"]); sid = str(r["selectionId"])
        px = r["px"]
        if px is None:
            continue
        # upsert anchor_odd; do not overwrite a newer oc1 if present
        _q_retry(adb, """
            INSERT INTO inbound_oc_cache (marketId, selectionId, anchor_odd, last_sync_ts)
            VALUES (?,?,?,datetime('now','utc'))
            ON CONFLICT(marketId, selectionId) DO UPDATE SET
                anchor_odd = COALESCE(excluded.anchor_odd, anchor_odd),
                last_sync_ts = datetime('now','utc')
        """, (mid, sid, float(px)))
        wrote += 1

    try:
        adb.commit()
    finally:
        try: adb.close()
        except Exception: pass
        try: bdb.close()
        except Exception: pass

    # health line
    status_once("anchors:upsert", wrote > 0, f"attempted={attempted} wrote={wrote}")
    return attempted, wrote

# time_oc.py — canonical TTO + OC banding + family windows

from datetime import datetime, timezone
import math
from typing import Optional, Tuple

# ──────────────────────────────────────────────────────────────────────
# UTC minutes-to-off
# ──────────────────────────────────────────────────────────────────────
def tto_minutes(off_at_utc_iso: str, *, now: Optional[datetime] = None) -> Optional[float]:
    """
    Minutes-to-off (TTO) in UTC.
      +ve pre-off (e.g., +80 .. +0), 0 at the off, -ve post-off (e.g., -1, -2…).
    Returns None if off time is missing/bad.
    """
    if not off_at_utc_iso:
        return None
    try:
        s = off_at_utc_iso.strip().replace("Z", "+00:00")
        off = datetime.fromisoformat(s)
        if off.tzinfo is None:
            off = off.replace(tzinfo=timezone.utc)
        now_utc = now or datetime.now(timezone.utc)
        return (off - now_utc).total_seconds() / 60.0
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────
# Family time window (bands), with hard expiry at TTO < -15
# ──────────────────────────────────────────────────────────────────────
def allowed_time_window(letter: str, tto_min: Optional[float]) -> bool:
    """
    Family windows:
      • A (ALWAYS_ON), P (BLUEPRINTS): anytime (even >> +60, and 0..-15 in-play)
      • IP5 (letter 'K') + P: allowed in-play (0..-15)
      • All others: allowed only in -15..+60
      • Expired: TTO < -15 → False for everyone
    """
    if tto_min is None:
        return False
    if tto_min < -15.0:
        return False  # expired

    L = (letter or "").upper()

    # in-play: 0 > TTO >= -15 → only P and IP5 (K)
    if tto_min < 0.0:
        return L in {"P", "K"}

    # pre-off beyond +60: only A and P
    if tto_min > 60.0:
        return L in {"A", "P"}

    # 0..+60: all families allowed
    return True


# ──────────────────────────────────────────────────────────────────────
# OC banding — bands (NOT points). Half-open intervals [lo, hi).
# Pre-off bands: [80,120), [60,80), [40,60), [20,40), [10,20), [5,10), [0,5)
# Post-off minute bands: [-1,0), [-2,-1), ..., [-15,-14)  (cap at -15)
# Returns (label, (lo, hi)) or (None, None) if expired (< -15) or bad TTO.
# ──────────────────────────────────────────────────────────────────────
PRE_BANDS = [
    ("OC120", 80.0, 120.0),
    ("OC80",  60.0,  80.0),
    ("OC60",  40.0,  60.0),
    ("OC40",  20.0,  40.0),
    ("OC20",  10.0,  20.0),
    ("OC10",   5.0,  10.0),
    ("OC5",    0.0,   5.0),
]

def oc_band(tto_min: Optional[float]) -> Tuple[Optional[str], Optional[Tuple[float, float]]]:
    """
    Map TTO to an OC band. Bands are inclusive at the lower bound, exclusive at the upper: [lo, hi).
    Examples:
      TTO= 83  → ("OC120", [80,120))
      TTO= 59  → ("OC60",  [40,60))
      TTO=  4  → ("OC5",   [0,5))
      TTO=-0.1 → ("OC-1",  [-1,0))
      TTO=-3.9 → ("OC-4",  [-4,-3))
      TTO<-15  → (None, None)  # expired
    """
    if tto_min is None:
        return None, None

    # expired: past -15
    if tto_min < -15.0:
        return None, None

    # pre-off bands
    if tto_min >= 0.0:
        for name, lo, hi in PRE_BANDS:
            if lo <= tto_min < hi:
                return name, (lo, hi)
        # above our largest band (>=120) — still useful to label; many strategies don't act anyway
        if tto_min >= 120.0:
            return "OC120+", (120.0, float("inf"))
        # between 5..0 already captured; nothing else to label here
        return "OC<0?", (tto_min, tto_min)

    # post-off per-minute bands (0 > TTO ≥ -15): OC-1 .. OC-15
    n = int(math.ceil(-tto_min))     # -0.1->1, -1.0->1, -1.01->2 …
    n = max(1, min(n, 15))           # cap at -15
    lo = float(-n)
    hi = float(-(n - 1))
    return f"OC-{n}", (lo, hi)


# ──────────────────────────────────────────────────────────────────────
# Convenience wrapper — one call per market to get all signals back
# ──────────────────────────────────────────────────────────────────────
def oc_state(off_at_utc_iso: str,
             letter: str,
             *,
             now: Optional[datetime] = None) -> dict:
    """
    Returns a dict with:
      - tto_min        : float or None
      - allowed        : bool (time window per family)
      - expired        : bool (TTO < -15)
      - oc_label       : string band label or None
      - oc_range       : (lo, hi) minutes or None
    """
    tto = tto_minutes(off_at_utc_iso, now=now)
    label, rng = oc_band(tto)
    return {
        "tto_min": tto,
        "allowed": allowed_time_window(letter, tto),
        "expired": (tto is not None and tto < -15.0),
        "oc_label": label,
        "oc_range": rng,
    }

# ============================================================
# 📍 TARGET: engines/decision_engine/decide_once/helpers.py
# 🔎 SEARCH: ^def refresh_odds_current_for_markets\(mids, max_runners=8\):
# ⛏️ ACTION: replace the inner loop that builds `rows` with a robust extractor
# ============================================================
def refresh_odds_current_for_markets(market_ids, *, max_runners: int = 8) -> bool:
    """
    Robust writer for autoscalp.odds_current.
    Accepts pairs from cands_pairs_for_market in flexible shapes:
      - tuple: (selectionId, ltp) or (selectionId, ltp, back1, lay1)
      - dict:  {'selectionId'|sid, 'ltp'|price|px|odd, 'back1'?, 'lay1'?}
    Upserts (day, marketId, selectionId) with updated_ts, ltp, back1, lay1.
    """
    from .candidates import cands_pairs_for_market
    import datetime

    def _num(x):
        try:
            return float(x) if x is not None else None
        except Exception:
            return None

    day = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    now_ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    con = None
    try:
        con = open_auto_db(); con.row_factory = sqlite3.Row
        rows = []
        for mid in (market_ids or []):
            pairs = cands_pairs_for_market(mid, max_runners=max_runners) or []
            for p in pairs:
                sid = None; ltp = None; b1 = None; l1 = None
                if isinstance(p, dict):
                    sid = p.get("selectionId") or p.get("sid") or p.get("SelectionId")
                    ltp = _num(p.get("ltp") or p.get("price") or p.get("px") or p.get("odd"))
                    b1 = _num(p.get("back1"))
                    l1 = _num(p.get("lay1"))
                else:
                    # tuple/list
                    if len(p) >= 1: sid = p[0]
                    if len(p) >= 2: ltp = _num(p[1])
                    if len(p) >= 3: b1  = _num(p[2])
                    if len(p) >= 4: l1  = _num(p[3])

                if sid is None or ltp is None:
                    continue
                rows.append((day, str(mid), str(sid), now_ts, float(ltp), b1, l1))

        if not rows:
            status_once("odds:write", True, "empty")
            return True

        con.executemany("""
            INSERT INTO odds_current(day, marketId, selectionId, updated_ts, ltp, back1, lay1)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(day, marketId, selectionId)
            DO UPDATE SET
              updated_ts=excluded.updated_ts,
              ltp=excluded.ltp,
              back1=COALESCE(excluded.back1, odds_current.back1),
              lay1=COALESCE(excluded.lay1, odds_current.lay1)
        """, rows)
        con.commit()
        status_once("odds:write", True, "")
        return True
    except Exception as e:
        try:
            if con: con.rollback()
        finally:
            status_once("odds:write", False, str(e))
            print(f"[HEALTH] odds:write => FAIL — {e}")

        return False
    finally:
        try:
            if con: con.close()
        except Exception:
            pass

def run_blueprints_if_needed(force: bool = False) -> bool:
    """
    Single entry to build/load Blueprints once per UTC day.
    Uses AUTO_DB.app_kv 'blueprints_ran_<YYYY-MM-DD>' key.
    Returns True if blueprints were run in this call, False if already done.
    """
    ensure_inbound_schema()
    day = _today_utc()
    key = f"blueprints_ran_{day}"

    con = open_auto_db(ro=False); con.row_factory = None
    try:
        if not force:
            r = _q_retry(con, "SELECT v FROM app_kv WHERE k=?", (key,)).fetchone()
            if r:
                status_once("blueprints", True, f"already-ran {day}")
                return False

        # Import the canonical builder (prefer engines.blueprint_build.main)
        bp_main = None
        try:
            from engines.blueprint_build import main as bp_main  # type: ignore
        except Exception:
            try:
                import blueprint_build as _bp_mod  # fallback path
                bp_main = getattr(_bp_mod, "main", None)
            except Exception:
                bp_main = None

        if not callable(bp_main):
            status_once("blueprints", False, "entrypoint-not-found")
            return False

        ok = False
        try:
            bp_main(force=bool(force))
            ok = True
        except Exception as e:
            status_once("blueprints", False, f"build-error: {e!r}")
            ok = False

        if ok:
            _q_retry(con, """
                CREATE TABLE IF NOT EXISTS app_kv(
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT DEFAULT (datetime('now','utc'))
                )
            """)
            row = _q_retry(con, "SELECT value FROM app_kv WHERE key=?", (key,)).fetchone()
            if row:
                return False
            _q_retry(con, """
                INSERT INTO app_kv(key, value, updated_at) VALUES(?, '1', datetime('now','utc'))
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """, (key,))
    finally:
        try: con.close()
        except Exception: pass

# some call-sites look for a leading-underscore name; keep alias
_run_blueprints_if_needed = run_blueprints_if_needed
# === PATCH END ===


def _latest_prices_from_inbound(mid: str) -> dict[str, float]:
    """
    Current best guess price per runner for a market:
    - oc1 if present
    - else anchor_odd
    - else oc1_band tail
    """
    con = _auto_conn(); con.row_factory = sqlite3.Row
    rows = _q_retry(con, """
        SELECT selectionId, oc1, anchor_odd, oc1_band_json
        FROM inbound_oc_cache
        WHERE marketId=? ORDER BY id DESC
    """, (str(mid),)).fetchall()
    seen, out = set(), {}
    for r in rows or []:
        sid = str(r["selectionId"])
        if sid in seen:
            continue
        px = r["oc1"] if r["oc1"] is not None else r["anchor_odd"]
        if px is None:
            bj = r["oc1_band_json"]
            if bj:
                try:
                    arr = json.loads(bj)
                    if isinstance(arr, list) and arr:
                        px = float(arr[-1])
                except Exception:
                    pass
        if px is not None:
            out[sid] = float(px)
        seen.add(sid)
    try: con.close()
    except Exception: pass
    return out

def active_candidates_for_market(mid: str, max_runners: int = 12) -> List[Tuple[str, float]]:
    """
    Sorted by price asc, capped to max_runners. 100% folder-local, no legacy deps.
    """
    odds = _latest_prices_from_inbound(mid)
    if not odds:
        return []
    pool = sorted([(sid, float(px)) for sid, px in odds.items()], key=lambda t: (t[1], t[0]))
    return pool[:max_runners]

# ---- Orders link column helper ----------------------------------------------
import sqlite3

def ensure_orders_link_col(con: sqlite3.Connection) -> str:
    """
    Ensure the orders table has a link column used to associate a hedge (child)
    with its parent. Returns the column name to use: 'hedge_of' or 'parent_id'.
    - If neither exists, adds 'hedge_of' INTEGER and indexes it.
    - If both exist, prefers 'hedge_of'.
    Safe to call many times.
    """
    try:
        con.row_factory = sqlite3.Row
    except Exception:
        pass

    # does 'orders' exist?
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='orders'"
    ).fetchone()
    if not row:
        # If you ever run before migrations, fail-open with canonical name.
        return "hedge_of"

    def _cols() -> set[str]:
        return {
            (r["name"] if isinstance(r, sqlite3.Row) else r[1])
            for r in con.execute("PRAGMA table_info(orders)")
        }

    cols = _cols()
    link = None

    if "hedge_of" in cols:
        link = "hedge_of"
    elif "parent_id" in cols:
        link = "parent_id"
    else:
        # add the canonical link column
        try:
            con.execute("ALTER TABLE orders ADD COLUMN hedge_of INTEGER")
            con.commit()
            link = "hedge_of"
        except Exception:
            # As a last resort, try parent_id
            try:
                con.execute("ALTER TABLE orders ADD COLUMN parent_id INTEGER")
                con.commit()
                link = "parent_id"
            except Exception:
                link = "hedge_of"  # fail-open with canonical name

    # index for faster lookups (idempotent)
    try:
        con.execute(f"CREATE INDEX IF NOT EXISTS idx_orders_{link} ON orders({link})")
        con.commit()
    except Exception:
        pass

    return link


# ── time / health ────────────────────────────────────────────────────────────

def utc_now() -> datetime:
    try:
        return datetime.now(timezone.utc)
    except Exception:
        return datetime.utcnow().replace(tzinfo=timezone.utc)


def minute_bucket(ts: Optional[float] = None) -> int:
    try:
        return int((ts if ts is not None else _time.time()) // 60)
    except Exception:
        return 0


import os, sys, time

_HEALTH_ALLOW = {"decide_once:tick", "PLACE", "NO-ENTRY"}

def status_once(tag: str, ok: bool, msg: str = "") -> None:
    """
    Consolidated health emitter.
    Set AUTOSCALP_HEALTH_MUTE=1 to suppress non-core tags.
    Core tags: decide_once:tick, PLACE, NO-ENTRY.
    """
    mute = os.environ.get("AUTOSCALP_HEALTH_MUTE", "0") == "1"
    if mute and tag not in _HEALTH_ALLOW:
        return
    ts = time.strftime("%H:%M:%S")
    state = "OK" if ok else "FAIL"
    try:
        print(f"[HEALTH] {tag} => {state}{(' — ' + msg) if msg else ''}")
    except Exception:
        pass

# ── config paths (best-effort) ───────────────────────────────────────────────

def _path_call(fn_name: str) -> Optional[str]:
    try:
        from engines import config_paths as cp  # type: ignore
        fn = getattr(cp, fn_name, None)
        if callable(fn):
            return fn()
    except Exception:
        return None
    return None

# ── DB connectors (canonical DAL; no local fallbacks) ────────────────────────
from engines.config_paths import auto_conn as _cp_auto_conn
from engines.config_paths import connect_bets_db as _cp_bets_conn
from engines.config_paths import q_retry as __cp_q_retry

def q_retry(obj, sql, params=(), *_a, **_k):
    con = getattr(obj, "connection", None) or obj
    return __cp_q_retry(con, sql, params)

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/helpers.py
# 🔎 SEARCH: ^def open_auto_db\(
# 📆 PATCHED: 2025-11-19 — default writer; explicit ro only
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def open_auto_db(*, ro: Optional[bool] = None, rw: Optional[bool] = None, **_k):
    """
    Canonical connector into AUTOSCALP_GUI (autoscalp_gui.db).

    Semantics (backwards-compatible):

      • ro=True              → explicit READ-ONLY connection
      • rw=True              → explicit READ/WRITE connection
      • default (no flags)   → READ/WRITE connection  (legacy behaviour)
      • ro=False             → treated as no explicit ro  → writer
    """
    # explicit read-only wins
    if ro:
        return _cp_auto_conn(rw=False)

    # explicit writer
    if rw:
        return _cp_auto_conn(rw=True)

    # default / ro=False → writer (legacy-safe)
    return _cp_auto_conn(rw=True)
# === PATCH END ===


# --- NON-RECURSIVE bets connector (helpers.py) ---
import sqlite3
from engines.config_paths import bets_db as _bets_path

def open_bets_db(*, ro: bool = True, rw: bool | None = None, timeout: float = 10.0):
    """
    Return a direct sqlite3 connection to bets.db using the path from config_paths.bets_db().
    - Never returns None
    - Does NOT call connect_bets_db (avoids DAL recursion)
    - Respects ro/rw flags via URI
    """
    path = _bets_path()
    # default: read-only; if rw=True, use writable (existing file)
    mode = "ro" if (ro is True and not rw) else "rw"
    uri = f"file:{path}?mode={mode}&cache=shared"

    con = sqlite3.connect(
        uri,
        uri=True,
        timeout=timeout,
        isolation_level=None,
        check_same_thread=False,
    )
    # row factory + pragmatic PRAGMAs (ignore failures in ro)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=12000;")
        con.execute("PRAGMA synchronous=NORMAL;")
        con.execute("PRAGMA journal_mode=WAL;")
    except Exception:
        pass
    return con



# Legacy convenience aliases (safe to keep — they forward to DAL)
auto_conn = open_auto_db
bets_conn = open_bets_db

# put near other DB helpers
def _auto_db_latest_prices(market_id: str) -> dict[str, float]:
    """
    Fallback: latest oc1 (or band tail) per runner for a market from AUTO_DB.inbound_oc_cache.
    Returns {selectionId: price}.
    """
    from engines.config_paths import auto_conn as _auto_conn, q_retry as _q
    import sqlite3, json
    con = _auto_conn(); con.row_factory = sqlite3.Row
    try:
        # grab latest rows (ORDER BY id DESC) and keep the first seen per sid
        rows = _q(con, "SELECT selectionId, oc1, oc1_band_json FROM inbound_oc_cache WHERE marketId=? ORDER BY id DESC",
                  (str(market_id),)).fetchall()
        latest: dict[str, float] = {}
        for r in rows or []:
            sid = str(r["selectionId"])
            if sid in latest:
                continue
            v = r["oc1"]
            if v is None:
                # try band tail if present
                bj = r["oc1_band_json"]
                if bj:
                    try:
                        arr = json.loads(bj)
                        if isinstance(arr, list) and arr:
                            v = float(arr[-1])
                    except Exception:
                        v = None
            if v is not None:
                latest[sid] = float(v)
        return latest
    except Exception:
        return {}
    finally:
        try: con.close()
        except Exception: pass

# --- UNIVERSAL ODDS RESOLVER (read-only; no odds_current dependency) ---------
def _market_odds(mid: str, sid: str) -> float | None:
    """
    Best-effort current odds for (mid, sid), precedence:
      1) AUTO_DB inbound_oc_cache.oc1 latest
      2) AUTO_DB oc_series.odd (today, latest)
      3) BETS_DB bets.COALSCE(anchor_odd, OC0)
      4) Live API best offers (optional last resort)
    Returns float or None. Never writes to any DB.
    """
    # 1) inbound_oc_cache.oc1
    try:
        from engines.config_paths import auto_conn
        con = auto_conn(rw=True)
        r = _q_retry(con,
            "SELECT oc1 FROM inbound_oc_cache WHERE marketId=? AND selectionId=? "
            "ORDER BY id DESC LIMIT 1",
            (str(mid), str(sid))
        ).fetchone()
        if r and r["oc1"] is not None:
            con.close()
            return float(r["oc1"])
        con.close()
    except Exception:
        pass

    # 2) AUTO_DB oc_series (today)
    try:
        from engines.config_paths import auto_conn
        con = auto_conn(rw=True)
        r = _q_retry(con,
            "SELECT odd FROM oc_series WHERE marketId=? AND selectionId=? "
            "AND date(snapshot_ts)=date('now','utc') "
            "ORDER BY datetime(snapshot_ts) DESC LIMIT 1",
            (str(mid), str(sid))
        ).fetchone()
        if r and r["odd"] is not None:
            con.close()
            return float(r["odd"])
        con.close()
    except Exception:
        pass

    # 3) BETS_DB anchors
    try:
        try:
            from engines.config_paths import connect_db as _conn
            bdb = _conn(ro=True)
        except Exception:
            bdb = open_bets_db(ro=True)
        bdb.row_factory = sqlite3.Row
        r = _q_retry(bdb,
            "SELECT COALESCE(anchor_odd, OC0) AS px FROM bets WHERE marketId=? AND selectionId=? "
            "ORDER BY datetime(timestamp) DESC LIMIT 1",
            (str(mid), str(sid))
        ).fetchone()
        if r and r["px"] is not None:
            bdb.close()
            return float(r["px"])
        bdb.close()
    except Exception:
        pass

    # 4) Live API best offers (only if available)
    try:
        from engines.utils.api_tools import fetch_live_odds  # your working tool
        odds = fetch_live_odds(None, str(mid), str(sid)) or {}
        # if we’re entering LAY->BACK we care about the lay quote as our entry
        px = odds.get("lay") or odds.get("back")
        return float(px) if px is not None else None
    except Exception:
        return None


def _runner_ids_for_market(mid: str, limit: int = 24) -> list[str]:
    """
    Candidate selectionIds for a market, by cheapest known OC1 first.
    Fallbacks to any seen runners if no OC1 rows exist.
    """
    sids: list[str] = []
    try:
        from engines.config_paths import auto_conn
        con = auto_conn(rw=True)
        rows = _q_retry(con,
            "SELECT selectionId, MIN(oc1) AS m FROM inbound_oc_cache "
            "WHERE marketId=? AND oc1 IS NOT NULL GROUP BY selectionId "
            "ORDER BY m ASC LIMIT ?",
            (str(mid), int(limit))
        ).fetchall()
        if rows:
            sids = [str(r["selectionId"]) for r in rows]
        con.close()
    except Exception:
        pass

    if sids:
        return sids

    # fallback to any known runners table
    try:
        from engines.config_paths import auto_conn
        con = auto_conn(rw=True)
        rows = _q_retry(con,
            "SELECT selectionId FROM runners WHERE marketId=? LIMIT ?",
            (str(mid), int(limit))
        ).fetchall()
        con.close()
        return [str(r["selectionId"]) for r in rows or []]
    except Exception:
        return []





# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/helpers.py
# 🔎 SEARCH: def tbl_exists(
# ⛏️ ACTION: replace body to use exec_rows/q_retry

def tbl_exists(con, table: str) -> bool:
    try:
        return bool(exec_rows(con,
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (str(table),)
        ))
    except Exception:
        return False
# === PATCH END ===


# Back-compat alias — older code imports `_tbl_exists`
_tbl_exists = tbl_exists
# === PATCH END ===


# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/helpers.py
# 🔎 SEARCH: def build_scope(
# ⛏️ ACTION: insert the following helpers after the build_scope(...) function

def in_pre_window(mto_minutes: float | int | None) -> bool:
    """
    True when the market is PRE-off. Convention: mto > 0 -> PRE window.
    """
    try:
        return float(mto_minutes) > 0.0
    except Exception:
        return False

def in_ip_window(mto_minutes: float | int | None) -> bool:
    """
    True when the market is IN-PLAY (or at/after the off). Convention: mto <= 0.
    """
    try:
        return float(mto_minutes) <= 0.0
    except Exception:
        return False

# Back-compat aliases (older code paths)
_in_pre_window = in_pre_window
_in_ip_window  = in_ip_window
# === PATCH END ===


# ── schedule / scope adapters ────────────────────────────────────────────────

def is_today_in_scope(market_id: str) -> bool:
    try:
        from engines.decision_engine.scope_helpers import is_today_in_scope as _impl  # type: ignore
        return bool(_impl(market_id))
    except Exception:
        return True


def build_scope(now_utc: Optional[datetime] = None, inplay_window_min: int = 15) -> Dict[str, List[Tuple[str, float]]]:
    try:
        from engines.decision_engine.scope_helpers import build_scope as _impl  # type: ignore
        return _impl(now_utc=now_utc or utc_now(), inplay_window_min=inplay_window_min) or {}
    except Exception:
        return {"pre_far": [], "pre_near": [], "in_play": []}

# ── prices / odds (best-effort imports) ──────────────────────────────────────

def latest_price(market_id: str, selection_id: str) -> Optional[float]:
    try:
        from engines.decision_engine.price import latest_price as _impl  # type: ignore
        return _impl(market_id, selection_id)
    except Exception:
        return None

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/helpers.py
# 🔎 SEARCH: def exec_rows(
# ⛏️ ACTION: ensure exec_rows uses q_retry and returns a list
def exec_rows(con, sql: str, params: tuple = ()) -> list[tuple]:
    try:
        cur = q_retry(con, sql, params)
        return cur.fetchall() or []
    except Exception:
        return []
# === PATCH END ===


# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/helpers.py
# 🔎 SEARCH: def latest_prices_for_market(market_id: str) -> Dict[str, float]:
# ⛏️ ACTION: replace the function header section up to the project-module try with this pre-read block

from typing import Dict
import sqlite3, json

def _as(row, key, idx):
    try:
        return row[key]
    except Exception:
        try:
            return row[idx]
        except Exception:
            return None

def _first_per_sid(rows, sid_key, px_key) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for r in rows or []:
        sid = _as(r, sid_key, 0)
        px  = _as(r, px_key, 1)
        if sid is None or px is None:
            continue
        s = str(sid)
        if s not in out:
            try:
                out[s] = float(px)
            except Exception:
                pass
    return out

# --- DROP-IN: robust latest_prices_for_market (RO only) ----------------------
def latest_prices_for_market(market_id: str) -> Dict[str, float]:
    """
    {selectionId: odds} without requiring any writes.
    Order of attempts (all read-only):
      1) AUTO_DB.inbound_oc_cache (latest oc1 per selection; fallback anchor_odd or band tail)
      2) AUTO_DB.oc_series (today, latest 'odd' per selection)
      3) BETS_DB.bets COALESCE(anchor_odd, OC0)
      4) BETS_DB.oc_series (today, latest 'odd' per selection)
    """
    out: Dict[str, float] = {}

    # 1) inbound_oc_cache (latest row per selection)
    try:
        from engines.config_paths import auto_conn
        con = auto_conn(rw=True)
        if con:
            rows = _q_retry(con, """
                SELECT t.selectionId AS sid,
                       t.oc1,
                       t.anchor_odd,
                       t.oc1_band_json
                  FROM inbound_oc_cache t
                  JOIN (
                        SELECT selectionId, MAX(id) AS max_id
                        FROM inbound_oc_cache
                        WHERE marketId=?
                        GROUP BY selectionId
                  ) u ON u.selectionId=t.selectionId AND u.max_id=t.id
                 WHERE t.marketId=?
            """, (market_id, market_id)).fetchall()

            for r in rows or []:
                sid = str(r["sid"] if isinstance(r, dict) else r[0])
                oc1 = r["oc1"] if not isinstance(r, dict) else r["oc1"]
                if oc1 is not None:
                    out[sid] = float(oc1)
                    continue
                # fallback: anchor_odd
                anch = r["anchor_odd"] if not isinstance(r, dict) else r["anchor_odd"]
                if anch is not None:
                    out[sid] = float(anch)
                    continue
                # fallback: band tail
                bj = r["oc1_band_json"] if not isinstance(r, dict) else r["oc1_band_json"]
                if bj:
                    try:
                        arr = json.loads(bj)
                        if isinstance(arr, list) and arr:
                            out[sid] = float(arr[-1])
                    except Exception:
                        pass
            try: con.close()
            except Exception: pass
    except Exception:
        pass
    if out:
        return out

    # 2) AUTO_DB.oc_series (today)
    try:
        from engines.config_paths import auto_conn
        con = auto_conn(rw=True)
        if con:
            rows = _q_retry(con, """
                SELECT selectionId, odd
                  FROM oc_series
                 WHERE marketId=? AND date(snapshot_ts)=date('now','utc')
                 ORDER BY selectionId ASC, datetime(snapshot_ts) DESC
            """, (market_id,)).fetchall()
            latest: Dict[str, float] = {}
            for r in rows or []:
                sid = str(r["selectionId"])
                if sid in latest:
                    continue
                px = r["odd"]
                if px is not None:
                    latest[sid] = float(px)
            try: con.close()
            except Exception: pass
            if latest:
                return latest
    except Exception:
        pass

    # 3) BETS_DB.bets COALESCE(anchor_odd, OC0)
    try:
        try:
            bdb = open_bets_db(ro=True)
        except Exception:
            from engines.config_paths import connect_db as _connect
            bdb = _connect(ro=True)
        if bdb:
            bdb.row_factory = sqlite3.Row
            rows = _q_retry(bdb, """
                SELECT selectionId, COALESCE(anchor_odd, OC0) AS px
                  FROM bets
                 WHERE marketId=?
                 GROUP BY selectionId
            """, (market_id,)).fetchall()
            bets_px = {str(r["selectionId"]): float(r["px"])
                       for r in rows or [] if r["px"] is not None}
            try: bdb.close()
            except Exception: pass
            if bets_px:
                return bets_px
    except Exception:
        pass

    # 4) BETS_DB.oc_series (today)
    try:
        try:
            bdb = open_bets_db(ro=True)
        except Exception:
            from engines.config_paths import connect_db as _connect
            bdb = _connect(ro=True)
        if bdb:
            bdb.row_factory = sqlite3.Row
            rows = _q_retry(bdb, """
                SELECT selectionId, odd
                  FROM oc_series
                 WHERE marketId=? AND date(snapshot_ts)=date('now','utc')
                 ORDER BY selectionId ASC, datetime(snapshot_ts) DESC
            """, (market_id,)).fetchall()
            latest_bets: Dict[str, float] = {}
            for r in rows or []:
                sid = str(r["selectionId"])
                if sid in latest_bets:
                    continue
                px = r["odd"]
                if px is not None:
                    latest_bets[sid] = float(px)
            try: bdb.close()
            except Exception: pass
            if latest_bets:
                return latest_bets
    except Exception:
        pass

    return {}

# --- odds cache for health + candidates --------------------------------------
# mid -> { selectionId: price }
_ODDS_CACHE: dict[str, dict[str, float]] = {}

def publish_odds_for_market(mid: str, odds: dict[str, float]) -> None:
    """Publish (replace) latest odds map for a market; used by odds_service & fallbacks."""
    try:
        mid = str(mid)
        _ODDS_CACHE[mid] = {str(k): float(v) for k, v in (odds or {}).items() if v is not None}
    except Exception:
        pass

def odds_map(mid: str) -> dict[str, float]:
    """Return cached odds for a market; empty dict if none."""
    return _ODDS_CACHE.get(str(mid), {}) or {}


# ============================================================
# 📍 TARGET: engines/decision_engine/decide_once/helpers.py
# 🔎 SEARCH: def _runner_activity
# ⛏️ ACTION: remove old runner_activity stubs, replace with clean no-ops
# ============================================================

# --- runner activity stubs (removed DB dependency) --------------------------
def _runner_activity(market_id: str, selection_id: str) -> str:
    """
    Placeholder for legacy runner_activity.
    Always returns 'active'. 
    This removes dependency on non-existent runner_activity table.
    """
    return "active"

# ── odds math ────────────────────────────────────────────────────────────────

def tick_size(o: float) -> float:
    try:
        if o < 2.0: return 0.01
        if o < 3.0: return 0.02
        if o < 4.0: return 0.05
        if o < 6.0: return 0.1
        if o < 10.0: return 0.2
        if o < 20.0: return 0.5
        if o < 30.0: return 1.0
        if o < 50.0: return 2.0
        return 5.0
    except Exception:
        return 0.01




def odds_plus_ticks(o: float, ticks: int) -> float:
    try:
        px = float(o); step = tick_size(px)
        return round(px + (step * int(ticks)), 2)
    except Exception:
        return o

# ── rulebook / caps / tags (adapters) ────────────────────────────────────────

def rulebook_allow(letter: str, pass_n: int, ctx: dict, plan: dict) -> Tuple[bool, Optional[dict]]:
    try:
        from engines.decision_engine.rulebook import rulebook_allow as _impl  # type: ignore
        return _impl(letter, pass_n, ctx, plan)
    except Exception:
        return True, plan


def apply_rulebook(letter: str, tag: str, ctx: dict, plan: dict) -> Tuple[bool, dict]:
    try:
        from engines.decision_engine.rulebook import apply_rulebook as _impl  # type: ignore
        return _impl(letter, tag, ctx, plan)
    except Exception:
        return False, plan


# === TRIPLE-HEADER PATCH ======================================================
# 📍 TARGET: engines/decision_engine/decide_once/helpers.py
# 🔎 SEARCH: def can_open_scalp(market_id: str, selection_id: str, *, max_per_runner: int, run_id: str, family_letter: str) -> Tuple[bool, str]:
# ============================================================================


from typing import Tuple

def can_open_scalp(market_id: str, selection_id: str, *, max_per_runner: int, run_id: str, family_letter: str) -> Tuple[bool, str]:
    """
    Delegate to engines.mastery.risk.can_open_scalp (per-runner, per-letter CAP).
    On import failure, conservatively allow (fallback) — callers should log blocks elsewhere.
    """
    try:
        from engines.mastery import risk as _risk  # ← corrected location
        return _risk.can_open_scalp(market_id, selection_id,
                                    max_per_runner=max_per_runner,
                                    run_id=run_id, family_letter=family_letter)
    except Exception:
        return True, "fallback"
# === END PATCH ================================================================



def next_pair_tag(letter: str, market_id: str) -> str:
    return f"{letter}:{market_id}:{minute_bucket()}"


def pass_tag(letter: str, mto: float) -> str:
    return f"{letter}:{int(mto)}:{minute_bucket()}"


def already_open_pass(market_id: str, selection_id: str, tag: str, *, mode: str) -> bool:
    try:
        from engines.decision_engine.pass_tags import already_open_pass as _impl  # type: ignore
        return bool(_impl(market_id, selection_id, tag, mode=mode))
    except Exception:
        return False

# ── placement adapters ───────────────────────────────────────────────────────

def queue_order(plan: dict, ctx: dict) -> Optional[int]:
    try:
        from engines.decision_engine.placement import queue_order as _impl  # type: ignore
        return _impl(plan, ctx)
    except Exception:
        return None


def place_decision(name: str, plan: dict, ctx: dict) -> Optional[int]:
    try:
        from engines.decision_engine.placement import place_decision as _impl  # type: ignore
        return _impl(name, plan, ctx)
    except Exception:
        return None


def place_companion_hedge(parent_id: int, plan: dict, ctx: dict) -> Optional[int]:
    try:
        from engines.decision_engine.placement import place_companion_hedge as _impl  # type: ignore
        return _impl(parent_id, plan, ctx)
    except Exception:
        return None


def set_order_status(order_id: int, status: str, reason: str = "") -> None:
    try:
        from engines.decision_engine.placement import set_order_status as _impl  # type: ignore
        return _impl(order_id, status, reason)
    except Exception:
        return None


def mark_last_child_placed(parent_id: int) -> None:
    try:
        from engines.decision_engine.placement import mark_last_child_placed as _impl  # type: ignore
        return _impl(parent_id)
    except Exception:
        return None

# ── candidates (internal) ────────────────────────────────────────────────────

def _runner_is_active(mid: str, sid: str, o: float) -> bool:
    try:
        if not (1.5 <= float(o) <= 8.0):
            return False
    except Exception:
        return False
    try:
        return _runner_activity(mid, sid) == "active"
    except Exception:
        return False

from typing import List, Tuple
import os

# --- DROP-IN: active candidates that NEVER depend on writes ------------------
def _active_candidates_for_market(mid: str, max_runners: int = 8) -> list[tuple[str, float]]:
    """
    Build candidates **without** odds_current. We look directly at OC/anchors/API.
    Never emits 'no odds map' and never writes to DB.
    """
    try:
        sids = _runner_ids_for_market(mid, limit=max(12, max_runners * 2))
    except Exception:
        sids = []

    pool: list[tuple[str, float]] = []
    for sid in sids:
        try:
            px = _market_odds(mid, sid)
            if px is None:
                continue
            # fast activity gate (keeps your snapshots logic)
            if _runner_activity(mid, sid) != "active":
                continue
            pool.append((sid, float(px)))
        except Exception:
            continue

    if not pool:
        # last nudge: allow top few by any price we can get (activity unknown→accept)
        cheap: list[tuple[str, float]] = []
        for sid in sids:
            px = _market_odds(mid, sid)
            if px is not None:
                cheap.append((sid, float(px)))
        pool = sorted(cheap, key=lambda t: (t[1], t[0]))[:max_runners]

    # order by price asc and do round-robin
    try:
        rr_key, order_key = "_RR_CURSOR", "_RR_ORDER"
        cursors = globals().setdefault(rr_key, {})
        orders  = globals().setdefault(order_key, {})
        cur_ids = [sid for (sid, _px) in sorted(pool, key=lambda t: (t[1], t[0]))]
        if orders.get(mid) != cur_ids:
            orders[mid]  = cur_ids
            cursors[mid] = int(cursors.get(mid, 0)) % max(1, len(cur_ids))
        cur = int(cursors.get(mid, 0))
        seq = orders[mid][cur:] + orders[mid][:cur]
        # emit at most max_runners, with real prices
        out: list[tuple[str, float]] = []
        seen: set[str] = set()
        for sid in seq:
            if sid in seen: continue
            seen.add(sid)
            px = _market_odds(mid, sid)
            if px is None: continue
            out.append((sid, float(px)))
            if len(out) >= max_runners: break
        if orders.get(mid):
            cursors[mid] = (cur + max(1, max_runners)) % len(orders[mid])
        return out
    except Exception:
        return sorted(pool, key=lambda t: (t[1], t[0]))[:max_runners]

# Keep the alias:
active_candidates_for_market = _active_candidates_for_market


import os, sqlite3
from datetime import date
from engines.config_paths import autoscalp_db

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/helpers.py:has_ran_today
# 🔎 SEARCH: def has_ran_today(
# 📆 PATCHED: 2025-11-21 — route AUTOSCALP access through DAL

from engines.config_paths import auto_conn as _auto_conn

def has_ran_today() -> bool:
    key = f"blueprints_ran_{date.today().isoformat()}"
    con = _auto_conn(rw=False)
    con.row_factory = sqlite3.Row
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS app_kv(
              key TEXT PRIMARY KEY,
              value TEXT,
              updated_at TEXT DEFAULT (datetime('now','utc'))
            )
        """)
        row = con.execute("SELECT value FROM app_kv WHERE key=?", (key,)).fetchone()
        return bool(row)
    finally:
        try: con.close()
        except Exception: pass
# === PATCH END ===


def run_if_needed(logger=None):
    log = logger or (lambda *a, **k: None)
    from datetime import datetime, timezone
    try:
        # use the canonical connector here
        from engines.config_paths import auto_conn as _auto_conn
        con = _auto_conn()
        con.execute("""
            CREATE TABLE IF NOT EXISTS app_kv(
              key TEXT PRIMARY KEY,
              value TEXT,
              updated_at TEXT DEFAULT (datetime('now','utc'))
            )
        """)
        today = datetime.now(timezone.utc).date().isoformat()
        key = f"blueprints_ran_{today}"
        row = con.execute("SELECT value FROM app_kv WHERE key=?", (key,)).fetchone()
        if row:
            log("↩️ Blueprints already ran today — skipping.")
            con.close()
            return

        # Import the actual builder location
        try:
            from engines.blueprint_build import main as blueprint_main
        except Exception as e:
            log(f"[blueprints] warn: {e}")
            con.close()
            return

        blueprint_main(force=False)   # MUST NOT sys.exit()
        con.execute("INSERT OR REPLACE INTO app_kv(key,value,updated_at) VALUES(?, '1', datetime('now','utc'))", (key,))
        con.commit()
        con.close()
        log("✅ Running Enhanced Blueprint Builder")
    except Exception as e:
        try:
            con.close()
        except Exception:
            pass
        log(f"[blueprints] helper warn: {e}")


# ── telemetry dummies (safe no-ops) ──────────────────────────────────────────

def gate_bump(*_a, **_k):
    return None

def gate_snapshot() -> dict:
    return {}

# legacy misc aliases
log_event_once = status_once
_utcnow = utc_now
_open_adb = open_auto_db
_tbl_exists = lambda *_a, **_k: False
_is_today_in_scope = is_today_in_scope
_build_scope = build_scope
_apply_rulebook = apply_rulebook
_can_open_scalp = can_open_scalp
_next_pair_tag = next_pair_tag
_pass_tag = pass_tag
_already_open_pass = already_open_pass
_queue_order = queue_order
_place_decision = place_decision
_place_companion_hedge = place_companion_hedge
_set_order_status = set_order_status
_mark_last_child_placed = mark_last_child_placed
_tick_size = tick_size
_odds_plus_ticks = odds_plus_ticks
