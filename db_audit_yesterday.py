#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AutoScalp GUI DB Audit — CAP & Rotation (UTC/Z aware, SQLite-safe)

Run with python3 (recommended):
  python3 db_audit_yesterday.py --db /path/to/autoscalp_gui.db [--date YYYY-MM-DD] [--cap 3] [--dump findings.json]

Design:
  • DB timestamps may be stored with 'Z' or naive forms. In SQL we normalize to
    'YYYY-MM-DD HH:MM:SS[.fff]' by REPLACE(...,'T',' ') and REPLACE(...,'Z','').
    We compare using julianday() on both sides — portable across SQLite builds.
  • In Python we parse anything we read and coerce to UTC-aware datetimes.
  • The audit window is a UTC day (yesterday by default). Comparisons are UTC-consistent.
"""

from __future__ import annotations
import argparse
import sqlite3
from dataclasses import dataclass, asdict
from typing import Optional, Tuple, List, Dict, Any, Iterable
from datetime import datetime, timedelta, timezone
import json

# ---------- Utilities (UTC/Z handling) ----------

ISO_PATTERNS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%d",
]

def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def parse_dt(s: Optional[str]) -> Optional[datetime]:
    """
    Parse a wide range of timestamp strings into a timezone-aware UTC datetime.
    Accepts 'Z', explicit offsets, or naive → coerced to UTC. Returns None if invalid.
    """
    if s is None:
        return None
    s = str(s).strip()
    if not s or s in ("0000-00-00", "1970-01-01"):
        return None

    # Try ISO parser first (normalize Z to +00:00)
    s2 = s.replace(" ", "T")
    if s2.endswith("Z"):
        s2 = s2[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception:
        pass

    # Fallback patterns (naive → UTC)
    for pat in ISO_PATTERNS:
        try:
            dt = datetime.strptime(s.replace("T"," "), pat.replace("T"," "))
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue

    return None

def utc_day_window(target_date: Optional[str]) -> Tuple[datetime, datetime]:
    """
    Returns (start,end) for the UTC day. If target_date is None, uses yesterday in UTC.
    """
    if target_date:
        d0 = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        start = d0.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        now_utc = datetime.now(timezone.utc)
        start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    end = start + timedelta(days=1)
    return start, end

def to_sqlite_utc(dt: datetime) -> str:
    """Format aware datetime to 'YYYY-MM-DD HH:MM:SS' in UTC for SQLite julianday()."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def iso_z(dt: datetime) -> str:
    """Format aware datetime to ISO string with 'Z' suffix."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

def connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA busy_timeout=8000;")
    except Exception:
        pass
    return con

def table_has_columns(con: sqlite3.Connection, table: str, cols: Iterable[str]) -> Dict[str, bool]:
    try:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
        names = {r["name"] for r in rows}
        return {c: (c in names) for c in cols}
    except Exception:
        return {c: False for c in cols}

# ---------- Data classes ----------

@dataclass
class CapViolation:
    marketId: str
    selectionId: str
    letter: str
    mode: str
    max_open: int
    cap: int
    at_times: List[str]  # ISO Z timestamps where the max occurred

@dataclass
class StampIssue:
    parent_id: int
    marketId: str
    selectionId: str
    opened_at: Optional[str]

@dataclass
class FinalizeIssue:
    parent_id: int
    marketId: str
    selectionId: str
    opened_at: Optional[str]
    has_matched_child: bool

@dataclass
class RotationDup:
    minute: str
    letter: str
    marketId: str
    selectionId: str
    attempts: int

@dataclass
class AuditResult:
    cap_violations: List[CapViolation]
    stamp_issues: List[StampIssue]
    finalize_issues: List[FinalizeIssue]
    rotation_dups: List[RotationDup]
    schema_missing: Dict[str, List[str]]

# ---------- Core SQL normalization helpers ----------

# Normalize a timestamp expression (column) to 'YYYY-MM-DD HH:MM:SS[.fff]' string without 'T' or 'Z'
def _norm(col: str) -> str:
    # REPLACE twice: 'T' → ' ', drop trailing 'Z' if present
    return f"REPLACE(REPLACE({col}, 'T', ' '), 'Z', '')"

def _jday(col: str) -> str:
    # julianday() of normalized expression
    return f"julianday({_norm(col)})"

def _jval(param: str) -> str:
    # julianday() of a parameter (already 'YYYY-MM-DD HH:MM:SS')
    return f"julianday({param})"

# ---------- Core queries ----------

REQUIRED_ORDERS_COLS = [
    "id", "marketId", "selectionId", "opened_at", "closed_at",
    "hedge_of", "source", "mode", "entry_status", "customer_ref",
]

def load_parents_for_window(con, start_utc: datetime, end_utc: datetime):
    """
    Parents overlapping the UTC window: [opened_at, closed_at) intersects [start,end).
    Uses julianday() comparisons with normalized strings for SQLite portability.
    """
    start_sql = to_sqlite_utc(start_utc)
    end_sql = to_sqlite_utc(end_utc)
    sql = f"""
        SELECT id, marketId, selectionId,
               COALESCE(source,'') AS source,
               COALESCE(mode,'') AS mode,
               opened_at, closed_at
          FROM orders
         WHERE (hedge_of IS NULL OR hedge_of='')
           AND {_jday("COALESCE(opened_at, '1900-01-01 00:00:00')")} < {_jval("?")}
           AND (
                 closed_at IS NULL OR closed_at='' OR
                 {_jday("closed_at")} >= {_jval("?")}
               )
    """
    return con.execute(sql, (end_sql, start_sql)).fetchall()

def find_stamp_issues(con, start_utc: datetime, end_utc: datetime):
    start_sql = to_sqlite_utc(start_utc)
    end_sql = to_sqlite_utc(end_utc)
    sql = f"""
        SELECT id, marketId, selectionId, opened_at
          FROM orders
         WHERE (hedge_of IS NULL OR hedge_of='')
           AND (source IS NULL OR TRIM(source)='')
           AND {_jday("COALESCE(opened_at, '1900-01-01 00:00:00')")} < {_jval("?")}
           AND (
                 closed_at IS NULL OR closed_at='' OR
                 {_jday("closed_at")} >= {_jval("?")}
               )
    """
    return con.execute(sql, (end_sql, start_sql)).fetchall()

def find_finalize_issues(con, start_utc: datetime, end_utc: datetime):
    start_sql = to_sqlite_utc(start_utc)
    end_sql = to_sqlite_utc(end_utc)
    sql = f"""
        SELECT p.id, p.marketId, p.selectionId, p.opened_at,
               EXISTS (
                   SELECT 1 FROM orders c
                    WHERE c.hedge_of = p.id
                      AND UPPER(COALESCE(c.entry_status,''))='MATCHED'
               ) AS has_matched_child
          FROM orders p
         WHERE (p.hedge_of IS NULL OR p.hedge_of='')
           AND (p.closed_at IS NULL OR p.closed_at='')
           AND {_jday("COALESCE(p.opened_at, '1900-01-01 00:00:00')")} < {_jval("?")}
           AND (
                 p.closed_at IS NULL OR p.closed_at='' OR
                 {_jday("p.closed_at")} >= {_jval("?")}
               )
    """
    return con.execute(sql, (end_sql, start_sql)).fetchall()

def decisions_table_present(con) -> bool:
    try:
        r = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='decisions'").fetchone()
        return bool(r)
    except Exception:
        return False

def decisions_columns(con) -> List[str]:
    try:
        rows = con.execute("PRAGMA table_info(decisions)").fetchall()
        return [r["name"] for r in rows]
    except Exception:
        return []

def find_rotation_dups(con, start_utc: datetime, end_utc: datetime) -> List[sqlite3.Row]:
    """
    If 'decisions' table exists, flag duplicate attempts per (minute, mid, sid, letter).
    """
    cols = decisions_columns(con)
    time_col = next((c for c in ("created_at","decided_at","ts") if c in cols), None)
    mid_col  = next((c for c in ("marketId","mid") if c in cols), None)
    sid_col  = next((c for c in ("selectionId","sid") if c in cols), None)
    let_col  = next((c for c in ("letter","source") if c in cols), None)
    if not (time_col and mid_col and sid_col and let_col):
        return []

    start_sql = to_sqlite_utc(start_utc)
    end_sql   = to_sqlite_utc(end_utc)
    ntime = _norm(time_col)
    sql = f"""
        SELECT
          strftime('%Y-%m-%d %H:%M', {ntime}) AS minute,
          {let_col} AS letter,
          {mid_col} AS marketId,
          {sid_col} AS selectionId,
          COUNT(*) AS attempts
        FROM decisions
        WHERE {_jday(time_col)} >= {_jval("?")}
          AND {_jday(time_col)} <  {_jval("?")}
        GROUP BY 1,2,3,4
        HAVING COUNT(*) > 1
        ORDER BY minute, letter, marketId, selectionId
    """
    return con.execute(sql, (start_sql, end_sql)).fetchall()

# ---------- CAP sweep (aware UTC) ----------

def cap_sweep(parents: List[sqlite3.Row], start_utc: datetime, end_utc: datetime, cap: int) -> List[CapViolation]:
    # Group by (mid, sid, letter, mode)
    by_key: Dict[Tuple[str,str,str,str], List[Tuple[datetime,int]]] = {}
    for r in parents:
        mid = str(r["marketId"])
        sid = str(r["selectionId"])
        letter = (r["source"] or "").strip()[:1].upper()
        mode = (r["mode"] or "").strip().upper() or "LIVE"
        t0 = ensure_utc(parse_dt(r["opened_at"]))
        t1 = ensure_utc(parse_dt(r["closed_at"]))
        if not t0:
            # Skip rows without open time
            continue
        # clamp to UTC window
        st = max(t0, start_utc)
        en = min(t1 if t1 else end_utc, end_utc)
        if st >= en:
            continue
        key = (mid, sid, letter or "?", mode)
        by_key.setdefault(key, []).append((st, +1))
        by_key[key].append((en, -1))

    violations: List[CapViolation] = []
    for (mid, sid, letter, mode), events in by_key.items():
        events.sort(key=lambda x: (x[0], x[1]))  # close (-1) before open (+1) at same instant
        cur = 0
        max_open = 0
        at: List[datetime] = []
        for t, d in events:
            cur += d
            if cur > max_open:
                max_open = cur
                at = [t]
            elif cur == max_open and max_open > 0:
                at.append(t)
        if max_open > cap:
            violations.append(CapViolation(
                marketId=mid, selectionId=sid, letter=letter, mode=mode,
                max_open=max_open, cap=cap,
                at_times=[t.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ") for t in sorted(set(at))]
            ))
    return violations

# ---------- Main audit ----------

def run_audit(db_path: str, target_date: Optional[str], cap_value: int) -> AuditResult:
    start_utc, end_utc = utc_day_window(target_date)
    con = connect(db_path)
    schema_missing: Dict[str, List[str]] = {}

    # schema check
    col_state = table_has_columns(con, "orders", REQUIRED_ORDERS_COLS)
    missing = [c for c, ok in col_state.items() if not ok]
    if missing:
        schema_missing["orders"] = missing

    # data pulls
    parents = load_parents_for_window(con, start_utc, end_utc)
    stamps = find_stamp_issues(con, start_utc, end_utc)
    finals = find_finalize_issues(con, start_utc, end_utc)

    # cap sweep
    cap_violations = cap_sweep(parents, start_utc, end_utc, cap_value)

    # rotation/throttle dups (optional)
    rotation_dups: List[RotationDup] = []
    if decisions_table_present(con):
        rows = find_rotation_dups(con, start_utc, end_utc)
        for r in rows:
            minute = str(r["minute"])
            if minute and not minute.endswith("Z"):
                minute = minute + "Z"
            rotation_dups.append(RotationDup(
                minute=minute, letter=str(r["letter"]),
                marketId=str(r["marketId"]), selectionId=str(r["selectionId"]),
                attempts=int(r["attempts"]),
            ))

    # package
    result = AuditResult(
        cap_violations=cap_violations,
        stamp_issues=[
            StampIssue(parent_id=int(r["id"]), marketId=str(r["marketId"]),
                       selectionId=str(r["selectionId"]),
                       opened_at=(str(r["opened_at"]) if r["opened_at"] else None))
            for r in stamps
        ],
        finalize_issues=[
            FinalizeIssue(parent_id=int(r["id"]), marketId=str(r["marketId"]),
                          selectionId=str(r["selectionId"]),
                          opened_at=(str(r["opened_at"]) if r["opened_at"] else None),
                          has_matched_child=bool(r["has_matched_child"]))
            for r in finals if r["has_matched_child"]
        ],
        rotation_dups=rotation_dups,
        schema_missing=schema_missing,
    )

    try:
        con.close()
    except Exception:
        pass

    return result

def print_report(res: AuditResult, db_path: str, start_utc: datetime, end_utc: datetime):
    print("="*78)
    print(f"AutoScalp DB Audit Report | DB: {db_path}")
    print(f"Window (UTC): {iso_z(start_utc)}  →  {iso_z(end_utc)}")
    print("="*78)

    if res.schema_missing:
        print("\n[SCHEMA] Missing required columns:")
        for tbl, cols in res.schema_missing.items():
            print(f"  - {tbl}: {', '.join(cols)}")
    else:
        print("\n[SCHEMA] OK")

    # CAP
    if res.cap_violations:
        print("\n[CAP VIOLATIONS] (max_open > cap)")
        for v in res.cap_violations[:200]:
            times = ', '.join(v.at_times[:5]) + (' ...' if len(v.at_times)>5 else '')
            print(f"  ({v.marketId},{v.selectionId}) letter={v.letter or '?'} mode={v.mode} "
                  f"max_open={v.max_open} > cap={v.cap} @ {times}")
        if len(res.cap_violations) > 200:
            print(f"  ... and {len(res.cap_violations)-200} more")
    else:
        print("\n[CAP] No violations detected.")

    # Stamping
    if res.stamp_issues:
        print("\n[STAMPING] Parents missing 'source' (letter):")
        for s in res.stamp_issues[:200]:
            print(f"  parent_id={s.parent_id} ({s.marketId},{s.selectionId}) opened_at={s.opened_at}")
        if len(res.stamp_issues) > 200:
            print(f"  ... and {len(res.stamp_issues)-200} more")
    else:
        print("\n[STAMPING] All parents have a letter 'source'.")

    # Finalization
    if res.finalize_issues:
        print("\n[FINALIZE] Matched child exists but parent not closed:")
        for f in res.finalize_issues[:200]:
            print(f"  parent_id={f.parent_id} ({f.marketId},{f.selectionId}) opened_at={f.opened_at}")
        if len(res.finalize_issues) > 200:
            print(f"  ... and {len(res.finalize_issues)-200} more")
    else:
        print("\n[FINALIZE] No parents with matched child left open.")

    # Rotation dups
    if res.rotation_dups:
        print("\n[ROTATION] Duplicate attempts per (minute, mid, sid, letter):")
        for d in res.rotation_dups[:200]:
            print(f"  {d.minute} ({d.marketId},{d.selectionId}) letter={d.letter} attempts={d.attempts}")
        if len(res.rotation_dups) > 200:
            print(f"  ... and {len(res.rotation_dups)-200} more")
    else:
        print("\n[ROTATION] No per-minute duplicate attempts found (or no decisions table).")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="Path to autoscalp_gui.db")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (UTC day; default: yesterday in UTC)")
    parser.add_argument("--cap", type=int, default=3, help="CAP value (default 3)")
    parser.add_argument("--dump", default=None, help="Optional path to JSON dump of findings")
    args = parser.parse_args()

    start_utc, end_utc = utc_day_window(args.date)
    res = run_audit(args.db, args.date, args.cap)

    print_report(res, args.db, start_utc, end_utc)

    # exit code priority order
    code = 0
    if res.schema_missing:
        code = max(code, 10)
    if res.cap_violations:
        code = max(code, 2)
    if res.stamp_issues:
        code = max(code, 3)
    if res.finalize_issues:
        code = max(code, 4)
    if res.rotation_dups:
        code = max(code, 5)

    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as f:
            json.dump({
                "schema_missing": res.schema_missing,
                "cap_violations": [asdict(v) for v in res.cap_violations],
                "stamp_issues": [asdict(s) for s in res.stamp_issues],
                "finalize_issues": [asdict(fv) for fv in res.finalize_issues],
                "rotation_dups": [asdict(d) for d in res.rotation_dups],
                "window_utc": {"start": iso_z(start_utc), "end": iso_z(end_utc)},
                "db": args.db,
            }, f, indent=2)
        print(f"\n[WROTE] {args.dump}")

    raise SystemExit(code)

if __name__ == "__main__":
    main()
