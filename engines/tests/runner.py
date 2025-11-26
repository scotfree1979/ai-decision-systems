#!/usr/bin/env python3
"""
engines/tests/runner.py

Phase‑1 test runner for system + race scenarios.

- Uses your real engine modules (DecisionEngine, playbook, pnl, stories/chapters, ladder, volatility).
- In TEST mode, DataProvider injects synthetic data using your real writers.
- In LEARNING/LIVE, provider no‑ops: the same downstream engine code runs on live data.

Public API (import from ScalperView):
    from engines.tests.runner import run_one, run_group, run_all
    res = run_one("system.schema_paths", mode="TEST")

Result object carries ok flag, notes (breadcrumbs), and metrics.
"""

from __future__ import annotations
import importlib
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple, Any


# ── Db path helpers ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DbPaths:
    bets: str
    autoscalp: str


def resolve_db_paths() -> DbPaths:
    """Single source of truth for DB paths (mode-aware)."""
    # engines.config_paths already routes TEST → /data/test/*.test.db
    from engines import config_paths as cp
    return DbPaths(bets=cp.bets_db(), autoscalp=cp.autoscalp_db())


# ── Result object ─────────────────────────────────────────────────────────────

@dataclass
class Result:
    ok: bool
    notes: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def pass_(**metrics: Any) -> "Result":
        return Result(True, ["PASS"], metrics)

    @staticmethod
    def fail(msg: str, **metrics: Any) -> "Result":
        return Result(False, [f"FAIL: {msg}"], metrics)

    def add(self, *lines: str) -> "Result":
        self.notes.extend(lines)
        return self


# ── Dependency bundle (real engine modules) ───────────────────────────────────

@dataclass(frozen=True)
class Deps:
    DecisionEngine: Any
    playbook: Any
    ladder: Any
    pnl: Any
    story_builder: Any
    chapter_builder: Any
    volatility_check: Any


# replace your load_deps() with this
def load_deps() -> Deps:
    """Import real engine modules, but do not blow up if some are unused."""
    DecisionEngine = _lazy_import("engines.decision_engine.engine", "DecisionEngine")

    def _soft(mod, attr=None):
        try:
            m = importlib.import_module(mod)
            return getattr(m, attr) if attr else m
        except Exception:
            return None

    # Keep these soft; our current tests do not require them at import time
    playbook = (
        _soft("engines.playbook")
        or _soft("engines.signal_memory_engine.playbook")
    )
    ladder = (
        _soft("engines.ladder")
        or _soft("engines.signal_memory_engine.ladder")
    )
    pnl = (
        _soft("engines.pnl")
        or _soft("engines.signal_memory_engine.pnl")
    )
    story_builder = (
        _soft("engines.story_builder")
        or _soft("engines.signal_memory_engine.story_builder")
    )
    chapter_builder = (
        _soft("engines.chapter_builder")
        or _soft("engines.signal_memory_engine.chapter_builder")
    )

    volatility_check = _soft("engines.volatility_check")

    return Deps(
        DecisionEngine=DecisionEngine,
        playbook=playbook,
        ladder=ladder,
        pnl=pnl,
        story_builder=story_builder,
        chapter_builder=chapter_builder,
        volatility_check=volatility_check,
    )


def _lazy_import(module: str, attr: Optional[str] = None):
    mod = importlib.import_module(module)
    return getattr(mod, attr) if attr else mod


# ── Data Provider (TEST-only data injection; no-ops elsewhere) ───────────────

class DataProvider:
    """
    Writes *synthetic* data using your real writers when mode == TEST.

    In LEARNING/LIVE we simply no-op (Betfair/live data provides the same updates).
    """

    def __init__(self, mode: str, db: DbPaths):
        self.mode = (mode or "").lower()
        self.db = db

    # ---- market seeding ----
    def seed_markets(self, *, n_markets: int = 1, runners: int = 10) -> Result:
        if self.mode != "test":
            return Result.pass_(note="seed_markets no-op outside TEST")
        from engines.database_hijack_monitor import enqueue_write as write
        from engines.utils.time_utils import now_utc
        import json
        for i in range(n_markets):
            mid = f"1.TEST.MKT.{i+1:03}"
            start = now_utc().isoformat()
            for j in range(runners):
                sid = 100000 + i * 1000 + j
                name = f"Runner {j+1}"
                write(
                    "INSERT OR IGNORE INTO bets (marketId, selectionId, horse_name, event_name, market_name, "
                    "race_name, marketStartTime, date, timestamp, meta_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [mid, sid, name, "TEST", "WIN", f"TEST RACE {i+1}", start, start.split("T")[0], start,
                     json.dumps({"src": "test-seed"})],
                )
        return Result.pass_(markets=n_markets, runners=runners)

    # ---- odds/bands path ----
    def oc_band_samples(self, market_id: str, selection_id: int | str, oc: int, samples: List[float]) -> Result:
        if self.mode != "test":
            return Result.pass_(note="oc_band_samples no-op outside TEST")
        from engines.database_hijack_monitor import enqueue_write as write
        from engines.utils.time_utils import now_utc
        import json
        # ensure row exists
        write(
            "INSERT INTO inbound_oc_cache (marketId, selectionId, last_sync_ts) "
            "SELECT ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM inbound_oc_cache WHERE marketId=? AND selectionId=?)",
            [market_id, str(selection_id), now_utc().isoformat(), market_id, str(selection_id)],
        )
        # write current oc value + band json
        label = f"oc{oc}"
        write(
            f"UPDATE inbound_oc_cache SET {label}=?, {label}_band_json=?, last_sync_ts=? "
            "WHERE marketId=? AND selectionId=?",
            [samples[-1], json.dumps(samples), now_utc().isoformat(), market_id, str(selection_id)],
        )
        return Result.pass_(marketId=market_id, selectionId=str(selection_id), oc=oc, n=len(samples))

    def set_anchor(self, market_id: str, selection_id: int | str, odd: float) -> Result:
        if self.mode != "test":
            return Result.pass_(note="set_anchor no-op outside TEST")
        from engines.database_hijack_monitor import enqueue_write as write
        from engines.utils.time_utils import now_utc
        write(
            "INSERT INTO inbound_oc_cache (marketId, selectionId, anchor_odd, last_sync_ts) "
            "SELECT ?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM inbound_oc_cache WHERE marketId=? AND selectionId=?)",
            [market_id, str(selection_id), odd, now_utc().isoformat(), market_id, str(selection_id)],
        )
        write(
            "UPDATE inbound_oc_cache SET anchor_odd=?, last_sync_ts=? WHERE marketId=? AND selectionId=?",
            [odd, now_utc().isoformat(), market_id, str(selection_id)],
        )
        return Result.pass_(anchor=odd)

    # ---- order fill helpers ----
    def instant_fill(self, order_id: int, pnl_delta: float | None = None) -> Result:
        if self.mode != "test":
            return Result.pass_(note="instant_fill no-op outside TEST")
        import sqlite3
        from engines.utils.time_utils import now_utc
        con = sqlite3.connect(self.db.autoscalp, timeout=8)
        con.row_factory = sqlite3.Row
        try:
            cols = [r["name"] for r in con.execute("PRAGMA table_info(orders)").fetchall()]
            ts = now_utc().isoformat()
            delta = pnl_delta or 0.0
            if "realized_pnl" in cols:
                con.execute(
                    "UPDATE orders SET entry_status='matched', closed_at=?, realized_pnl=COALESCE(realized_pnl,0)+? WHERE id=?",
                    (ts, delta, order_id),
                )
            else:
                con.execute("UPDATE orders SET entry_status='matched', closed_at=? WHERE id=?", (ts, order_id))
            con.commit()
        finally:
            try: con.close()
            except Exception: pass
        return Result.pass_(order_id=order_id, matched=True)


# ── REGISTRY (inline; imports every test module) ──────────────────────────────
# System tests
from engines.tests.system import schema_paths
from engines.tests.system import oc_timeline
from engines.tests.system import order_lifecycle
from engines.tests.system import pnl_rollup
from engines.tests.system import decision_narrative

# Race/decision tests
from engines.tests.race import two_chapter_trigger
from engines.tests.race import scalp_opportunity
from engines.tests.race import no_trade_zone
from engines.tests.race import late_steam
from engines.tests.race import early_drift
from engines.tests.race import incomplete_bands
from engines.tests.race import overlapping_races
from engines.tests.race import volatility_spike
from engines.tests.race import liquidity_gate
from engines.tests.race import confidence_thresholds
from engines.tests.race import invalid_market
from engines.tests.race import greenup_exit

REGISTRY: Dict[str, Callable[[DbPaths, DataProvider, Deps], Result]] = {
    # system
    "system.schema_paths":       schema_paths.run,
    "system.oc_timeline":   oc_timeline.run,
    "system.order_lifecycle":    order_lifecycle.run,
    "system.pnl_rollup":         pnl_rollup.run,
    "system.decision_narrative": decision_narrative.run,
    # race
    "race.two_chapter_trigger":  two_chapter_trigger.run,
    "race.scalp_opportunity":    scalp_opportunity.run,
    "race.no_trade_zone":        no_trade_zone.run,
    "race.late_steam":           late_steam.run,
    "race.early_drift":          early_drift.run,
    "race.incomplete_bands":     incomplete_bands.run,
    "race.overlapping_races":    overlapping_races.run,
    "race.volatility_spike":     volatility_spike.run,
    "race.liquidity_gate":       liquidity_gate.run,
    "race.confidence_thresholds":confidence_thresholds.run,
    "race.invalid_market":       invalid_market.run,
    "race.greenup_exit":         greenup_exit.run,
}


# ── Runner API ────────────────────────────────────────────────────────────────

def _mode_from_env() -> str:
    try:
        from engines.upgrade_import_patch import get_mode  # type: ignore
        return (get_mode() or "learning").lower()
    except Exception:
        return (os.environ.get("AUTOSCALP_MODE") or "learning").lower()


def run_one(name: str, *, mode: Optional[str] = None) -> Result:
    """Run a single named test via the registry."""
    fn = REGISTRY.get(name)
    if not fn:
        return Result.fail(f"unknown test: {name}")
    db = resolve_db_paths()
    deps = load_deps()
    provide = DataProvider(mode or _mode_from_env(), db)
    try:
        res = fn(db, provide, deps)
        res.notes.insert(0, f"TEST {name}")
        return res
    except Exception as e:
        return Result.fail(f"{name}: {e}")


def run_group(prefix: str, *, mode: Optional[str] = None) -> Result:
    """Run all tests whose name starts with prefix (e.g., 'system.' or 'race.')."""
    names = [k for k in REGISTRY.keys() if k.startswith(prefix)]
    if not names:
        return Result.fail(f"no tests with prefix '{prefix}'")
    agg = Result(ok=True, notes=[f"GROUP {prefix}"], metrics={"passed": 0, "failed": 0})
    for n in names:
        r = run_one(n, mode=mode)
        if r.ok:
            agg.metrics["passed"] += 1
        else:
            agg.metrics["failed"] += 1
        agg.notes.extend([f"- {n}:"] + [f"  {line}" for line in r.notes])
    agg.ok = agg.metrics["failed"] == 0
    return agg


def run_all(*, mode: Optional[str] = None) -> Result:
    """Run every registered test in REGISTRY."""
    agg = Result(ok=True, notes=["RUN ALL"], metrics={"passed": 0, "failed": 0})
    for n in list(REGISTRY.keys()):
        r = run_one(n, mode=mode)
        if r.ok:
            agg.metrics["passed"] += 1
        else:
            agg.metrics["failed"] += 1
        agg.notes.extend([f"- {n}:"] + [f"  {line}" for line in r.notes])
    agg.ok = agg.metrics["failed"] == 0
    return agg
