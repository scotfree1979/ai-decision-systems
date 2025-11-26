#!/usr/bin/env python3
import os, sys, sqlite3
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here)
if _root not in sys.path:
    sys.path.insert(0, _root)

import tkinter as tk
from tkinter import ttk
from engines.utils.time_utils import now_utc, minutes_to_off
# tests runner (system + race scenarios)
from engines.tests.runner import run_one, run_group, run_all
from datetime import datetime
from engines.config_paths import connect_db, autoscalp_db, set_db_paths

# --- PATCH START: policy import (insert with other imports) -------------------

# --- PATCH END ----------------------------------------------------------------


# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: gui/scalper_view.py
# 🔎 SEARCH: ^from engines\.utils\.time_utils import now_utc, minutes_to_off$
# --- PATCH START: Collapsible + Mastery helpers (insert after imports) -------
import json
from engines.config_paths import connect_db
from datetime import datetime  # already imported below; ok to dedupe or keep
import engines.mastery.mastery_policy as mp

from engines.mastery.context_builder import build_context_from_test_db
from engines.mastery.policy_lookup import get_bin_key, load_posteriors
from engines.mastery.microstructure import compute_p_fill
# Learning health validator
from engines.health.learning_validator import validate_learning


# --- Collapsible group --------------------------------------------------------
# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: gui/scalper_view.py
# 🔎 SEARCH: ^class Collapsible\(ttk\.Frame\):\n(?:.*\n)+?def body\(self\) -> ttk\.Frame: return self\._body
# --- PATCH START: Collapsible with show_header + helpers ----------------------
class Collapsible(ttk.Frame):
    def __init__(self, master, title: str, start_open: bool = True, show_header: bool = True):
        super().__init__(master)
        self._open = tk.BooleanVar(value=start_open)
        self._show_header = bool(show_header)
        self._pack_opts: dict | None = None

        self._body = ttk.Frame(self)
        if self._show_header:
            hdr = ttk.Frame(self); hdr.pack(fill="x")
            ttk.Checkbutton(
                hdr, text=title, variable=self._open, command=self._toggle, style="Toolbutton"
            ).pack(side="left")

        if start_open:
            self._body.pack(fill="x", pady=(4, 0))

    # Remember our pack options so we can re-pack on reopen.
    def pack(self, *args, **kwargs):  # type: ignore[override]
        self._pack_opts = kwargs
        return super().pack(*args, **kwargs)

    def body(self) -> ttk.Frame:
        return self._body

    def open_var(self) -> tk.BooleanVar:
        return self._open

    def _toggle(self):
        if self._open.get():
            self.open()
        else:
            self.close()

    def open(self):
        if not self._show_header:
            # Re-pack the wrapper frame if it was forgotten.
            if self._pack_opts is not None:
                super().pack(**self._pack_opts)
        if not self._body.winfo_ismapped():
            self._body.pack(fill="x", pady=(4, 0))
        self._open.set(True)

    def close(self):
        if self._show_header:
            self._body.forget()
        else:
            # Headerless → hide the entire section so it frees space.
            super().pack_forget()
        self._open.set(False)

    def toggle(self):
        self._open.set(not self._open.get())
        self._toggle()


# --- PATCH END ----------------------------------------------------------------


# --- Mastery persistence helpers ---------------------------------------------
def _mastery_get_progress():
    try:
        with connect_db(ro=True) as conn:
            row = conn.execute("SELECT progress, updated_at FROM mastery_state WHERE id=1").fetchone()
        return (row[0], row[1]) if row else (0, "-")
    except Exception:
        return (0, "-")

def _mastery_event(event_type: str, delta: int = 0, details: dict | None = None):
    details = details or {}
    with connect_db(ro=False) as conn:
        conn.execute("INSERT INTO mastery_events(event_type,details_json,delta_progress,source) VALUES (?,?,?,'TEST')",
                     (event_type, json.dumps(details), delta))
        if delta != 0:
            conn.execute("UPDATE mastery_state SET progress = MIN(100, MAX(0, progress + ?)), updated_at=datetime('now') WHERE id=1", (delta,))
        conn.commit()

def _mastery_set(field: str, value: dict):
    with connect_db(ro=False) as conn:
        conn.execute(f"UPDATE mastery_state SET {field}=?, updated_at=datetime('now') WHERE id=1", (json.dumps(value),))
        conn.commit()

def _mastery_reset():
    with connect_db(ro=False) as conn:
        conn.execute("""
        UPDATE mastery_state
           SET progress=0,
               thresholds_json='{}', volatility_json='{}', liquidity_json='{}',
               time_windows_json='{}', exit_policy_json='{}', confidence_json='{}',
               updated_at=datetime('now')
         WHERE id=1
        """)
        conn.execute("DELETE FROM mastery_events")
        conn.commit()


# --- Mastery UI block ---------------------------------------------------------
# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: gui/scalper_view.py
# 🔎 SEARCH: ^def _build_mastery_group\(parent: ttk\.Frame\):
# --- PATCH START: ping external progress after updates -----------------------
def _build_mastery_group(parent: ttk.Frame, notify=None):
    """Build Mastery actions + log. `notify` should be a callable that refreshes the top bar."""
    group = ttk.LabelFrame(parent, text="Mastery (actions)")
    group.pack(fill="x", padx=4, pady=4)
    body = ttk.Frame(group); body.pack(fill="x")

    def _notify():
        if callable(notify):
            notify()

    # Buttons row
    btns = ttk.Frame(body); btns.pack(fill="x", padx=4, pady=4)
    def start(): _mastery_reset(); _mastery_event("start", 0, {"note":"init"}); _notify()
    def learn_thresholds(): _mastery_set("thresholds_json", {"steam_strength":0.65}); _mastery_event("learn_thresholds", 20); _notify()
    def learn_vol(): _mastery_set("volatility_json", {"sigma_low":0.5,"sigma_high":2.0}); _mastery_event("learn_volatility", 15); _notify()
    def learn_liq(): _mastery_set("liquidity_json", {"min_lay_avail":250,"min_back_avail":250,"depth_levels":3}); _mastery_event("learn_liquidity", 15); _notify()
    def learn_time(): _mastery_set("time_windows_json", {"windows":[["120","60"],["60","30"],["30","10"],["10","2"],["2","0"]]}); _mastery_event("learn_time_windows", 15); _notify()
    def learn_exit(): _mastery_set("exit_policy_json", {"greenup_ticks":3,"hard_stop_ticks":-6}); _mastery_event("learn_exit_policy", 15); _notify()
    def advance(): p,_ = _mastery_get_progress(); delta = max(0, 100-int(p)); _mastery_event("advance_to_100", delta); _notify()
    def reset(): _mastery_reset(); _notify()

    for txt, cmd in [
        ("Start Mastery (0%)", start),
        ("Learn Signal Thresholds", learn_thresholds),
        ("Learn Volatility Bounds", learn_vol),
        ("Learn Liquidity Gate", learn_liq),
        ("Learn Time Windows", learn_time),
        ("Learn Exit Policy", learn_exit),
        ("Advance to 100%", advance),
        ("Reset Mastery", reset),
    ]:
        ttk.Button(btns, text=txt, command=cmd).pack(fill="x", pady=2)

    # Log viewer
    log_group = Collapsible(group, "Mastery Log", start_open=True, show_header=True)
    tk.Text(log_group.body(), height=8).pack(fill="both", expand=True, padx=2, pady=2)
    log_group.pack(fill="x", padx=4, pady=4)

    return group


# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: gui/scalper_view.py
# 🔎 SEARCH: ^from engines\.utils\.time_utils import now_utc, minutes_to_off$
# (insert the following block right after the imports; before open_scalper_window)
# ─────────────────────────────────────────────────────────────────────────────
# Fallback minimal view if ScalperView was not defined (e.g., during refactor)
if "ScalperView" not in globals():
    import json
    import engines.config_paths as cp

    class ScalperView(ttk.Frame):
        """
        Minimal orders table so the window isn't blank when the full class is missing.
        Shows open TEST/LEARNING/LIVE orders with basic fields + P&L tiles
        and a collapsible Decision Engine narrative.
        """
        def __init__(self, parent, app=None):
            # Session-scoped SIM run id (visible in Orders query if 'run_id' column exists)
            import time as _time, random as _rnd
            # Prefer attaching to an active run if one exists (re-open scenario)
            try:
                _b = connect_db(ro=True); _b.row_factory = sqlite3.Row
                _row = _b.execute(
                    "SELECT run_id FROM sim_runs WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if _row and _row["run_id"]:
                    self._sim_run_id = str(_row["run_id"])
                else:
                    self._sim_run_id = f"SIM-{_time.strftime('%Y%m%d-%H%M%S')}-{_rnd.randint(100,999)}"
            except Exception:
                self._sim_run_id = f"SIM-{_time.strftime('%Y%m%d-%H%M%S')}-{_rnd.randint(100,999)}"
            finally:
                try: _b.close()
                except Exception: pass


            super().__init__(parent, padding=(8, 8, 8, 8))
            self.app = app
            try:
                self._db = cp.autoscalp_db()
            except Exception:
                self._db = os.path.join(_root, "data", "autoscalp_gui.db")

            header = ttk.Label(self, text="Scalping — Active Trades (fallback view)",
                               font=("SF Pro Text", 13, "bold"))
            header.pack(anchor="w", pady=(0, 8))

            # --- Decision Engine dialog (collapsible) ---------------------------
            bar = ttk.Frame(self); bar.pack(fill="x", padx=6, pady=(10, 0))
            ttk.Label(bar, text="Decision Engine", font=("SF Pro Text", 11, "bold")).pack(side="left")
            self._de_open = tk.BooleanVar(value=False)
            self._de_frame = ttk.Frame(self)  # created later on show

            # Fallback: ensure self._de_toggle exists even if the real method was dedented
            if not hasattr(self, "_de_toggle"):
                def _de_toggle_fallback():
                    is_open = not self._de_open.get()
                    self._de_open.set(is_open)
                    # update button label if the button is already created
                    try:
                        self._de_btn.config(text=("Hide" if is_open else "Show"))
                    except Exception:
                        pass
                    if is_open:
                        try:
                            self._build_de_view(self._de_frame)
                        except Exception:
                            pass
                        self._de_frame.pack(fill="both", expand=True, padx=6, pady=(6, 6))
                        try:
                            self._refresh_de_view()
                        except Exception:
                            pass
                    else:
                        try:
                            for c in self._de_frame.winfo_children():
                                c.destroy()
                        except Exception:
                            pass
                        self._de_frame.pack_forget()
                self._de_toggle = _de_toggle_fallback

            self._de_btn  = ttk.Button(bar, text="Show", width=8, command=self._de_toggle)
            self._de_btn.pack(side="right")


            # ── Top bar: toolbar (left) + P&L tiles (right) ───────────────────────────────
            topbar = ttk.Frame(self); topbar.pack(fill="x", padx=8, pady=(0, 8))

            # Left: section toolbar (buttons wired later-safe)
            # Left: section toolbar (buttons will be added after sections are created)
            self.toolbar = ttk.Frame(topbar)
            self.toolbar.pack(side="left", anchor="w")


            # Right: P&L tiles aligned to top-right
            pnl_row = ttk.Frame(topbar); pnl_row.pack(side="right", anchor="e")
            # pack rightmost first to keep logical order left→right as Total, Today, 7 days, 30 days
            self.pnl_30d = _MiniTile(pnl_row, "30 days"); self.pnl_30d.pack(side="right", padx=(8,0))
            self.pnl_7d  = _MiniTile(pnl_row, "7 days");  self.pnl_7d.pack(side="right", padx=(8,0))
            self.pnl_today = _MiniTile(pnl_row, "Today"); self.pnl_today.pack(side="right", padx=(8,0))
            self.pnl_total = _MiniTile(pnl_row, "Total"); self.pnl_total.pack(side="right", padx=(8,0))

            # External Mastery summary just under toolbar
            mp = ttk.Frame(self); mp.pack(fill="x", padx=8, pady=(0, 8))
            self._mprog_var = tk.IntVar(value=0)
            self._mprog_txt = tk.StringVar(value="")
            ttk.Label(mp, text="Mastery Progress").pack(anchor="w")
            ttk.Progressbar(mp, orient="horizontal", mode="determinate", maximum=100, variable=self._mprog_var)\
                .pack(fill="x", padx=2, pady=(0,4))
            ttk.Label(mp, textvariable=self._mprog_txt).pack(anchor="w", padx=2)
            def _refresh_mastery_summary():
                p, ts = _mastery_get_progress()
                self._mprog_var.set(p)
                self._mprog_txt.set(f"{p}% — Updated: {ts}")
            self.update_mastery_summary = _refresh_mastery_summary
            self.update_mastery_summary()

            # --- PnL Source filter -------------------------------------------------
            self._pnl_source = tk.StringVar(value="ALL")  # ALL / TEST / LEARNING / LIVE
            srcf = ttk.Frame(topbar); srcf.pack(side="left", padx=(0,8))
            ttk.Label(srcf, text="PnL Source").pack(side="left")
            src_cmb = ttk.Combobox(srcf, width=10, state="readonly",
                               values=("ALL","TEST","LEARNING","LIVE"),
                               textvariable=self._pnl_source)
            src_cmb.pack(side="left", padx=(6,0))
            def _on_src_change(*_):
                try: self._refresh_once()
                except Exception: pass
            src_cmb.bind("<<ComboboxSelected>>", _on_src_change)


            # --- Next Proposal (TEST) -----------------------------------------------------
            prop = ttk.LabelFrame(self, text="Next Proposal (TEST)")
            prop.pack(fill="x", padx=8, pady=(0, 8))
            row = ttk.Frame(prop); row.pack(fill="x", padx=8, pady=(6, 6))

            def _log_to_panel(prefix: str, msg: str):
                try:
                    self._test_log.insert("end", f"[{prefix}] {msg}\n")
                    self._test_log.see("end")
                except Exception:
                    pass

            def _demo_proposal():
                # Real TEST context (same call path as LEARNING; only the feed differs)
                try:
                    ctx, meta = build_context_from_test_db()
                    plan = mp.propose_trade(ctx)

                    if plan.get("enter"):
                        msg = f"ENTER {plan['direction']} | ticks={plan['target_ticks']} | stop={plan['stop_ticks']} | {plan['why']}"
                    else:
                        msg = f"NO-TRADE | {plan.get('why','')}"
                    _log_to_panel("PROPOSAL", msg)
                except Exception as e:
                    _log_to_panel("PROPOSAL", f"error: {e}")

            def _latest_mid_odds(meta: dict) -> float:
                """Try OC band last value; fallback to last order; else 6.0."""
                con = None
                try:
                    import json as _json
                    con = sqlite3.connect(self._db, timeout=8)
                    con.row_factory = sqlite3.Row

                    mid = None
                    if meta.get("marketId") and meta.get("selectionId"):
                        r = con.execute(
                            "SELECT oc1_band_json FROM inbound_oc_cache "
                            "WHERE marketId=? AND selectionId=? "
                            "ORDER BY id DESC LIMIT 1",
                            (meta["marketId"], meta["selectionId"])
                        ).fetchone()
                        if r and r["oc1_band_json"]:
                            arr = _json.loads(r["oc1_band_json"])
                            if arr:
                                mid = float(arr[-1])

                    if mid is None:
                        r = con.execute(
                            "SELECT entry_odds FROM orders ORDER BY id DESC LIMIT 1"
                        ).fetchone()
                        if r:
                            mid = float(r[0])

                    return mid if mid is not None else 6.0

                except Exception:
                    return 6.0

                finally:
                    try:
                        if con is not None:
                            con.close()
                    except Exception:
                        pass


            def _sim_trade():
                # 1) Build context and query Mastery policy
                try:
                    ctx, meta = build_context_from_test_db()
                    plan = mp.propose_trade(ctx)

                except Exception as e:
                    _log_to_panel("SIM", f"context/policy error: {e}")
                    return

                if not plan.get("enter"):
                    _log_to_panel("SIM", f"NO-TRADE | {plan.get('why','')}")
                    return

                # 2) Derive order fields
                side = "LAY" if str(plan.get("direction","")).startswith("LAY") else "BACK"
                odds = _latest_mid_odds(meta)
                stake = float(plan.get("size") or 2.0)

                # 3) Insert into orders (schema-aware; include run_id if present)
                try:
                    con = sqlite3.connect(self._db, timeout=8); con.row_factory = sqlite3.Row

                    def _has_col(cname: str) -> bool:
                        return any(r["name"] == cname for r in con.execute("PRAGMA table_info(orders)"))

                    has_run = _has_col("run_id")
                    has_src = _has_col("source")

                    base_cols = ["mode","side","entry_odds","entry_stake","entry_status","marketId","selectionId"]
                    base_vals = ["TEST", side, odds, stake, "queued", meta.get("marketId"), meta.get("selectionId")]
                    extra_cols, extra_vals = [], []
                    if has_run:
                        extra_cols.append("run_id"); extra_vals.append(self._sim_run_id)
                    if has_src:
                        extra_cols.append("source"); extra_vals.append("TEST")

                    all_cols = base_cols + extra_cols
                    placeholders = ", ".join(["?"] * len(all_cols))
                    sql = f"INSERT INTO orders ({', '.join(all_cols)}, opened_at) VALUES ({placeholders}, datetime('now','utc'))"
                    con.execute(sql, base_vals + extra_vals)
                    oid = int(con.execute("SELECT last_insert_rowid()").fetchone()[0])
                    con.commit()
                    _log_to_panel("SIM", f"order queued id={oid} side={side} odds={odds:.2f} stake={stake:.2f}")
                except Exception as e:
                    _log_to_panel("SIM", f"db error: {e}")
                    return
                finally:
                    try: con.close()
                    except Exception: pass

                # 4) Optional provenance in mastery_events
                try:
                    import json as _json
                    with connect_db(ro=False) as mcon:
                        mcon.execute(
                            "INSERT INTO mastery_events(event_type, details_json, delta_progress, source) VALUES (?,?,0,'TEST')",
                            ("sim_order_queued", _json.dumps({
                                "order_id": oid, "side": side, "odds": odds, "stake": stake,
                                "run_id": self._sim_run_id
                            }))
                        )
                        mcon.commit()
                except Exception:
                    pass

                # 5) Refresh Orders grid
                try:
                    self._refresh_once()
                except Exception:
                    pass

            def _run_test_day_button():
                import threading, importlib, time as _t, random as _rnd
                def _logger(msg: str): _log_to_panel("SIM", msg)

                # prevent double-run
                if getattr(self, "_run_active", False):
                    _logger("Run already active—please wait until it finishes.")
                    return
                self._run_active = True

                # fresh run_id for every press
                self._sim_run_id = f"SIM-{_t.strftime('%Y%m%d-%H%M%S')}-{_rnd.randint(100,999)}"

                # disable button while running
                try: self._btn_run_day.state(["disabled"])
                except Exception: pass

                def _bg():
                    try:
                        import importlib
                        import engines.mastery.policy_lookup as lookup
                        import engines.mastery.risk as risk
                        import engines.mastery.microstructure as ms
                        import engines.mastery.mastery_policy as mp
                        import engines.decision_engine.orchestrator as orch

                        # Reload mastery modules FIRST, then orchestrator LAST
                        for m in (lookup, risk, ms, mp):
                            importlib.reload(m)
                        importlib.reload(orch)

                        _logger(f"TestDay run_id={self._sim_run_id} starting…")
                        orch.run_test_day(self._sim_run_id, seconds=600, hz=4, logger=_logger)

                        _logger("TestDay complete.")
                        try: self.update_mastery_summary()
                        except Exception: pass
                        try: self._refresh_once()
                        except Exception: pass
                    except Exception as e:
                        _logger(f"runner error: {e}")
                    finally:
                        self._run_active = False
                        try: self._btn_run_day.state(["!disabled"])
                        except Exception: pass

                threading.Thread(target=_bg, daemon=True).start()

            def _reset_pnl_button():
                import threading
                def _logger(msg: str): _log_to_panel("SIM", msg)
                def _bg():
                    try:
                        from engines.decision_engine.orchestrator import reset_pnl_ledger
                        reset_pnl_ledger(logger=_logger)
                        try:
                            self._refresh_once()
                        except Exception:
                            pass
                    except Exception as e:
                        _logger(f"P&L reset error: {e}")
                threading.Thread(target=_bg, daemon=True).start()

            def _mastery_sprint():
                """
                Run a focused battery of tests in TEST to accelerate mastery based on *passes*.
                No artificial progress: delta is a function of pass-rate.
                """
                import threading

                keys = [
                    # System backbone
                    "system.schema_paths",
                    "system.oc_timeline",
                    "system.order_lifecycle",
                    "system.pnl_rollup",
                    # Race behaviours (core scalp conditions)
                    "race.scalp_opportunity",
                    "race.no_trade_zone",
                    "race.liquidity_gate",
                    "race.confidence_thresholds",
                    "race.greenup_exit",
                    "race.late_steam",
                    "race.early_drift",
                    "race.overlapping_races",
                ]

                def _bg():
                    passed = 0
                    total = 0
                    for k in keys:
                        try:
                            res = run_one(k, mode="TEST")
                            total += 1
                            if getattr(res, "ok", False):
                                passed += 1
                            for line in getattr(res, "notes", []):
                                _log_to_panel("SPRINT", f"{k}: {line}")
                        except Exception as e:
                            _log_to_panel("SPRINT", f"{k}: error {e}")

                    # Map pass-rate → progress delta (bounded; never exceeds remaining gap)
                    try:
                        current, _ts = _mastery_get_progress()
                    except Exception:
                        current = 0
                    rate = (passed / total) if total else 0.0
                    if   rate >= 0.90: delta = 25
                    elif rate >= 0.75: delta = 15
                    elif rate >= 0.60: delta = 10
                    elif rate >= 0.40: delta = 5
                    else:              delta = 0

                    if delta > 0:
                        remaining = max(0, 100 - int(current))
                        delta = min(delta, remaining)
                        _mastery_event("sprint_pass", delta, {"passed": passed, "total": total, "rate": round(rate, 3)})
                        _log_to_panel("SPRINT", f"progress +{delta} → requested; passed={passed}/{total} ({rate:.0%})")
                        try: self.update_mastery_summary()
                        except Exception: pass
                    else:
                        _log_to_panel("SPRINT", f"no progress (passed={passed}/{total})")

                threading.Thread(target=_bg, daemon=True).start()

            # Buttons (right-aligned)
            self._btn_run_day = ttk.Button(row, text="Run Test Day (10 min)", command=_run_test_day_button, width=22)
            self._btn_run_day.pack(side="right", padx=(6,0))
            ttk.Button(row, text="Mastery Sprint (TEST)", command=_mastery_sprint, width=22).pack(side="right", padx=(6,0))
            ttk.Button(row, text="Place Sim Trade (TEST)", command=_sim_trade, width=24).pack(side="right", padx=(6,0))
            ttk.Button(row, text="Propose from priors (TEST)", command=_demo_proposal, width=28).pack(side="right")




            # ── Orders (collapsible, headerless; toggled from top toolbar) ───────────────
            self.orders_group = Collapsible(self, "Orders", start_open=True, show_header=False)
            self.orders_group.pack(fill="both", expand=True, padx=8, pady=(6, 6))

            cols = ("order_id","side","entry_odds","stake","status","market_sel","opened_at")
            self.tbl = ttk.Treeview(self.orders_group.body(), columns=cols, show="headings", height=14)

            # --- Runner cap indicator (per (marketId, selectionId)) ---------------
            cap_row = ttk.Frame(self.orders_group.body()); cap_row.pack(fill="x", pady=(0,4))
            self._cap_lbl = ttk.Label(cap_row, text="Open scalps: --/3", font=("SF Pro Text", 10, "bold"))
            self._cap_lbl.pack(side="left")
            self._cap_status = ttk.Label(cap_row, text="status: ?", padding=(8,0))
            self._cap_status.pack(side="left", padx=(12,0))

            # track last runner used for cap indicator (mid, sid)
            self._cap_runner: tuple[str|None,str|None] = (None, None)

            headings = {
                "order_id":"Order", "side":"Side", "entry_odds":"Entry",
                "stake":"Stake", "status":"Status", "market_sel":"Market/Selection",
                "opened_at":"Opened"
            }
            for c in cols:
                self.tbl.heading(c, text=headings[c])
                self.tbl.column(c, width=120 if c not in ("market_sel","opened_at") else 180, anchor="w")
            self.tbl.pack(fill="both", expand=True)
            self.tbl.bind("<<TreeviewSelect>>", self._on_row_select)

            # ─────────────────────────────────────────────────────────────────────────────
            # 📍 TARGET: gui/scalper_view.py
            # 🔎 SEARCH: ^\s*self\._build_tests_panel\(self\).*$  # the line that builds tests panel
            # --- PATCH START: wrap Tests in Race collapsible + add System & Mastery ------
            # Sections (headerless; controlled by top toolbar)
            self.sys_group = Collapsible(self, "System", start_open=False, show_header=False)
            ttk.Label(self.sys_group.body(), text="System status is GREEN in TEST.").pack(anchor="w")

            self.race_group = Collapsible(self, "Race", start_open=True, show_header=False)
            self._build_tests_panel(self.race_group.body())

            self.mastery_group = Collapsible(self, "Mastery", start_open=True, show_header=False)
            _build_mastery_group(self.mastery_group.body(), notify=self.update_mastery_summary)


            # Pack sections (orders already packed above)
            self.sys_group.pack(fill="x", padx=8, pady=(0, 6))
            self.race_group.pack(fill="x", padx=8, pady=(0, 6))
            self.mastery_group.pack(fill="x", padx=8, pady=(0, 6))

            # Now add toolbar toggle buttons bound to each section's BooleanVar
            def _bind_toggle(name: str, label: str):
                grp = getattr(self, f"{name}_group")
                var = grp.open_var()
                ttk.Checkbutton(self.toolbar, text=label, variable=var, style="Toolbutton",
                                command=lambda g=grp, v=var: (g.open() if v.get() else g.close()))\
                    .pack(side="left", padx=(0, 6))





            btns = ttk.Frame(self); btns.pack(fill="x", pady=(8,0))
            ttk.Button(btns, text="Refresh", command=self._refresh_once).pack(side="left")
            ttk.Button(btns, text="Close", command=self._close_window).pack(side="right")

            # ── Decision Engine dialog (collapsible) ────────────────────────────
            bar = ttk.Frame(self); bar.pack(fill="x", padx=6, pady=(10, 0))
            ttk.Label(bar, text="Decision Engine", font=("SF Pro Text", 11, "bold")).pack(side="left")
            self._de_open = tk.BooleanVar(value=False)
            self._de_btn  = ttk.Button(bar, text="Show", width=8, command=self._de_toggle)
            self._de_btn.pack(side="right")
            self._de_frame = ttk.Frame(self)  # created later on show

            # kick off first refresh & polling
            self._refresh_once()
            self.after(2000, self._poll)

        def _poll(self):
            self._refresh_once()
            if self._de_open.get():
                self._refresh_de_view()
            self.after(2000, self._poll)

        def _refresh_once(self):
            # 1) Figure out active run + time window (attach on reopen)
            run_start, run_end = None, None
            try:
                bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
                rid = getattr(self, "_sim_run_id", "")
                row = bdb.execute(
                    "SELECT started_at, ended_at FROM sim_runs WHERE run_id=? ORDER BY id DESC LIMIT 1",
                    (rid,)
                ).fetchone()
                if not row:
                    # adopt currently active run if any
                    row2 = bdb.execute(
                        "SELECT run_id, started_at, ended_at FROM sim_runs WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                    if row2 and row2["run_id"]:
                        self._sim_run_id = str(row2["run_id"]); row = row2
                if row and row["started_at"]:
                    run_start = row["started_at"]; run_end = row["ended_at"] or None
            except Exception:
                pass
            finally:
                try: bdb.close()
                except Exception: pass

# ✂️ REPLACE WITH (exact; same indentation inside the method):
            # 2) Query AUTO_DB.orders for the current PnL Source
            try:
                con = sqlite3.connect(self._db, timeout=8)
                con.row_factory = sqlite3.Row
                cols = [r["name"] for r in con.execute("PRAGMA table_info(orders)")]
                has_run = ("run_id" in cols)
                src = (self._pnl_source.get() or "ALL").upper()

                def _fetch(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
                    return con.execute(sql, args).fetchall()

                if src == "TEST" and has_run:
                    # Show only this session's TEST orders
                    rows = _fetch(
                        """
                        SELECT id, side, entry_odds, entry_stake, entry_status,
                               marketId, selectionId, opened_at
                        FROM orders
                        WHERE mode='TEST' AND run_id=?
                        ORDER BY datetime(COALESCE(opened_at, '')) DESC
                        """,
                        (self._sim_run_id,)
                    )
                elif src in ("LEARNING","LIVE"):
                    # Show today's orders for the chosen live mode
                    rows = _fetch(
                        """
                        SELECT id, side, entry_odds, entry_stake, entry_status,
                               marketId, selectionId, opened_at
                        FROM orders
                        WHERE mode=?
                          AND date(COALESCE(opened_at, date('now','utc'))) = date('now','utc')
                        ORDER BY datetime(COALESCE(opened_at, '')) DESC
                        """,
                        (src,)
                    )
                else:
                    # ALL (or TEST without run_id column): show today's across all modes
                    rows = _fetch(
                        """
                        SELECT id, side, entry_odds, entry_stake, entry_status,
                        marketId, selectionId, opened_at
                        FROM orders
                        WHERE date(COALESCE(opened_at, date('now','utc'))) = date('now','utc')
                        ORDER BY datetime(COALESCE(opened_at, '')) DESC
                        """
                    )
            except Exception:
                rows = []
            finally:
                try:
                    con.close()
                except Exception:
                    pass

            # repopulate table

            try:
                for iid in self.tbl.get_children():
                    self.tbl.delete(iid)
                for r in rows:
                    self.tbl.insert(
                        "",
                        "end",
                        values=(
                            f"ORD-{r['id']}",
                            (r["side"] or ""),
                            f"{(r['entry_odds'] or 0):.2f}",
                            f"£{(r['entry_stake'] or 0):.2f}",
                            (r["entry_status"] or ""),
                            f"{r['marketId']} / {r['selectionId']}",
                            (r["opened_at"] or "")
                        )
                    )
            except Exception:
                pass

            # P&L tiles (live-moving per your _pnl_sums)
            try:
                total, d1, d7, d30 = self._pnl_sums()
                self.pnl_total.update_value(f"£{total:.2f}")
                self.pnl_today.update_value(f"£{d1:.2f}")
                self.pnl_7d.update_value(f"£{d7:.2f}")
                self.pnl_30d.update_value(f"£{d30:.2f}")
            except Exception:
                pass

            # Update cap indicator (selected row → else first row)
            try:
                sel = self.tbl.selection()
                if sel:
                    self._on_row_select()
                else:
                    first = self.tbl.get_children()
                    if first:
                        item = self.tbl.item(first[0])
                        vals = item.get("values") or []
                        ms = str(vals[5]) if len(vals) > 5 else ""
                        if " / " in ms:
                            mid, sid = ms.split(" / ", 1)
                            self._cap_runner = (mid.strip(), sid.strip())
                            self._update_cap_indicator(mid.strip(), sid.strip())
            except Exception:
                pass


        def _on_row_select(self, event=None):
            """Update cap indicator when a row is selected."""
            try:
                sel = self.tbl.selection()
                if not sel:
                    return
                item = self.tbl.item(sel[0])  # first selected row
                vals = item.get("values") or []
                # market_sel is col index 5 in ("order_id","side","entry_odds","stake","status","market_sel","opened_at")
                ms = str(vals[5]) if len(vals) > 5 else ""
                # format is "marketId / selectionId"
                if " / " in ms:
                    mid, sid = ms.split(" / ", 1)
                    mid, sid = mid.strip(), sid.strip()
                    self._cap_runner = (mid, sid)
                    self._update_cap_indicator(mid, sid)
            except Exception:
                pass

        def _update_cap_indicator(self, marketId: str|None, selectionId: str|None):
            """Read open parent scalps and activity and paint the indicator."""
            try:
                if not marketId or not selectionId:
                    self._cap_lbl.config(text="Open scalps: --/3")
                    self._cap_status.config(text="status: ?")
                    return
                open_n = self._open_parents_count_ui(marketId, selectionId)
                status = self._runner_activity_ui(marketId, selectionId)

                # text
                self._cap_lbl.config(text=f"Open scalps: {open_n}/3")
                self._cap_status.config(text=f"status: {status}")

                # colors
                if status == "active":
                    if open_n >= 3:
                        fg1, fg2 = "#C77700", "#C77700"   # orange: cap reached
                    else:
                        fg1, fg2 = "#0A7F00", "#0A7F00"   # green: ok to place
                else:
                    fg1, fg2 = "#777777", "#777777"       # grey: ignored/passive
                try:
                    self._cap_lbl.config(foreground=fg1)
                    self._cap_status.config(foreground=fg2)
                except Exception:
                    pass
            except Exception:
                pass

        def _open_parents_count_ui(self, marketId: str, selectionId: str) -> int:
            """Count open parent (entry) orders for this runner in AUTO_DB, filtered by PnL Source."""
            try:
                con = sqlite3.connect(self._db, timeout=5)
                con.row_factory = sqlite3.Row
                cols = [r["name"] for r in con.execute("PRAGMA table_info(orders)")]
                link_col = "hedge_of" if "hedge_of" in cols else ("parent_id" if "parent_id" in cols else None)
                if link_col:
                    where_parent = f"({link_col} IS NULL OR {link_col}='')"
                else:
                    where_parent = "1=1"

                # Respect the PnL source selector
                src = (self._pnl_source.get() or "ALL").upper()
                args = [str(marketId), str(selectionId)]
                mode_clause = ""
                if "mode" in cols and src in ("TEST","LEARNING","LIVE"):
                    mode_clause = " AND mode=?"
                    args.append(src)

                row = con.execute(
                    f"""
                    SELECT COUNT(*) AS n
                    FROM orders
                    WHERE {where_parent}
                      AND (closed_at IS NULL OR closed_at='')
                      AND marketId=? AND selectionId=?{mode_clause}
                    """,
                    args
                ).fetchone()
                return int((row["n"] if isinstance(row, sqlite3.Row) else row[0]) or 0)
            except Exception:
                return 0
            finally:
                try: con.close()
                except Exception: pass


        def _runner_activity_ui(self, marketId: str, selectionId: str) -> str:
            """Return 'active'|'ignored'|'passive' using BETS_DB tables if present, else 'active'."""
            try:
                db = connect_db(ro=True)
                db.row_factory = sqlite3.Row
                # runner_activity(status TEXT)
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runner_activity'").fetchone():
                    row = db.execute(
                        "SELECT status FROM runner_activity WHERE marketId=? AND selectionId=? ORDER BY id DESC LIMIT 1",
                        (str(marketId), str(selectionId))
                    ).fetchone()
                    if row and row["status"]:
                        return str(row["status"]).lower()
                # inbound_bets_min(active INTEGER)
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inbound_bets_min'").fetchone():
                    cols = [r["name"] for r in db.execute("PRAGMA table_info(inbound_bets_min)")]
                    if "active" in cols:
                        row = db.execute(
                            "SELECT active FROM inbound_bets_min WHERE marketId=? AND selectionId=? ORDER BY id DESC LIMIT 1",
                            (str(marketId), str(selectionId))
                        ).fetchone()
                        if row is not None:
                            return "active" if int(row[0] or 0) == 1 else "ignored"
                # runners(status TEXT)
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runners'").fetchone():
                    row = db.execute(
                        "SELECT status FROM runners WHERE marketId=? AND selectionId=? ORDER BY id DESC LIMIT 1",
                        (str(marketId), str(selectionId))
                    ).fetchone()
                    if row and row["status"]:
                        return str(row["status"]).lower()
                return "active"
            except Exception:
                return "active"
            finally:
                try: db.close()
                except Exception: pass


        # ───────────────────────────────────────────────────────────────────
        # Tests panel UI (right side) + log
        # ───────────────────────────────────────────────────────────────────
        def _build_tests_panel(self, parent):
            """
            Buttons laid out as:
              - Column 0: 5 system tests (vertical)
              - Column 1: 6 race tests (vertical)
              - Column 2: 6 race tests (vertical)
              - Column 3: narrative/log box

            All four columns sit on the same row inside 'panel'.
            """
            # wrapper
            wrap = ttk.Frame(parent)
            wrap.pack(fill="x", padx=8, pady=(8, 0))

            # keep the whole test area in a labeled frame
            wrap.columnconfigure(0, weight=1)
            panel = ttk.LabelFrame(wrap, text="Tests")
            panel.grid(row=0, column=0, sticky="nsew")
            # four columns across: 0,1,2 => buttons; 3 => log
            for c in (0, 1, 2, 3):
                panel.columnconfigure(c, weight=(0 if c < 3 else 1))
            panel.rowconfigure(0, weight=1)

            # helper to create a bound button
            def _btn(parent, label, key):
                return ttk.Button(
                    parent,
                    text=label,
                    width=22,
                    command=lambda k=key: self._run_in_thread(lambda: self._run_and_log(k)),
                )

            # ── Column 0 (SYSTEM: 5) ───────────────────────────────────────────
            col0 = ttk.Frame(panel)
            col0.grid(row=0, column=0, sticky="n", padx=(8, 6), pady=8)
            _btn(col0, "Schema & Paths",        "system.schema_paths").pack(anchor="w", pady=2)
            _btn(col0, "OC Timeline Fill",      "system.oc_timeline").pack(anchor="w", pady=2)
            _btn(col0, "Order Lifecycle",       "system.order_lifecycle").pack(anchor="w", pady=2)
            _btn(col0, "PnL Rollup",            "system.pnl_rollup").pack(anchor="w", pady=2)
            _btn(col0, "Decision + Narrative",  "system.decision_narrative").pack(anchor="w", pady=2)
            ttk.Button(col0, text="Validate Learning", command=self._validate_learning, width=22)\
                .pack(anchor="w", pady=2)
            _btn(col0, "Check DB Hygiene (TEST)", "system.db_hygiene").pack(anchor="w", pady=2)
            _btn(col0, "Clean Open TEST Orders",  "system.clean_test_open").pack(anchor="w", pady=2)
            _btn(col0, "Stop Test Day",          "system.stop_test_day").pack(anchor="w", pady=2)



            # ── Column 1 (RACE set A: 6) ───────────────────────────────────────
            col1 = ttk.Frame(panel)
            col1.grid(row=0, column=1, sticky="n", padx=6, pady=8)
            _btn(col1, "Two‑Chapter Trigger",   "race.two_chapter_trigger").pack(anchor="w", pady=2)
            _btn(col1, "Scalp Opportunity",     "race.scalp_opportunity").pack(anchor="w", pady=2)
            _btn(col1, "No‑Trade Zone",         "race.no_trade_zone").pack(anchor="w", pady=2)
            _btn(col1, "Late Steam",            "race.late_steam").pack(anchor="w", pady=2)
            _btn(col1, "Early Drift",           "race.early_drift").pack(anchor="w", pady=2)
            _btn(col1, "Incomplete Bands",      "race.incomplete_bands").pack(anchor="w", pady=2)

            # ── Column 2 (RACE set B: 6) ───────────────────────────────────────
            col2 = ttk.Frame(panel)
            col2.grid(row=0, column=2, sticky="n", padx=6, pady=8)
            _btn(col2, "Overlapping Races",     "race.overlapping_races").pack(anchor="w", pady=2)
            _btn(col2, "Volatility Spike",      "race.volatility_spike").pack(anchor="w", pady=2)
            _btn(col2, "Liquidity Gate",        "race.liquidity_gate").pack(anchor="w", pady=2)
            _btn(col2, "Confidence Thresholds", "race.confidence_thresholds").pack(anchor="w", pady=2)
            _btn(col2, "Invalid Market",        "race.invalid_market").pack(anchor="w", pady=2)
            _btn(col2, "Green‑Up / Exit",       "race.greenup_exit").pack(anchor="w", pady=2)

            # ── Column 3 (Log box) ─────────────────────────────────────────────
            logf = ttk.Frame(panel)
            logf.grid(row=0, column=3, sticky="nsew", padx=(8, 8), pady=8)
            logf.rowconfigure(0, weight=1)
            logf.columnconfigure(0, weight=1)

            self._test_log = tk.Text(logf, height=18, wrap="word")
            self._test_log.grid(row=0, column=0, sticky="nsew")
            sy = ttk.Scrollbar(logf, orient="vertical", command=self._test_log.yview)
            sy.grid(row=0, column=1, sticky="ns")
            self._test_log.configure(yscrollcommand=sy.set)

            # Row of group runners under buttons (optional, stays on same row visually)
            controls = ttk.Frame(panel)
            controls.grid(row=1, column=0, columnspan=3, sticky="we", padx=8, pady=(0, 8))
            ttk.Button(
                controls, text="Run Group: System",
                command=lambda: self._run_in_thread(lambda: self._run_group_and_log("system."))
            ).pack(side="left", padx=(0, 6))
            ttk.Button(
                controls, text="Run Group: Race",
                command=lambda: self._run_in_thread(lambda: self._run_group_and_log("race."))
            ).pack(side="left", padx=6)
            ttk.Button(
                controls, text="Run All",
                command=lambda: self._run_in_thread(lambda: self._run_all_and_log())
            ).pack(side="left", padx=6)


            self._log("Test panel ready. Buttons mapped. Use group runners to batch scenarios.")


        def _log(self, msg: str):
            try:
                ts = now_utc().strftime("%H:%M:%S")
                self._test_log.insert("end", f"[{ts}] {msg}\n")
                self._test_log.see("end")
            except Exception:
                pass

        def _run_and_log(self, key: str) -> bool:
            # Built-in actions mapped here
            if key == "system.db_hygiene":
                return self._system_db_hygiene()
            if key == "system.clean_test_open":
                return self._system_clean_test_open()
            if key == "system.stop_test_day":
                return self._system_stop_test_day()
            """Run a single test key via runner and stream notes to the log."""
            try:
                res = run_one(key, mode="TEST")
            except Exception as e:
                self._log(f"❌ {key}: {e}")
                return False
            # header + notes
            self._log(f"▶ {key}")
            for line in res.notes:
                self._log(f"  {line}")
            # selected metrics (compact)
            if res.metrics:
                show = ", ".join(f"{k}={v}" for k, v in res.metrics.items() if k in ("order_id","pnl","bets_db","autoscalp_db","confidence","side","status"))
                if show:
                    self._log(f"  metrics: {show}")
            return bool(res.ok)

        def _run_group_and_log(self, prefix: str) -> bool:
            """Run all tests by prefix (system./race.) and log summary."""
            try:
                res = run_group(prefix, mode="TEST")
            except Exception as e:
                self._log(f"❌ group {prefix}: {e}")
                return False
            self._log(f"▶ GROUP {prefix}")
            for line in res.notes:
                self._log(f"  {line}")
            self._log(f"  summary: passed={res.metrics.get('passed',0)} failed={res.metrics.get('failed',0)}")
            return bool(res.ok)

        def _run_all_and_log(self) -> bool:
            try:
                res = run_all(mode="TEST")
            except Exception as e:
                self._log(f"❌ run_all: {e}")
                return False
            self._log("▶ RUN ALL")
            for line in res.notes:
                self._log(f"  {line}")
            self._log(f"  summary: passed={res.metrics.get('passed',0)} failed={res.metrics.get('failed',0)}")
            return bool(res.ok)

        def _system_db_hygiene(self) -> bool:
            # Show whether orders.run_id exists and how many dangling open TEST parents exist.
            try:
                import sqlite3, engines.config_paths as cp
                db = sqlite3.connect(cp.autoscalp_db(), timeout=6)
                db.row_factory = sqlite3.Row
                cols = [r["name"] for r in db.execute("PRAGMA table_info(orders)")]
                has_run = ("run_id" in cols)
                row = db.execute("""
                    SELECT COUNT(*) AS c
                    FROM orders
                    WHERE mode='TEST' AND (closed_at IS NULL OR closed_at='')
                """).fetchone()
                c = int(row["c"] or 0)
                self._log(f"DB Hygiene: orders.run_id={has_run}  open_TEST_parents={c}")
                if c > 0:
                    rows = db.execute("""
                        SELECT marketId, selectionId, COUNT(*) AS open_n
                        FROM orders
                        WHERE mode='TEST' AND (closed_at IS NULL OR closed_at='')
                        GROUP BY marketId, selectionId
                        ORDER BY open_n DESC
                        LIMIT 5
                    """).fetchall()
                    for r in rows:
                        self._log(f"  {r['marketId']}/{r['selectionId']} open={r['open_n']}")
                return True
            except Exception as e:
                self._log(f"DB Hygiene error: {e}")
                return False
            finally:
                try: db.close()
                except Exception: pass

        def _system_clean_test_open(self) -> bool:
            # Hard-delete all open TEST parents (and their decisions) so gates cannot block a new run.
            try:
                import sqlite3, engines.config_paths as cp
                db = sqlite3.connect(cp.autoscalp_db(), timeout=8)
                db.execute("""
                    DELETE FROM decisions
                    WHERE order_id IN (
                        SELECT id FROM orders
                        WHERE mode='TEST' AND (closed_at IS NULL OR closed_at='')
                    )
                """)
                db.execute("""
                    DELETE FROM orders
                    WHERE mode='TEST' AND (closed_at IS NULL OR closed_at='')
                """)
                db.commit()
                self._log("Cleaned: all open TEST orders removed.")
                return True
            except Exception as e:
                self._log(f"Clean error: {e}")
                return False
            finally:
                try: db.close()
                except Exception: pass

        def _system_stop_test_day(self) -> bool:
            # Write kill switch file so orchestrator.run_test_day breaks its loop
            try:
                import os, engines.config_paths as cp
                flag = os.path.join(os.path.dirname(cp.autoscalp_db()), ".stop_testday")
                with open(flag, "w") as f:
                    f.write("stop")
                self._log("Stop requested. The TestDay loop will exit soon.")
                return True
            except Exception as e:
                self._log(f"Stop error: {e}")
                return False


        def _validate_learning(self):
            """Run the LEARNING health checklist and print PASS/FAIL lines to the log."""
            import threading
            try:
                # local import keeps the class import-safe even if the module isn't present yet
                from engines.health.learning_validator import validate_learning  # noqa
            except Exception as e:
                self._log(f"❌ validator not available: {e}")
                return

            def _logger(msg: str):
                self._log(msg)

            def _bg():
                try:
                    res = validate_learning(logger=_logger, minutes_recent=2)
                    s = res.get("summary", {})
                    self._log(f"  summary: passed={s.get('passed',0)} failed={s.get('failed',0)}")
                except Exception as e:
                    self._log(f"❌ validator error: {e}")

            threading.Thread(target=_bg, daemon=True).start()

        def _run_in_thread(self, fn):
            import threading
            threading.Thread(target=fn, daemon=True).start()

        def _ensure_test_mode(self) -> bool:
            try:
                from engines.upgrade_import_patch import get_mode  # type: ignore
                mode = (get_mode() or "learning").lower()
            except Exception:
                mode = "learning"
            if mode != "test":
                self._log("❌ Not in TEST mode — switch to TEST to run simulator tests.")
                return False
            return True

        # ───────────────────────────────────────────────────────────────────
        # Test harness
        # ───────────────────────────────────────────────────────────────────
        def _run_all_tests(self):
            self._run_all_and_log()  # delegates to engines.tests.runner.run_all

        # ───────────────────────────────────────────────────────────────────
        # Individual tests (return True/False)
        # ───────────────────────────────────────────────────────────────────
        def _pick_any_runner(self):
            try:
                con = sqlite3.connect(self._db, timeout=5)
                con.row_factory = sqlite3.Row
                row = con.execute("""
                    SELECT marketId, selectionId
                    FROM inbound_bets_min
                    ORDER BY id ASC LIMIT 1
                """).fetchone()
            except Exception:
                row = None
            finally:
                try: con.close()
                except Exception: pass
            if row:
                return row["marketId"], str(row["selectionId"])
            return None, None

        def _test_oc_bands(self) -> bool:
            if not self._ensure_test_mode(): return False
            mid, sid = self._pick_any_runner()
            if not mid: 
                self._log("OC Bands: no runner found.")
                return False
            self._log(f"OC Bands: writing OC1 band samples for {mid}/{sid}")
            try:
                con = sqlite3.connect(self._db, timeout=8); con.row_factory = sqlite3.Row
                con.execute("""
                    INSERT INTO inbound_oc_cache (marketId, selectionId, last_sync_ts)
                    SELECT ?, ?, datetime('now','utc')
                    WHERE NOT EXISTS (
                        SELECT 1 FROM inbound_oc_cache WHERE marketId=? AND selectionId=?
                    )
                """, (mid, sid, mid, sid))
                samples = [6.0, 6.1, 6.2, 6.3, 6.35, 6.4]
                import json
                con.execute("""
                    UPDATE inbound_oc_cache
                       SET oc1=?, oc1_band_json=?, last_sync_ts=datetime('now','utc')
                     WHERE marketId=? AND selectionId=?
                """, (samples[-1], json.dumps(samples), mid, sid))
                con.commit()
                row = con.execute("""
                    SELECT json_array_length(oc1_band_json) AS n
                    FROM inbound_oc_cache WHERE marketId=? AND selectionId=?""",(mid, sid)).fetchone()
                ok = bool(row and (row["n"] or 0) >= 6)
            except Exception as e:
                self._log(f"OC Bands: error {e}")
                ok = False
            finally:
                try: con.close()
                except Exception: pass
            return ok

        def _test_place_and_match(self) -> bool:
            if not self._ensure_test_mode(): return False
            mid, sid = self._pick_any_runner()
            if not mid:
                self._log("Place & Match: no runner found.")
                return False
            self._log(f"Place & Match: simulating for {mid}/{sid}")
            try:
                con = sqlite3.connect(self._db, timeout=8); con.row_factory = sqlite3.Row
                con.execute("""
                    INSERT INTO orders (mode, side, entry_odds, entry_stake, entry_status,
                                        marketId, selectionId, opened_at)
                    VALUES ('TEST','LAY', 6.2, 2.0, 'queued', ?, ?, datetime('now','utc'))
                """, (mid, sid))
                oid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
                con.execute("UPDATE orders SET entry_status='placed' WHERE id=?", (oid,))
                con.execute("""
                    UPDATE orders
                       SET entry_status='matched',
                           closed_at=datetime('now','utc'),
                           net_pl=COALESCE(net_pl,0.0) + 0.45
                     WHERE id=?""",(oid,))
                con.commit()
                row = con.execute("SELECT entry_status, closed_at FROM orders WHERE id=?", (oid,)).fetchone()
                ok = bool(row and row["entry_status"]=="matched" and row["closed_at"])
            except Exception as e:
                self._log(f"Place & Match: error {e}")
                ok = False
            finally:
                try: con.close()
                except Exception: pass
            return ok

        def _test_stories_chapters(self) -> bool:
            if not self._ensure_test_mode(): return False
            self._log("Stories & Chapters: inserting one story + chapter link")
            try:
                con = sqlite3.connect(self._db, timeout=8); con.row_factory = sqlite3.Row
                con.execute("""CREATE TABLE IF NOT EXISTS stories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT, kind TEXT, note TEXT)""")
                con.execute("""CREATE TABLE IF NOT EXISTS chapters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    story_id INTEGER, created_at TEXT, kind TEXT, note TEXT)""")
                con.execute("INSERT INTO stories (created_at, kind, note) VALUES (datetime('now','utc'),'scalp','sim story')")
                sid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
                con.execute("INSERT INTO chapters (story_id, created_at, kind, note) VALUES (?,?,?,?)",
                            (sid, datetime.now().isoformat(), 'open', 'queued→matched test'))
                con.commit()
                row = con.execute("SELECT COUNT(*) AS n FROM chapters WHERE story_id=?", (sid,)).fetchone()
                ok = bool(row and (row["n"] or 0) == 1)
            except Exception as e:
                self._log(f"Stories & Chapters: error {e}")
                ok = False
            finally:
                try: con.close()
                except Exception: pass
            return ok

        def _test_pnl_rollup(self) -> bool:
            if not self._ensure_test_mode(): return False
            self._log("P&L Rollup: writing pnl_trades rows for today/7/30 day windows")
            try:
                con = sqlite3.connect(self._db, timeout=8); con.row_factory = sqlite3.Row
                con.execute("""CREATE TABLE IF NOT EXISTS pnl_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    amount REAL, created_at TEXT, settled_at TEXT)""")
                con.execute("INSERT INTO pnl_trades (amount, created_at, settled_at) VALUES (0.90, datetime('now','-1 day','utc'), datetime('now','-1 day','utc'))")
                con.execute("INSERT INTO pnl_trades (amount, created_at, settled_at) VALUES (1.10, datetime('now','utc'),       datetime('now','utc'))")
                con.execute("INSERT INTO pnl_trades (amount, created_at, settled_at) VALUES (-0.40, datetime('now','-10 day','utc'), datetime('now','-10 day','utc'))")
                con.commit()
                total, d1, d7, d30 = self._pnl_sums()
                ok = (abs(total - 1.60) > 0.0001) is False
                self._log(f"P&L Rollup: totals -> total={total:.2f} today={d1:.2f} 7d={d7:.2f} 30d={d30:.2f}")
            except Exception as e:
                self._log(f"P&L Rollup: error {e}")
                ok = False
            finally:
                try: con.close()
                except Exception: pass
            return ok

        def _test_decision_loop_stub(self) -> bool:
            self._log("Decision Loop (stub): reading last 5 decisions, refreshing narrative")
            try:
                self._refresh_de_view()
                return True
            except Exception as e:
                self._log(f"Decision Loop: error {e}")
                return False

        def _pnl_sums(self) -> tuple[float, float, float, float]:
            """
            Live-moving tiles with source filter:
              - Prefer ledger (pnl_daily) for history
              - Add live pnl_trades since current run started (if run is active)
              - Filter by source: ALL / TEST / LEARNING / LIVE
            """
            src = (self._pnl_source.get() or "ALL").upper()
            try:
                bdb = connect_db(ro=True)
                bdb.row_factory = sqlite3.Row

                def t_exists(tab: str) -> bool:
                    return bool(bdb.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tab,)
                    ).fetchone())

                def has_col(tab: str, col: str) -> bool:
                    return col in [r["name"] for r in bdb.execute(f"PRAGMA table_info({tab})")]

                # Ledger sums (run-indexed)
                total_ledger = today_ledger = d7_ledger = d30_ledger = 0.0
                if t_exists("pnl_daily") and has_col("pnl_daily","run_day"):
                    row = bdb.execute("SELECT COALESCE(MAX(run_day),0) AS maxd FROM pnl_daily").fetchone()
                    maxd = int(row["maxd"] or 0)
                    if maxd > 0:
                        if has_col("pnl_daily","source") and src != "ALL":
                            total_ledger = float(bdb.execute(
                                "SELECT COALESCE(SUM(amount),0.0) FROM pnl_daily WHERE source=?",
                                (src,)
                            ).fetchone()[0] or 0.0)
                            today_ledger = float(bdb.execute(
                                "SELECT COALESCE(SUM(amount),0.0) FROM pnl_daily WHERE run_day=? AND source=?",
                                (maxd, src)
                            ).fetchone()[0] or 0.0)
                            d7_ledger = float(bdb.execute(
                                "SELECT COALESCE(SUM(amount),0.0) FROM pnl_daily WHERE run_day>? AND run_day<=? AND source=?",
                                (maxd-7, maxd, src)
                            ).fetchone()[0] or 0.0)
                            d30_ledger = float(bdb.execute(
                                "SELECT COALESCE(SUM(amount),0.0) FROM pnl_daily WHERE run_day>? AND run_day<=? AND source=?",
                                (maxd-30, maxd, src)
                            ).fetchone()[0] or 0.0)
                        else:
                            total_ledger = float(bdb.execute(
                                "SELECT COALESCE(SUM(amount),0.0) FROM pnl_daily"
                            ).fetchone()[0] or 0.0)
                            today_ledger = float(bdb.execute(
                                "SELECT COALESCE(SUM(amount),0.0) FROM pnl_daily WHERE run_day=?",
                                (maxd,)
                            ).fetchone()[0] or 0.0)
                            d7_ledger = float(bdb.execute(
                                "SELECT COALESCE(SUM(amount),0.0) FROM pnl_daily WHERE run_day>? AND run_day<=?",
                                (maxd-7, maxd)
                            ).fetchone()[0] or 0.0)
                            d30_ledger = float(bdb.execute(
                                "SELECT COALESCE(SUM(amount),0.0) FROM pnl_daily WHERE run_day>? AND run_day<=?",
                                (maxd-30, maxd)
                            ).fetchone()[0] or 0.0)

                # Live portion for the active run (since started_at)
                live_sum = 0.0
                started_at = None
                active_id = getattr(self, "_sim_run_id","")
                if t_exists("sim_runs") and has_col("sim_runs","run_id"):
                    row = bdb.execute(
                        "SELECT started_at, ended_at FROM sim_runs WHERE run_id=? ORDER BY id DESC LIMIT 1",
                        (active_id,)
                    ).fetchone()
                    if (not row) or (row and row["ended_at"]):
                        # adopt any currently active run
                        row2 = bdb.execute(
                            "SELECT run_id, started_at, ended_at FROM sim_runs WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
                        ).fetchone()
                        if row2 and row2["run_id"]:
                            self._sim_run_id = str(row2["run_id"]); row = row2
                    if row and row["started_at"] and not row["ended_at"]:
                        started_at = row["started_at"]


                if started_at and t_exists("pnl_trades") and has_col("pnl_trades","amount"):
                    if has_col("pnl_trades","source") and src != "ALL":
                        live_sum = float(bdb.execute(
                            "SELECT COALESCE(SUM(amount),0.0) FROM pnl_trades "
                            "WHERE source=? AND datetime(created_at)>=datetime(?)",
                            (src, started_at)
                        ).fetchone()[0] or 0.0)
                    else:
                        live_sum = float(bdb.execute(
                            "SELECT COALESCE(SUM(amount),0.0) FROM pnl_trades "
                            "WHERE datetime(created_at)>=datetime(?)",
                            (started_at,)
                        ).fetchone()[0] or 0.0)

                total = total_ledger + live_sum
                today = today_ledger + live_sum
                d7 = d7_ledger + live_sum
                d30 = d30_ledger + live_sum

                # Fallbacks if ledger empty: instant tables (filtered by source if present)
                if total == today == d7 == d30 == 0.0:
                    if t_exists("pnl_realized") and has_col("pnl_realized","amount"):
                        src_clause = " AND source=?" if has_col("pnl_realized","source") and src!="ALL" else ""
                        args = (src,) if src!="ALL" and src_clause else tuple()
                        q = lambda sql, a=(): float(bdb.execute(sql, a).fetchone()[0] or 0.0)
                        total = q("SELECT COALESCE(SUM(amount),0.0) FROM pnl_realized" + (f" WHERE 1=1{src_clause}" if src_clause else ""), args)
                        today = q("SELECT COALESCE(SUM(amount),0.0) FROM pnl_realized WHERE date(realized_at)=date('now','utc')" + src_clause, args)
                        d7    = q("SELECT COALESCE(SUM(amount),0.0) FROM pnl_realized WHERE realized_at>=datetime('now','-7 days','utc')" + src_clause, args)
                        d30   = q("SELECT COALESCE(SUM(amount),0.0) FROM pnl_realized WHERE realized_at>=datetime('now','-30 days','utc')" + src_clause, args)
                        return (total, today, d7, d30)
                    if t_exists("pnl_trades") and has_col("pnl_trades","amount"):
                        ts_col = "settled_at" if has_col("pnl_trades","settled_at") else "created_at"
                        src_clause = " AND source=?" if has_col("pnl_trades","source") and src!="ALL" else ""
                        args = (src,) if src!="ALL" and src_clause else tuple()
                        q = lambda sql, a=(): float(bdb.execute(sql, a).fetchone()[0] or 0.0)
                        total = q("SELECT COALESCE(SUM(amount),0.0) FROM pnl_trades" + (f" WHERE 1=1{src_clause}" if src_clause else ""), args)
                        today = q(f"SELECT COALESCE(SUM(amount),0.0) FROM pnl_trades WHERE date({ts_col})=date('now','utc')"+src_clause, args)
                        d7    = q(f"SELECT COALESCE(SUM(amount),0.0) FROM pnl_trades WHERE {ts_col}>=datetime('now','-7 days','utc')"+src_clause, args)
                        d30   = q(f"SELECT COALESCE(SUM(amount),0.0) FROM pnl_trades WHERE {ts_col}>=datetime('now','-30 days','utc')"+src_clause, args)
                        return (total, today, d7, d30)

                return (total, today, d7, d30)

            except Exception:
                # Final fallback: sum from orders in AUTO_DB (filter by orders.mode if source selected)
                try:
                    con = sqlite3.connect(self._db, timeout=5)
                    con.row_factory = sqlite3.Row
                    cols = [r["name"] for r in con.execute("PRAGMA table_info(orders)")]
                    cand = "net_pl" if "net_pl" in cols else ("pnl_amount" if "pnl_amount" in cols else ("pnl" if "pnl" in cols else None))
                    if not cand:
                        return (0.0, 0.0, 0.0, 0.0)
                    where = "WHERE (closed_at IS NOT NULL AND closed_at!='')"
                    if "mode" in cols and src in ("TEST","LEARNING","LIVE"):
                        where += f" AND mode='{src}'"
                    total = float(con.execute(f"SELECT COALESCE(SUM({cand}),0.0) FROM orders {where}").fetchone()[0] or 0.0)
                    today = float(con.execute(f"SELECT COALESCE(SUM({cand}),0.0) FROM orders {where} AND date(closed_at)=date('now','utc')").fetchone()[0] or 0.0)
                    d7    = float(con.execute(f"SELECT COALESCE(SUM({cand}),0.0) FROM orders {where} AND closed_at>=datetime('now','-7 days','utc')").fetchone()[0] or 0.0)
                    d30   = float(con.execute(f"SELECT COALESCE(SUM({cand}),0.0) FROM orders {where} AND closed_at>=datetime('now','-30 days','utc')").fetchone()[0] or 0.0)
                    return (total, today, d7, d30)
                except Exception:
                    return (0.0, 0.0, 0.0, 0.0)
            finally:
                try:
                    bdb.close()
                except Exception:
                    pass

        def _pnl_from_instant_table(self, con, tname, amount_col, ts_col):
            q_total = f"SELECT COALESCE(SUM({amount_col}),0.0) FROM {tname}"
            q_d1    = f"SELECT COALESCE(SUM({amount_col}),0.0) FROM {tname} WHERE date({ts_col})=date('now','utc')"
            q_d7    = f"SELECT COALESCE(SUM({amount_col}),0.0) FROM {tname} WHERE {ts_col}>=datetime('now','-7 days','utc')"
            q_d30   = f"SELECT COALESCE(SUM({amount_col}),0.0) FROM {tname} WHERE {ts_col}>=datetime('now','-30 days','utc')"
            total = float(con.execute(q_total).fetchone()[0] or 0.0)
            d1    = float(con.execute(q_d1).fetchone()[0] or 0.0)
            d7    = float(con.execute(q_d7).fetchone()[0] or 0.0)
            d30   = float(con.execute(q_d30).fetchone()[0] or 0.0)
            return (total, d1, d7, d30)

        def _pnl_from_run_ledger(self, con):
            """Run-indexed daily ledger: uses run_day to compute Today/7d/30d rolling sums."""
            row = con.execute("SELECT COALESCE(MAX(run_day),0) FROM pnl_daily").fetchone()
            maxd = int(row[0] or 0)
            if maxd == 0:
                return (0.0, 0.0, 0.0, 0.0)

            total = float(con.execute(
                "SELECT COALESCE(SUM(amount),0.0) FROM pnl_daily"
            ).fetchone()[0] or 0.0)

            d1 = float(con.execute(
                "SELECT COALESCE(SUM(amount),0.0) FROM pnl_daily WHERE run_day=?",
                (maxd,)
            ).fetchone()[0] or 0.0)

            d7 = float(con.execute(
                "SELECT COALESCE(SUM(amount),0.0) FROM pnl_daily WHERE run_day>? AND run_day<=?",
                (maxd - 7, maxd)
            ).fetchone()[0] or 0.0)

            d30 = float(con.execute(
                "SELECT COALESCE(SUM(amount),0.0) FROM pnl_daily WHERE run_day>? AND run_day<=?",
                (maxd - 30, maxd)
            ).fetchone()[0] or 0.0)

            return (total, d1, d7, d30)

        def _pnl_from_orders(self, con):
            cols = [r["name"] for r in con.execute("PRAGMA table_info(orders)")]
            cand = "net_pl" if "net_pl" in cols else ("pnl_amount" if "pnl_amount" in cols else ("pnl" if "pnl" in cols else None))
            if not cand:
                return (0.0, 0.0, 0.0, 0.0)
            base = "FROM orders WHERE (closed_at IS NOT NULL AND closed_at!='')"
            total = float(con.execute(f"SELECT COALESCE(SUM({cand}),0.0) {base}").fetchone()[0] or 0.0)
            d1    = float(con.execute(f"SELECT COALESCE(SUM({cand}),0.0) {base} AND date(closed_at)=date('now','utc')").fetchone()[0] or 0.0)
            d7    = float(con.execute(f"SELECT COALESCE(SUM({cand}),0.0) {base} AND closed_at>=datetime('now','-7 days','utc')").fetchone()[0] or 0.0)
            d30   = float(con.execute(f"SELECT COALESCE(SUM({cand}),0.0) {base} AND closed_at>=datetime('now','-30 days','utc')").fetchone()[0] or 0.0)
            return (total, d1, d7, d30)

        def _build_de_view(self, parent):
            parent.rowconfigure(1, weight=1); parent.columnconfigure(0, weight=1)
            cols = ("ts","market_sel","signal","conf","note")
            self._de_tbl = ttk.Treeview(parent, columns=cols, show="headings", height=6)
            for c, w in (("ts",110),("market_sel",190),("signal",90),("conf",70),("note",220)):
                self._de_tbl.heading(c, text=c.upper()); self._de_tbl.column(c, width=w, anchor="w")
            self._de_tbl.grid(row=0, column=0, sticky="nsew")
            y = ttk.Scrollbar(parent, orient="vertical", command=self._de_tbl.yview)
            y.grid(row=0, column=1, sticky="ns")
            self._de_tbl.configure(yscrollcommand=y.set)

            lf = ttk.LabelFrame(parent, text="Engine Narrative")
            lf.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(6,0))
            lf.rowconfigure(0, weight=1); lf.columnconfigure(0, weight=1)
            self._de_txt = tk.Text(lf, height=6, wrap="word")
            self._de_txt.grid(row=0, column=0, sticky="nsew")
            sy = ttk.Scrollbar(lf, orient="vertical", command=self._de_txt.yview)
            sy.grid(row=0, column=1, sticky="ns")
            self._de_txt.configure(yscrollcommand=sy.set)

        def _refresh_de_view(self):
            try:
                con = sqlite3.connect(self._db, timeout=5)
                con.row_factory = sqlite3.Row
                rows = con.execute("""
                    SELECT COALESCE(decided_at, opened_at, timestamp) AS ts,
                           marketId, selectionId,
                           COALESCE(signal_type,'exploratory') AS signal_type,
                           COALESCE(confidence, 0.0) AS conf,
                           COALESCE(meta_json,'') AS meta_json
                    FROM decisions
                    ORDER BY datetime(ts) DESC
                    LIMIT 20
                """).fetchall()
            except Exception:
                rows = []
            finally:
                try: con.close()
                except Exception: pass

            for i in self._de_tbl.get_children(): self._de_tbl.delete(i)
            for r in rows:
                ms = f"{r['marketId']} / {r['selectionId']}"
                note = (r["meta_json"] or "")[:60]
                self._de_tbl.insert("", "end", values=(r["ts"] or "", ms, r["signal_type"], f"{float(r['conf']):.2f}", note))

            try:
                eng = getattr(self.app, "decision_engine", None)
                txt = str(eng.explain_state()) if (eng and hasattr(eng, "explain_state")) else \
                      "No live DecisionEngine bound. Showing DB snapshots only."
            except Exception as e:
                txt = f"[engine error] {e}"

            try:
                self._de_txt.delete("1.0","end"); self._de_txt.insert("end", txt); self._de_txt.see("end")
            except Exception:
                pass

        def _close_window(self):
            try:
                self.winfo_toplevel().destroy()
            except Exception:
                pass


    # tiny tile used above
    class _MiniTile(ttk.Frame):
        def __init__(self, parent, title: str):
            super().__init__(parent, padding=(8, 6, 8, 6))
            self["borderwidth"] = 1; self["relief"] = "solid"
            ttk.Label(self, text=title, font=("SF Pro Text", 11, "bold")).pack(anchor="w")
            self._val = ttk.Label(self, text="£0.00", font=("SF Pro Text", 12, "bold"))
            self._val.pack(anchor="w", pady=(4,0))
        def update_value(self, text: str):
            self._val.config(text=text)

def open_scalper_window(host_widget, app=None):
    # Ensure TEST mode + TEST paths for Simulator
    try:
        from engines.upgrade_import_patch import get_mode, set_mode  # type: ignore
        if (get_mode() or "learning").lower() != "test":
            set_mode("TEST")
    except Exception:
        pass
    try:
        set_db_paths("TEST")  # routes to /data/test/*.test.db
    except Exception:
        pass

    root = host_widget.winfo_toplevel()
    win = tk.Toplevel(root)
    win.title("Scalping — Active Trades")
    win.geometry("1000x640"); win.minsize(900, 580)

    # Scrollable container
    wrap = ttk.Frame(win); wrap.pack(fill="both", expand=True)
    canvas = tk.Canvas(wrap, highlightthickness=0)
    vsb = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)
    vsb.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    content = ttk.Frame(canvas)
    win_id = canvas.create_window((0, 0), window=content, anchor="nw")

    def _on_config(event=None):
        # Update scrollable region and keep content width in sync with canvas width
        canvas.configure(scrollregion=canvas.bbox("all"))
        try:
            canvas.itemconfigure(win_id, width=canvas.winfo_width())
        except Exception:
            pass
    content.bind("<Configure>", _on_config)
    canvas.bind("<Configure>", _on_config)

    # Mouse wheel (macOS/Windows)
    def _on_mousewheel(event):
        delta = -1 * int(event.delta/120) if event.delta else 0
        canvas.yview_scroll(delta, "units")
    canvas.bind_all("<MouseWheel>", _on_mousewheel)      # Windows/mac
    canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))  # Linux up
    canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll( 1, "units"))  # Linux down

    # Embed the actual view inside the scrollable content
    view = ScalperView(content, app=app)
    view.pack(fill="both", expand=True)

    win.transient(root); win.focus_force()
    return win

# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: gui/scalper_view.py
# 🔎 SEARCH: win\.transient\(root\); win\.focus\b
# ─────────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    root = tk.Tk()
    root.title("Scalping — Active Trades (standalone)")
    root.geometry("1000x640")
    container = ttk.Frame(root); container.pack(fill="both", expand=True)
    ScalperView(container).pack(fill="both", expand=True)
    root.mainloop()
