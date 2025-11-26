"""
AutoScalping Sprint Canvas — Drop-in Python Module
==================================================
Paste this file into your repo (e.g., engines/sprint/autoscalp_sprint_canvas.py).
You can run it as a helper CLI or just keep it as a living checklist in-code.

How to use (in any tab):
- Paste this canvas/file, save, commit. 
- In chat say: "Continue AutoScalping Sprint – pick up from where we left off." 
- Update statuses by editing TASKS or running with CLI flags below.

Ground rules (agreed in chat):
- GUI **may place bets** by calling the existing router: signal_memory_engine.bet_router.fire_initial_scalp
- Data-plane stays as-is; GUI reads from BETS DB, **writes only** to a new GUI-owned DB.
- "Memory" is nuked. All state is DB-first. If GUI needs any extra scratch, add it to GUI DB.
- Simulation vs Live: identical decision path; only the **bet placement side-effect** changes.
- Blueprints: GUI Start → ensures today's blueprints exist (or builds them) before threads.
- No writes to BETS DB (except optional test hook you can enable later).

CLI (optional):
  python autoscalp_sprint_canvas.py --list
  python autoscalp_sprint_canvas.py --next
  python autoscalp_sprint_canvas.py --done T1.1
  python autoscalp_sprint_canvas.py --print-ddl
"""
from __future__ import annotations
import json, sys, argparse, textwrap, datetime as _dt

SPRINT_NAME = "AutoScalping v1 — GUI-Orchestrated Betting (DB-first)"
GOAL = (
    "Run existing data threads as-is; GUI orchestrates decisions, places scalps via bet_router; "
    "all GUI state, actions, stories/chapters, learning, and rollups live in a new GUI DB."
)

# ----------------------------------------------------------------------------
# Modes / Flags
# ----------------------------------------------------------------------------
class Mode:
    SIM = "sim"
    LIVE = "live"

DEFAULT_MODE = Mode.SIM  # ✅ start in sim; same path, no external API payloads
WRITE_BETS_TEST_MODE = False  # ⚠ off by default; if True, stamp BETS.bets.test_mode ('sim_placed'|'live')

# ----------------------------------------------------------------------------
# GUI DB — canonical schema (SQLite) with WAL + rollups (Poker-style)
# ----------------------------------------------------------------------------
# File name is up to you; keep it next to BETS DB or under ./data/
GUI_DB_FILENAME = "autoscalp_gui.db"

DDL = {
    # Session & configuration
    "config": """
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """,
    "runs": """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            mode TEXT NOT NULL CHECK (mode in ('sim','live')),
            session_token_hash TEXT,
            blueprint_file TEXT,
            notes TEXT
        );
    """,
    # Inbound mirrors (derived from BETS DB) — our *read-only* cache for GUI logic
    "inbound_markets": """
        CREATE TABLE IF NOT EXISTS inbound_markets (
            market_id TEXT PRIMARY KEY,
            venue TEXT,
            race_name TEXT,
            market_start TEXT
        );
    """,
    "inbound_runners": """
        CREATE TABLE IF NOT EXISTS inbound_runners (
            market_id TEXT,
            selection_id TEXT,
            runner_name TEXT,
            anchor_odd REAL,
            oc1 REAL, oc2 REAL, oc3 REAL, oc4 REAL, oc5 REAL, oc6 REAL,
            PRIMARY KEY (market_id, selection_id)
        );
    """,
    # Story/Chapter model (ours; **not** runner_ram_snapshots). Built from inbound_* + BETS reads.
    "stories": """
        CREATE TABLE IF NOT EXISTS stories (
            market_id TEXT,
            selection_id TEXT,
            story_id TEXT,
            created_at TEXT,
            PRIMARY KEY (market_id, selection_id)
        );
    """,
    "chapters": """
        CREATE TABLE IF NOT EXISTS chapters (
            market_id TEXT,
            selection_id TEXT,
            oc_label TEXT,            -- OC0..OC6..OCn
            oc_band_min REAL,
            oc_band_max REAL,
            entry_odds REAL,
            exit_odds REAL,
            tick_pattern TEXT,        -- drifted|steamed|volatile|pingpong|flat
            direction_bias TEXT,      -- drifted|steamed|flat
            volatility REAL,
            position_ratio REAL,
            completed_at TEXT,
            PRIMARY KEY (market_id, selection_id, oc_label)
        );
    """,
    # Decisions & executions (GUI-owned, authoritative record of what we did)
    "decisions": """
        CREATE TABLE IF NOT EXISTS decisions (
            decision_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            market_id TEXT NOT NULL,
            selection_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            signal_type TEXT,         -- exploratory|partial_blueprint|full_blueprint|oc_trigger|autonomous_scan
            blueprint_match TEXT,
            confidence REAL,
            scalp_direction TEXT,     -- lay_to_back|back_to_lay
            odds REAL,
            stake REAL,
            reasons_json TEXT         -- free-form audit trail
        );
    """,
    "orders": """
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            decision_id TEXT NOT NULL,
            side TEXT NOT NULL CHECK (side in ('BACK','LAY')),
            odds REAL NOT NULL,
            stake REAL NOT NULL,
            betfair_bet_id TEXT,      -- if we actually hit API
            placed_at TEXT,
            status TEXT,              -- queued|placed|matched|cancelled|failed
            notes TEXT
        );
    """,
    "ladders": """
        CREATE TABLE IF NOT EXISTS ladders (
            decision_id TEXT,
            rung INTEGER,
            side TEXT,
            odds REAL,
            stake REAL,
            status TEXT,
            timestamp TEXT,
            PRIMARY KEY (decision_id, rung)
        );
    """,
    # P&L rollups (fast queries for dashboard) — Poker-style aggregates
    "pnl_runner_daily": """
        CREATE TABLE IF NOT EXISTS pnl_runner_daily (
            ymd TEXT,
            market_id TEXT,
            selection_id TEXT,
            trades INTEGER,
            pnl REAL,
            PRIMARY KEY (ymd, market_id, selection_id)
        );
    """,
    "pnl_pattern_daily": """
        CREATE TABLE IF NOT EXISTS pnl_pattern_daily (
            ymd TEXT,
            pattern_key TEXT,
            trades INTEGER,
            pnl REAL,
            win_rate REAL,
            PRIMARY KEY (ymd, pattern_key)
        );
    """,
    "pnl_market_daily": """
        CREATE TABLE IF NOT EXISTS pnl_market_daily (
            ymd TEXT,
            market_id TEXT,
            trades INTEGER,
            pnl REAL,
            PRIMARY KEY (ymd, market_id)
        );
    """,
    # Event bus (for GUI widgets & audit)
    "events": """
        CREATE TABLE IF NOT EXISTS events (
            ts TEXT,
            level TEXT,
            area TEXT,
            message TEXT
        );
    """,
    # Indices (important when it gets big)
    "idx": """
        CREATE INDEX IF NOT EXISTS idx_decisions_market ON decisions(market_id, selection_id);
        CREATE INDEX IF NOT EXISTS idx_orders_decision ON orders(decision_id);
        CREATE INDEX IF NOT EXISTS idx_chapters_key ON chapters(market_id, selection_id, oc_label);
        CREATE INDEX IF NOT EXISTS idx_events_time ON events(ts);
    """,
    # PRAGMAs
    "pragma": """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        PRAGMA temp_store=MEMORY;
        PRAGMA mmap_size=134217728; -- 128MB
    """,
}

# ----------------------------------------------------------------------------
# Sprint structure (master + mini-sprints)
# Use [ ] and [x] purely as comments; canonical state lives in TASKS below.
# ----------------------------------------------------------------------------
TASKS = [
    {
        "id": "T0",
        "title": "Bootstrap GUI DB (create + PRAGMA + indices)",
        "status": "todo",
        "owner": "GUI",
        "deps": [],
        "notes": "No writes to BETS DB. This must run on first launch before anything else.",
    },
    {
        "id": "T1",
        "title": "Wire StartupView → Start Engine (Auto=Yes) and Run Blueprints",
        "status": "todo",
        "owner": "GUI",
        "deps": ["T0"],
        "notes": "On START ENGINE: (a) start all existing threads exactly as before; (b) ensure_blueprints_current(); (c) log run in GUI DB.",
    },
    {
        "id": "T1.1",
        "title": "Blueprint button: invoke ensure_blueprints_current() + run_blueprint_export()",
        "status": "todo",
        "owner": "GUI",
        "deps": ["T1"],
        "notes": "Show toast + event row; status pill on Dashboard.",
    },
    {
        "id": "T2",
        "title": "Read-model ETL: pull minimal fields from BETS.bets → inbound_*",
        "status": "todo",
        "owner": "GUI",
        "deps": ["T0"],
        "notes": "We do **not** read runner_ram_snapshots directly. We derive our own stories/chapters from BETS and keep a stable cache in GUI DB.",
    },
    {
        "id": "T3",
        "title": "StoryBuilder: create chapters per OC band (OC0..OC6..) in GUI DB",
        "status": "todo",
        "owner": "GUI",
        "deps": ["T2"],
        "notes": "Classify tick_pattern, direction_bias; mirror poker rollup approach.",
    },
    {
        "id": "T4",
        "title": "Decision engine in GUI: evaluate_matching_opportunity() wrapper",
        "status": "todo",
        "owner": "GUI",
        "deps": ["T3"],
        "notes": "Read story/chapters from GUI DB; no RAM reads; build decision dict; write to decisions table.",
    },
    {
        "id": "T5",
        "title": "Execution: SIM path (no API); LIVE path via bet_router.fire_initial_scalp",
        "status": "todo",
        "owner": "GUI",
        "deps": ["T4"],
        "notes": "On SIM: write orders with status=queued/placed, no external call. On LIVE: call bet_router and record betfair_bet_id.",
    },
    {
        "id": "T6",
        "title": "Ladders: reflect rung_log into ladders table (read from BETS only)",
        "status": "todo",
        "owner": "GUI",
        "deps": ["T5"],
        "notes": "We do not drive ladders from GUI; just observe and mirror into GUI DB for analytics.",
    },
    {
        "id": "T7",
        "title": "Dashboard widgets wired to GUI DB (readiness, counts, P&L panels)",
        "status": "todo",
        "owner": "GUI",
        "deps": ["T5"],
        "notes": "Budget/risk panels computed from GUI DB decisions + orders + pnl_* tables.",
    },
    {
        "id": "T8",
        "title": "Daily rollups (runner, pattern, market) — Poker-style",
        "status": "todo",
        "owner": "GUI",
        "deps": ["T5"],
        "notes": "Batch every N minutes or at day end; incremental idempotent upserts.",
    },
    {
        "id": "T9",
        "title": "Sim vs Live stamp control",
        "status": "todo",
        "owner": "GUI",
        "deps": ["T5"],
        "notes": "Store mode in runs + orders; optional BETS.bets.test_mode stamping behind feature flag.",
    },
    {
        "id": "T10",
        "title": "Blueprint learning loop: use playbooks & history to adjust confidence/stake",
        "status": "todo",
        "owner": "GUI",
        "deps": ["T8"],
        "notes": "No new models. Use existing tables and rollups to bias confidence and stake.",
    },
    {
        "id": "T11",
        "title": "Go/No-Go gate: readiness checklist + dry-run harness",
        "status": "todo",
        "owner": "GUI",
        "deps": ["T7","T8","T10"],
        "notes": "Gate flips to 'ready to go live' when thresholds met (win rate/pattern PnL, etc.).",
    },
]

# ----------------------------------------------------------------------------
# Helpers to pretty print / tick tasks
# ----------------------------------------------------------------------------
SYMBOL = {"todo": "[ ]", "done": "[x]", "blocked": "[!]"}

def _fmt_task(t: dict) -> str:
    head = f"{SYMBOL.get(t['status'],'[ ]')} {t['id']}: {t['title']}"
    body = textwrap.indent((t.get("notes") or "").strip(), prefix="    ")
    deps = ", ".join(t.get("deps") or [])
    meta = f"    owner={t.get('owner')} deps=[{deps}]"
    return "\n".join([head, meta, body])

def list_tasks():
    print(f"\nSprint: {SPRINT_NAME}\nGoal  : {GOAL}\n")
    for t in TASKS:
        print(_fmt_task(t))
        print()

def next_task_id() -> str|None:
    for t in TASKS:
        if t["status"] == "todo":
            return t["id"]
    return None

def mark_done(task_id: str) -> bool:
    for t in TASKS:
        if t["id"].lower() == task_id.lower():
            t["status"] = "done"
            return True
    return False

# ----------------------------------------------------------------------------
# Integration stubs the GUI will call (no new memory state; DB-first only)
# ----------------------------------------------------------------------------
class BlueprintHooks:
    """Stubs to be bound to your real implementations in scalper_module.py."""
    @staticmethod
    def ensure_blueprints_current() -> str:
        """Return today's blueprint filename after ensuring freshness."""
        # GUI should import the real function from scalper_module and call it.
        return f"blueprint_signals_{_dt.date.today().isoformat()}.json"

    @staticmethod
    def run_blueprint_export() -> None:
        """Run the exporter directly (used by the Blueprint button)."""
        pass

class BetRouter:
    """Minimal façade — GUI calls this; you bind to signal_memory_engine.bet_router.fire_initial_scalp."""
    @staticmethod
    def fire_initial_scalp(signal: dict, customer_order_ref: str):
        raise NotImplementedError("Bind BetRouter.fire_initial_scalp to the real router before LIVE mode.")

# ----------------------------------------------------------------------------
# DDL printer for quick copy/paste
# ----------------------------------------------------------------------------
def print_ddl():
    order = ["pragma","config","runs","inbound_markets","inbound_runners","stories","chapters",
             "decisions","orders","ladders","pnl_runner_daily","pnl_pattern_daily","pnl_market_daily",
             "events","idx"]
    for k in order:
        print("\n-- ", k)
        print(DDL[k].strip())

# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--next", action="store_true")
    ap.add_argument("--done", metavar="TASK_ID")
    ap.add_argument("--print-ddl", action="store_true")
    args = ap.parse_args()

    if args.list:
        list_tasks()
        sys.exit(0)

    if args.next:
        nid = next_task_id()
        print(nid or "<none>")
        sys.exit(0)

    if args.done:
        ok = mark_done(args.done)
        print("OK" if ok else "NOT-FOUND")
        if ok:
            list_tasks()
        sys.exit(0)

    if args.print_ddl:
        print_ddl()
        sys.exit(0)

    # Default: print sprint header
    list_tasks()
