#!/usr/bin/env python3
"""
DB + system adapters for the Decision Engine (Phase 1).
No external deps beyond stdlib and project modules.
"""
from __future__ import annotations

import os, json, sqlite3
from typing import Optional, Dict, Any, List, Tuple
from engines.utils.time_utils import now_utc
import threading
import re

# add near top-level singletons
_CRED_SOURCE_LOCK = "DB"  # default to DB unless explicitly unlocked
_APP_KEY = None
_SESSION = None

def lock_cred_source(src: str = "DB"):
    global _CRED_SOURCE_LOCK
    _CRED_SOURCE_LOCK = str(src or "DB").upper()

def _resolve_from_db():
    try:
        from engines.session_secrets import load_betfair_creds
        ak, ss = load_betfair_creds()
        if ak and ss:
            return str(ak), str(ss)
    except Exception:
        return None, None
    return None, None

def _resolve_from_env():
    import os
    ak = os.environ.get("BETFAIR_APP_KEY") or None
    ss = os.environ.get("BETFAIR_SESSION") or None
    return (ak, ss)

def _resolve_from_json():
    try:
        from engines.config_paths import autoscalp_db
        import os, json
        base = os.path.dirname(autoscalp_db())
        p = os.path.join(base, "betfair_creds.json")
        if os.path.exists(p):
            o = json.load(open(p, "r", encoding="utf-8"))
            return (o.get("app_key") or o.get("application_key"), o.get("session") or o.get("session_token") or o.get("ssoid"))
    except Exception:
        pass
    return None, None

def force_reload_creds():
    """Resolve creds using locked priority; cache into globals; return bool ok."""
    global _APP_KEY, _SESSION
    order = ["DB", "ENV", "JSON"] if _CRED_SOURCE_LOCK == "DB" else ["ENV", "JSON", "DB"]
    ak = ss = None
    for src in order:
        if src == "DB":
            ak, ss = _resolve_from_db()
        elif src == "ENV":
            ak, ss = _resolve_from_env()
        else:
            ak, ss = _resolve_from_json()
        if ak and ss:
            _APP_KEY, _SESSION = ak, ss
            return True
    _APP_KEY = _SESSION = None
    return False

def ensure_betfair_ready() -> bool:
    ok = bool(_APP_KEY and _SESSION) or force_reload_creds()
    if not ok:
        print("[adapters] creds missing")
    return ok

def configure_betfair(app_key: str, session: str):
    """Allow GUI to push creds in-process; also persists them to DB via session_secrets."""
    global _APP_KEY, _SESSION
    _APP_KEY, _SESSION = str(app_key or ""), str(session or "")
    try:
        from engines.session_secrets import set_secret, APP_KEY_KEY, SESSION_KEY
        set_secret(APP_KEY_KEY, _APP_KEY); set_secret(SESSION_KEY, _SESSION)
    except Exception:
        pass

def get_keys() -> tuple[str|None, str|None]:
    return _APP_KEY, _SESSION




# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: engines/decision_engine/adapters.py
# 🔎 SEARCH: ^def inbound_candidates\(
def inbound_candidates(day_iso: str | None = None):
    """
    Return rows only for today's (UTC) markets; prevents stale markets from triggering odds fetches.
    """
    from engines.config_paths import connect_db
    from datetime import datetime, timezone
    day = (day_iso or datetime.now(timezone.utc).date().isoformat())

    with connect_db(ro=True) as conn:
        conn.row_factory = sqlite3.Row

        # Prefer markets present in bets for 'day'
        rows = conn.execute(
            "SELECT DISTINCT b.marketId AS marketId, r.selectionId AS selectionId "
            "FROM bets b "
            "JOIN inbound_oc_cache r ON r.marketId = b.marketId "
            "WHERE date(b.marketStartTime) = date(?) "
            "AND date(r.last_sync_ts) = date(?) "
            "ORDER BY b.marketId ASC, r.selectionId ASC",
            (day, day)
        ).fetchall()

        if rows:
            return [dict(r) for r in rows]

        # Fallback: inbound cache only, still scoped to today
        rows = conn.execute(
            "SELECT DISTINCT marketId, selectionId "
            "FROM inbound_oc_cache "
            "WHERE date(last_sync_ts) = date(?) "
            "ORDER BY marketId ASC, selectionId ASC",
            (day,)
        ).fetchall()
        return [dict(r) for r in rows]



# ── Credentials bootstrap (unified: DB > ENV > JSON)
import os, json
from engines.session_secrets import load_betfair_creds
from engines.config_paths import autoscalp_db

_BF_APP_KEY = None
_BF_SESSION = None



def _json_creds_path() -> str:
    base = os.path.dirname(autoscalp_db())
    return os.path.join(base, "betfair_creds.json")

def _load_from_env():
    return os.getenv("BETFAIR_APP_KEY"), os.getenv("BETFAIR_SESSION")

def _load_from_json():
    try:
        with open(_json_creds_path(), "r", encoding="utf-8") as f:
            j = json.load(f)
        return j.get("app_key"), j.get("session")
    except Exception:
        return None, None

def _fingerprint(tok: str | None) -> str:
    if not tok: return "NONE"
    return f"{tok[:6]}…{tok[-4:]}" if len(tok) > 10 else tok

def _load_creds_unified():
    """
    Return a triple (app_key, session, source) or (None, None, None).
    Precedence: DB > ENV > JSON, unless locked to DB.
    """
    # DB first
    try:
        from engines.session_secrets import load_betfair_creds
        ak, st = load_betfair_creds()
        if ak and st:
            return ak, st, "DB"
    except Exception:
        pass
    try:
        from engines.upgrade_import_patch import get_session_token, get_app_key
        st = get_session_token() or None
        ak = get_app_key() or None
        if ak and st:
            return ak, st, "SHIM"
    except Exception:
        pass
    # If we already have runtime creds, keep them
    if _BF_APP_KEY and _BF_SESSION:
        return _BF_APP_KEY, _BF_SESSION, "RUNTIME"

    # If locked to DB, do not downgrade to ENV/JSON
    if _CRED_SOURCE_LOCK == "DB":
        try:
            ak, st = load_betfair_creds()
            if ak and st:
                return ak, st, "DB"
        except Exception:
            pass
        return (None, None, None)

    # Normal precedence
    try:
        ak, st = load_betfair_creds()
        if ak and st:
            return ak, st, "DB"
    except Exception:
        pass

    ak, st = _load_from_env()
    if ak and st:
        return ak, st, "ENV"

    ak, st = _load_from_json()
    if ak and st:
        return ak, st, "JSON"

    return (None, None, None)





def keep_alive() -> bool:
    # TODO: swap for a real ping; for now true if we have globals
    return bool(_BF_APP_KEY and _BF_SESSION)



# ──────────────────────────────────────────────────────────────────────────────
# Paths & Time  
# ──────────────────────────────────────────────────────────────────────────────
# Paths
import engines.config_paths as cp


# Time helpers
from engines.utils.time_utils import now_utc


# ──────────────────────────────────────────────────────────────────────────────
# budget
# ──────────────────────────────────────────────────────────────────────────────
try:
    from engines.utils.time_utils import now_utc  # returns aware UTC datetime
except Exception:
    from datetime import datetime, timezone
    def now_utc():
        return datetime.now(timezone.utc)

try:
    # canonical budget hook (read-only for Phase 1)
    from engines.daily_config import fetch_available_budget  # type: ignore
except Exception:
    def fetch_available_budget() -> float:
        return 800.0
# ───────────────────────────────────────────────────────────────────────────────
# Unique CustomerOrderRef builder (AA###_BOT_TRK_DIST) — canonical source
# ───────────────────────────────────────────────────────────────────────────────
_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_SUFFIX_LOCK = threading.Lock()
_SUFFIX_INDEX: Optional[int] = None  # numeric counter base

def _prefix_to_base(prefix: str) -> Optional[int]:
    """
    Convert 'AA123' -> numeric base (A=0..Z=25; base = a*26*1000 + b*1000 + n).
    Returns None if shape invalid.
    """
    if not prefix or len(prefix) < 5:
        return None
    a, b, digits = prefix[0], prefix[1], prefix[2:5]
    if not (a.isalpha() and b.isalpha() and digits.isdigit()):
        return None
    return (ord(a.upper()) - 65) * 26 * 1000 + (ord(b.upper()) - 65) * 1000 + int(digits)

def _scan_max_suffix_in_orders() -> int:
    """
    Look across existing orders for any 'AA###_*' style and return max numeric base.
    If none found, return -1 so next becomes 0.
    """
    try:
        with _con(cp.autoscalp_db()) as con:
            rows = con.execute("""
                SELECT substr(customerOrderRef,1,5) AS p
                FROM orders
                WHERE customerOrderRef GLOB '[A-Z][A-Z][0-9][0-9][0-9]_*'
            """).fetchall()
        mx = -1
        for r in rows or []:
            base = _prefix_to_base(r["p"])
            if base is not None and base > mx:
                mx = base
        return mx
    except Exception:
        return -1

def _next_suffix() -> str:
    """
    Thread-safe monotonic suffix generator: AA000, AA001, ..., AZ999, BA000, ...
    Seeds from DB on first call (orders table), then increments in-memory.
    """
    global _SUFFIX_INDEX
    with _SUFFIX_LOCK:
        if _SUFFIX_INDEX is None:
            max_found = _scan_max_suffix_in_orders()
            _SUFFIX_INDEX = max_found + 1
        code = _SUFFIX_INDEX
        _SUFFIX_INDEX += 1

    a_idx = (code // 1000) // 26
    b_idx = (code // 1000) % 26
    if a_idx >= len(_LETTERS) or b_idx >= len(_LETTERS):
        # Extremely unlikely for Phase 1. If it ever happens, reset/expand alphabet.
        a_idx, b_idx, code = 25, 25, 999  # ZZ999 cap
    return f"{_LETTERS[a_idx]}{_LETTERS[b_idx]}{code % 1000:03d}"

# --- Track codes (subset; extend as needed) -----------------------------------
_TRACK_CODES = {
    # GB
    "Ascot":"ASC","Aintree":"AIN","Ayr":"AYR","Bangor":"BAN","Bath":"BAT",
    "Beverley":"BEV","Brighton":"BRI","Carlisle":"CAR","Cartmel":"CTM","Catterick":"CAT",
    "Chelmsford":"CHL","Cheltenham":"CHE","Chepstow":"CHP","Chester":"CHS","Doncaster":"DON",
    "Epsom":"EPS","Exeter":"EXE","Fakenham":"FAK","Fontwell":"FON","Goodwood":"GOO",
    "Hamilton":"HAM","Haydock":"HAY","Hereford":"HER","Hexham":"HEX","Huntingdon":"HUN",
    "Kelso":"KEL","Kempton":"KEM","Leicester":"LEI","Lingfield":"LIN","Ludlow":"LUD",
    "Market Rasen":"MAR","Musselburgh":"MUS","Newbury":"NEW","Newcastle":"NCS","Newmarket":"NMK",
    "Newton Abbot":"NAB","Nottingham":"NOT","Perth":"PER","Plumpton":"PLU","Pontefract":"PON",
    "Redcar":"RED","Ripon":"RIP","Salisbury":"SAL","Sandown":"SAN","Sedgefield":"SED",
    "Southwell":"STH","Stratford":"STR","Taunton":"TAU","Thirsk":"THI","Uttoxeter":"UTT",
    "Warwick":"WAR","Wetherby":"WET","Windsor":"WIN","Wolverhampton":"WOL","Worcester":"WOR",
    "Yarmouth":"YAR","York":"YOR",
    # IRE
    "Bellewstown":"BEL","Clonmel":"CLO","Cork":"COR","Curragh":"CUR","Downpatrick":"DOW",
    "Down Royal":"DRO","Dundalk":"DUN","Fairyhouse":"FAI","Galway":"GAL","Gowran Park":"GOW",
    "Kilbeggan":"KIL","Killarney":"KLY","Laytown":"LAY","Leopardstown":"LEO","Limerick":"LIM",
    "Listowel":"LIS","Naas":"NAA","Navan":"NAV","Punchestown":"PUN","Roscommon":"ROS",
    "Sligo":"SLI","Thurles":"THU","Tipperary":"TIP","Tramore":"TRA","Wexford":"WEX",
}

def _track_code(event_name: Optional[str]) -> str:
    if not event_name:
        return "UNK"
    name = event_name.strip()
    # exact
    if name in _TRACK_CODES:
        return _TRACK_CODES[name]
    # try prefix/substring match
    for k, v in _TRACK_CODES.items():
        if k.lower() in name.lower():
            return v
    return "UNK"

# Distance / type parsing — very simple Phase-1 rules; expand later
_DIST_RX = re.compile(r'(?:(\d+)m)?\s*(?:(\d+)f)?', re.IGNORECASE)

def _distance_code_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    m = _DIST_RX.search(text.replace(" ", ""))
    if not m:
        return None
    mls = m.group(1); frl = m.group(2)
    if mls and frl:
        return f"{int(mls)}M{int(frl)}F"
    if mls:
        return f"{int(mls)}M"
    if frl:
        return f"{int(frl)}F"
    return None

def _distance_code(meta: Dict[str, Any]) -> str:
    for key in ("market_name", "race_name"):
        v = meta.get(key)
        if v:
            d = _distance_code_from_text(str(v))
            if d:
                return d.upper()
    return "UNK"

def build_customer_order_ref(marketId: str, selectionId: int, bot_name: Optional[str] = None) -> str:
    """
    Build <=28 char ref: AA###_BOT_TRK_DIST
    - BOT: first 3 uppercase letters of bot (env AUTOSCALP_BOT or 'ASL')
    - TRK: 3-letter course code
    - DIST: parsed distance (e.g., 7F, 1M4F), else UNK
    Ensures uniqueness by checking orders table; retries once if needed.
    """
    meta = get_market_meta(marketId)  # event_name, market_name, race_name, start
    bot = (bot_name or os.environ.get("AUTOSCALP_BOT") or "ASL")[:3].upper()
    trk = _track_code(meta.get("event_name"))
    dst = _distance_code(meta)

    # first attempt
    base = _next_suffix()
    ref = f"{base}_{bot}_{trk}_{dst}"
    ref = ref[:28]  # hard cap

    try:
        with _con(cp.autoscalp_db()) as con:
            hit = con.execute(
                "SELECT 1 FROM orders WHERE customerOrderRef=? LIMIT 1", [ref]
            ).fetchone()
        if not hit:
            return ref
        # retry once with a fresh suffix
        ref2 = f"{_next_suffix()}_{bot}_{trk}_{dst}"
        return ref2[:28]
    except Exception:
        # last-ditch fallback
        return ref

# ──────────────────────────────────────────────────────────────────────────────
# Price math helpers (ticks)
# ──────────────────────────────────────────────────────────────────────────────
try:
    from engines.price_math import tick_diff as _tick_diff, add_ticks as _add_ticks  # type: ignore
except Exception:
    def _tick_diff(a: float, b: float) -> int:
        return int(round((b - a) / 0.01))
    def _add_ticks(p: float, n: int) -> float:
        return round(p + n * 0.01, 2)

# Expose small helpers
add_ticks = _add_ticks
tick_diff = _tick_diff

# ──────────────────────────────────────────────────────────────────────────────
# Modes (display vs storage)
# ──────────────────────────────────────────────────────────────────────────────
_DISPLAY_MODES = {"TEST", "LEARNING", "LIVE"}

def normalize_mode(mode: str) -> str:
    m = (mode or "LEARNING").upper()
    return m if m in _DISPLAY_MODES else "LEARNING"

def storage_mode(mode: str) -> str:
    # Accept TEST / LEARNING / LIVE directly; coerce legacy SIM → LEARNING
    m = (mode or "LEARNING").upper()
    if m == "SIM":
        return "LEARNING"
    return m if m in {"TEST","LEARNING","LIVE"} else "LEARNING"

# ──────────────────────────────────────────────────────────────────────────────
# Basic SQL helpers
# ──────────────────────────────────────────────────────────────────────────────
def _con(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    return con

def _row_get(row: sqlite3.Row | None, key: str) -> Any:
    if row is None:
        return None
    try:
        # sqlite3.Row supports dict-style access, not .get()
        return row[key]
    except Exception:
        try:
            if hasattr(row, "keys") and key in row.keys():
                return row[key]
        except Exception:
            pass
        return None

# ──────────────────────────────────────────────────────────────────────────────
# Bets DB lookups (bets.db)
# ──────────────────────────────────────────────────────────────────────────────
def get_market_meta(marketId: str) -> Dict[str, Any]:
    """Return start time + names from bets.bets (best-effort)."""
    sql = """
        SELECT marketStartTime, event_name, market_name, race_name
        FROM bets
        WHERE marketId = ?
        ORDER BY timestamp DESC
        LIMIT 1
    """
    with _con(cp.bets_db()) as con:
        row = con.execute(sql, [marketId]).fetchone()
    if not row:
        return {"marketStartTime": None, "event_name": None, "market_name": None, "race_name": None}
    return dict(row)

def get_anchor_from_bets(marketId: str, selectionId: int) -> Optional[float]:
    with _con(cp.bets_db()) as con:
        row = con.execute(
            "SELECT anchor_odd FROM bets WHERE marketId=? AND selectionId=? ORDER BY timestamp DESC LIMIT 1",
            [marketId, selectionId],
        ).fetchone()
    v = _row_get(row, "anchor_odd")
    return float(v) if v is not None else None

# ──────────────────────────────────────────────────────────────────────────────
# Autoscalp (autoscalp_gui.db) lookups & writes
# ──────────────────────────────────────────────────────────────────────────────
def get_or_create_run(mode: str, notes: str = "") -> int:
    """Insert a run and return run id (Phase-1: always new row)."""
    ts = now_utc().isoformat()
    with _con(cp.autoscalp_db()) as con:
        mode = (mode or 'LEARNING').upper()
        con.execute("INSERT INTO runs(started_at, mode, notes) VALUES(?,?,?)",
                    [ts, mode, notes])
        rid = con.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    return int(rid)


def inbound_candidates(limit_per_market: int = 6) -> List[Dict[str, Any]]:
    """Return minimal set of (marketId, selectionId, horse_name)."""
    sql = """
        SELECT marketId, selectionId, COALESCE(horse_name,'') AS horse_name
        FROM inbound_bets_min
        ORDER BY marketId ASC, selectionId ASC
    """
    with _con(cp.autoscalp_db()) as con:
        rows = con.execute(sql).fetchall()
    return [dict(r) for r in rows]

def oc_cache_row(marketId: str, selectionId: int) -> Optional[sqlite3.Row]:
    sql = "SELECT * FROM inbound_oc_cache WHERE marketId=? AND selectionId=? LIMIT 1"
    with _con(cp.autoscalp_db()) as con:
        return con.execute(sql, [marketId, selectionId]).fetchone()

def current_oc_label(cache_row: sqlite3.Row | None) -> str:
    """Return the highest OCn present as label (OC0 if none)."""
    if not cache_row:
        return "OC0"
    for n in range(20, 0, -1):
        if _row_get(cache_row, f"oc{n}") is not None:
            return f"OC{n}"
    return "OC0"

def band_for_label(cache_row: sqlite3.Row | None, oc_label: str) -> Optional[list]:
    if not cache_row or oc_label == "OC0":
        return None
    try:
        n = int(oc_label[2:])
    except Exception:
        return None
    raw = _row_get(cache_row, f"oc{n}_band_json")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None

def latest_price(cache_row: sqlite3.Row | None) -> Optional[float]:
    """Latest lay sampled (prefer highest OCn value; fall back to anchor)."""
    if not cache_row:
        return None
    for n in range(20, 0, -1):
        v = _row_get(cache_row, f"oc{n}")
        if v is not None:
            return float(v)
    a = _row_get(cache_row, "anchor_odd")
    return float(a) if a is not None else None

def anchor_from_cache(cache_row: sqlite3.Row | None) -> Optional[float]:
    a = _row_get(cache_row, "anchor_odd")
    return float(a) if a is not None else None

def ensure_story(marketId: str, selectionId: int, start_label: str) -> int:
    """
    Ensure a story row exists (idempotent by (marketId,selectionId)).
    Returns the story id. This matches your existing stories schema.
    """
    ts = now_utc().replace(microsecond=0).isoformat()
    sql_ins = (
        "INSERT OR IGNORE INTO stories(marketId, selectionId, story_start_oc, created_at) "
        "VALUES(?,?,?,?)"
    )
    sql_sel = "SELECT id FROM stories WHERE marketId=? AND selectionId=? LIMIT 1"
    with _con(cp.autoscalp_db()) as con:
        con.execute(sql_ins, [marketId, selectionId, start_label, ts])
        row = con.execute(sql_sel, [marketId, selectionId]).fetchone()
    return int(_row_get(row, "id") or 0)

def upsert_chapter(
    story_id: int,
    oc_label: str,
    entry_odds: Optional[float],
    exit_odds: Optional[float],
    band_json: Optional[list],
    tick_pattern: Optional[str],
    direction_bias: Optional[str],
    position_ratio: Optional[float],
    volatility: Optional[float],
    minutes_to_post: Optional[float],
    opened_at: Optional[str],
    closed_at: Optional[str],
) -> None:
    """
    Safe UPSERT without requiring a UNIQUE constraint:
    SELECT → UPDATE if exists, else INSERT.
    """
    js = json.dumps(band_json) if band_json is not None else None
    with _con(cp.autoscalp_db()) as con:
        row = con.execute(
            "SELECT id FROM chapters WHERE story_id=? AND oc_label=? LIMIT 1",
            [story_id, oc_label],
        ).fetchone()
        if row:
            con.execute(
                """
                UPDATE chapters
                   SET entry_odds      = ?,
                       exit_odds       = ?,
                       oc_band_json    = ?,
                       tick_pattern    = ?,
                       direction_bias  = ?,
                       position_ratio  = ?,
                       volatility      = ?,
                       minutes_to_post = ?,
                       opened_at       = COALESCE(opened_at, ?),
                       closed_at       = ?
                 WHERE id = ?
                """,
                [
                    entry_odds, exit_odds, js, tick_pattern, direction_bias,
                    position_ratio, volatility, minutes_to_post, opened_at,
                    closed_at, int(row["id"]),
                ],
            )
        else:
            con.execute(
                """
                INSERT INTO chapters(
                    story_id, oc_label, entry_odds, exit_odds, oc_band_json,
                    tick_pattern, direction_bias, position_ratio, volatility,
                    minutes_to_post, opened_at, closed_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    story_id, oc_label, entry_odds, exit_odds, js,
                    tick_pattern, direction_bias, position_ratio, volatility,
                    minutes_to_post, opened_at, closed_at,
                ],
            )

def record_decision(
    run_id: int,
    marketId: str,
    selectionId: int,
    signal_type: str,
    blueprint_match: Optional[str],
    confidence: float,
    scalp_direction: str,
    proposed_odds: float,
    proposed_stake: float,
    notes: str = "",
) -> int:
    ts = now_utc().replace(microsecond=0).isoformat()
    with _con(cp.autoscalp_db()) as con:
        con.execute(
            """
            INSERT INTO decisions(
                run_id, marketId, selectionId, decided_at,
                signal_type, blueprint_match, confidence,
                scalp_direction, proposed_odds, proposed_stake, notes
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                run_id, marketId, selectionId, ts,
                signal_type, blueprint_match, float(confidence),
                scalp_direction, float(proposed_odds), float(proposed_stake),
                notes,
            ],
        )
        row = con.execute("SELECT last_insert_rowid() AS id").fetchone()
    return int(_row_get(row, "id") or 0)

def record_order(
    run_id: int,
    decision_id: Optional[int],
    customerOrderRef: str,
    marketId: str,
    selectionId: int,
    mode: str,                    # 'TEST' | 'LEARNING' | 'LIVE'
    side: str,                    # 'LAY'|'BACK'
    entry_odds: float,
    entry_stake: float,
    entry_status: str = "queued",
    unrealized_pnl: float = 0.0,
) -> int:
    ts = now_utc().isoformat()
    with _con(cp.autoscalp_db()) as con:
        con.execute(
            """
            INSERT INTO orders(
                run_id, decision_id, customerOrderRef,
                marketId, selectionId, mode, side,
                entry_odds, entry_stake, entry_status,
                unrealized_pnl, opened_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                run_id, decision_id, customerOrderRef,
                marketId, selectionId, (mode or 'LEARNING').upper(), side,
                entry_odds, entry_stake, entry_status,
                unrealized_pnl, ts,
            ],
        )
        row = con.execute("SELECT last_insert_rowid() AS id").fetchone()
    return int(row["id"]) if row else 0

