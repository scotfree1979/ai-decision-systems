#!/usr/bin/env python3
# ===============================================================
# AutoScalp Mastery Dashboard v7 – Phase 7B Dynamic Refactor
# ===============================================================
# Adds:
#   • Global KPI bar (7 metrics)
#   • Persistent right-hand System Control Hub
#   • Responsive left main content (7 tabs)
#   • Teal animated bars (2-second tick)
# ===============================================================

from __future__ import annotations
if __name__ == "__main__" and __package__ is None:
    import sys, os
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


import tkinter as tk
from tkinter import ttk
import random, datetime, math

from gui.dashboard_data import (
    kpi_tiles,
    portfolio_risk,
)


TEAL = "#00bcd4"
BG_DARK = "#1e1e1e"
CARD_BG = "#f5f5f5"
INNER_BG = "#ffffff"

class DBConnection:
    _shared_con = None

    @classmethod
    def get(cls):
        import sqlite3
        if cls._shared_con is None:
            cls._shared_con = sqlite3.connect(
                "file:data/autoscalp_gui.db?mode=ro&cache=shared",
                uri=True,
                check_same_thread=False
            )
            cls._shared_con.row_factory = sqlite3.Row

            # --- Performance pragmas ---
            cls._shared_con.execute("PRAGMA journal_mode=WAL;")
            cls._shared_con.execute("PRAGMA cache_size=-10000;")   # 10 MB cache
            cls._shared_con.execute("PRAGMA synchronous=OFF;")
            cls._shared_con.execute("PRAGMA temp_store=MEMORY;")

        return cls._shared_con



    @classmethod
    def get(cls):
        import sqlite3
        if cls._shared_con is None:
            cls._shared_con = sqlite3.connect(
                "file:data/autoscalp_gui.db?mode=ro&cache=shared",
                uri=True,
                check_same_thread=False
            )
            cls._shared_con.row_factory = sqlite3.Row
            cls._shared_con.execute("PRAGMA journal_mode=WAL;")
        return cls._shared_con

def keep_tk_alive(widget):
    """Keep the Tk event loop processing during heavy work."""
    widget.update_idletasks()
    widget.update()

import threading

def run_in_thread(target, callback=None):
    """
    Run `target()` in a background thread.
    When it completes, call `callback(result)` safely on the main thread.
    """
    def runner():
        try:
            result = target()
        except Exception as e:
            result = e
        # Call back on the Tk thread
        if callback:
            # callback will be called inside Tk's event loop
            try:
                tk._default_root.after(0, lambda: callback(result))
            except Exception:
                pass
    threading.Thread(target=runner, daemon=True).start()


# === PATCH START ===
# 📍 TARGET: gui/mastery_dashboard_v7/mastery_dashboard_v7.py
# 📆 PATCHED: 2025-11-02Z — Global auto-resize fix for all canvases
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _bind_canvas_autosize(canvas, inner_frame):
    """
    Keep a canvas sized to its inner scroll frame.
    Attach once right after create_window().
    Works for every _tab_* canvas automatically.
    """
    def _on_config(event):
        # resize canvas to match inner frame’s visible area
        bbox = canvas.bbox("all")
        if bbox:
            canvas.configure(scrollregion=bbox)
        # ensure width/height track inner frame dynamically
        canvas.itemconfig(inner_frame, width=event.width)
    canvas.bind(
        "<Configure>",
        lambda e: _on_config(e)
    )
# === PATCH END ===
def _enable_smooth_scroll(canvas):
    """Enable macOS two-finger / touchpad scrolling for a canvas."""
    def _on_mousewheel(event):
        # macOS uses event.delta, Linux uses Button-4/5
        if event.delta:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        elif event.num == 4:
            canvas.yview_scroll(-3, "units")
        elif event.num == 5:
            canvas.yview_scroll(3, "units")
    # Bind for macOS/Windows
    canvas.bind_all("<MouseWheel>", _on_mousewheel)
    # Bind for Linux (Button-4/5 events)
    canvas.bind_all("<Button-4>", _on_mousewheel)
    canvas.bind_all("<Button-5>", _on_mousewheel)


# ---------- helper: generic light card ----------
def make_card(parent, title=None, height=220):
    card = tk.Frame(parent, bg=CARD_BG, highlightbackground="#cccccc",
                    highlightthickness=1, bd=0)
    if title:
        tk.Label(card, text=title, bg=CARD_BG, fg="#222222",
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=12, pady=(8,0))
    inner = tk.Frame(card, bg=INNER_BG, height=height)
    inner.pack(fill="both", expand=True, padx=12, pady=(4,10))
    card.pack(fill="x", padx=16, pady=10)
 
    return inner

# ---------- helper: teal bar with label ----------
def teal_bar(parent, label, init=0):
    row = tk.Frame(parent, bg=INNER_BG)
    row.pack(fill="x", pady=3)
    tk.Label(row, text=label, bg=INNER_BG, fg="#333", width=22,
             anchor="w").pack(side="left")
    val = tk.IntVar(value=init)
    bar = ttk.Progressbar(row, length=200, mode="determinate",
                          maximum=100, variable=val)
    style = ttk.Style()
    style.configure("Teal.Horizontal.TProgressbar", troughcolor="#e6e6e6",
                    background=TEAL, bordercolor="#cccccc", thickness=12)
    bar.configure(style="Teal.Horizontal.TProgressbar")
    bar.pack(side="left", padx=6)
    lbl = tk.Label(row, text=f"{init} %", bg=INNER_BG, fg="#333")
    lbl.pack(side="left")
    return bar, val, lbl

def _query_overview_snapshot():
    """Aggregate live metrics from key v7 intelligence views."""
    con = DBConnection.get()
    out = {}

    # 1️⃣ Strategy Evolution (posterior drift & pnl)
    q1 = """
    SELECT 
        ROUND(AVG(p1_mean - p2_mean),3) AS drift_mean,
        ROUND(AVG(pnl_today),2) AS avg_pnl,
        (SELECT strategy FROM v_strategy_perf ORDER BY pnl_today DESC LIMIT 1) AS top_strategy,
        (SELECT strategy FROM v_strategy_perf ORDER BY pnl_today ASC  LIMIT 1) AS weak_strategy
    FROM v_mastery_live JOIN v_strategy_perf;
    """
    out["strategy"] = dict(con.execute(q1).fetchone() or {})

    # 2️⃣ Letter Learning & Plans
    q2 = """
    SELECT 
        letter,
        ROUND(AVG(pnl),2) AS pnl,
        ROUND(AVG(success)*100,1) AS win_pct
    FROM v_mastery_intel_v7
    GROUP BY letter
    ORDER BY letter;
    """
    out["letters"] = [dict(r) for r in con.execute(q2).fetchall()]
    q3 = """
    SELECT 
        status, COUNT(*) AS n
    FROM v_plan_ledger_compat
    GROUP BY status;
    """
    out["plans"] = {r["status"]: r["n"] for r in con.execute(q3).fetchall()}

    # 3️⃣ Training Flow
    q4 = """
    SELECT 
        bucket,
        ROUND(AVG(value)*100,1) AS pct
    FROM mastery_training_metrics
    WHERE ts >= datetime('now','-7 day','utc')
    GROUP BY bucket
    LIMIT 3;
    """
    out["training"] = [dict(r) for r in con.execute(q4).fetchall()]

    # 4️⃣ Race Intelligence
    q5 = """
    SELECT 
        ROUND(AVG(inplay_progress),2) AS progress,
        ROUND(AVG(drift_speed),2) AS drift,
        ROUND(AVG(success)*100,1) AS win
    FROM v_mastery_intel_v7;
    """
    out["race"] = dict(con.execute(q5).fetchone() or {})

    # 5️⃣ Global snapshot
    out["score"] = {
        "posterior_drift": out["strategy"].get("drift_mean", 0),
        "learning_eff": _safe_avg([t["pct"] for t in out["training"]]) if out["training"] else 0,
        "avg_pnl": out["strategy"].get("avg_pnl", 0),
        "race_win": out["race"].get("win", 0),
    }
    return out


# ===============================================================
class MasteryDashboardV7(tk.Tk):
    """Phase 7B unified dashboard (KPI bar + sidebar + tabs)."""
    def __init__(self):
        super().__init__()
        self.title("AutoScalp Mastery Dashboard v7")
        self.geometry("1400x900")
        self.configure(bg=BG_DARK)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.dynamic_bars = []
        self._build_header()
        self._build_body()
        self._build_footer()
        self._animate_bars()
        # Initial KPI population (after UI is ready)
        self.after(100, self._refresh_kpis)
        self.after(1000, self._refresh_tab_overview)
        self.after(1000, self._refresh_tab_strategy)
        self.after(1000, self._refresh_tab_learning)
        self.after(1000, self._refresh_tab_progress)
    # ===========================================================
    def _build_header(self):
        # main title strip
        h = tk.Frame(self, bg="#262626", height=48)
        h.pack(fill="x", side="top")
        tk.Label(h, text="AutoScalp Mastery Dashboard v7",
                 fg="#e0e0e0", bg="#262626",
                 font=("Segoe UI", 16, "bold")).pack(side="left", padx=10)
        tk.Button(h, text="🔄 Refresh", command=self._refresh_all,
                  bg="#d9d9d9", fg="black").pack(side="right", padx=10)

   
        # ─────────────────────────────────────────────
        # KPI TILES (Dashboard v2)
        # ─────────────────────────────────────────────
        kpi_row = tk.Frame(self, bg=BG_DARK)
        kpi_row.pack(fill="x", padx=10, pady=6)

        def _kpi_box(title):
            box = tk.Frame(
                kpi_row,
                bg=CARD_BG,
                highlightbackground="#ccc",
                highlightthickness=1,
                padx=10,
                pady=6
            )
            box.pack(side="left", expand=True, fill="both", padx=4)

            tk.Label(
                box,
                text=title,
                bg=CARD_BG,
                fg="#333",
                font=("Segoe UI", 10, "bold")
            ).pack()

            val = tk.Label(
                box,
                text="—",
                bg=CARD_BG,
                fg="#000",
                font=("Segoe UI", 14, "bold")
            )
            val.pack()

            return val

        self.kpi_total_pnl   = _kpi_box("Total P&L")
        self.kpi_win_rate    = _kpi_box("Win Rate")
        self.kpi_markets     = _kpi_box("Markets")
        self.kpi_trades      = _kpi_box("Trades Today")
        self.kpi_conf_avg    = _kpi_box("Conf Avg")



    # === PATCH START ===
    # 📍 TARGET: gui/mastery_dashboard_v7/mastery_dashboard_v7.py:_build_body
    # 📆 PATCHED: 2025-11-02Z — Right-align sidebar + global dynamic stretch
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def _build_body(self):
        """Main body layout — left tabs + right sidebar."""
        body = tk.Frame(self, bg=BG_DARK)
        body.pack(fill="both", expand=True)

        body.columnconfigure(0, weight=5)
        body.columnconfigure(1, weight=0)
        body.rowconfigure(0, weight=1)

        # LEFT (main notebook)
        left = tk.Frame(body, bg=BG_DARK)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self._build_tabs(left)  # ✅ notebook attaches here visibly

        # RIGHT (sidebar)
        sidebar = tk.Frame(body, bg="#111", width=300)
        sidebar.grid(row=0, column=1, sticky="ns")
        self._build_sidebar(sidebar)



    def _build_sidebar(self, parent):
        """Simple persistent right sidebar."""
        sections = {
            "Analytics": ["Canonical Report", "7-day Trend", "30-day Trend"],
            "Learning": ["Run Replay", "Replay Digest", "Consolidate", "Train Now"],
            "Execution": ["Full Cycle", "Resume Replay", "Stop Execution"],
            "Git": ["Check Branch", "Commit All", "Push Branch", "Create Tag"],
            "Backups": ["Manual Backup", "Diff Check", "Export Sprint"]
        }

        for name, buttons in sections.items():
            box = tk.LabelFrame(parent, text=name, bg="#111", fg="#ddd",
                                labelanchor="n", font=("Segoe UI", 10, "bold"),
                                padx=6, pady=6)
            box.pack(fill="x", padx=8, pady=6)
            for b in buttons:
                tk.Button(box, text=b, width=24, bg="#d9d9d9",
                          relief="raised").pack(pady=3)


    # ===========================================================
    def _build_footer(self):
        f = tk.Frame(self, bg="#262626", height=30)
        f.pack(fill="x", side="bottom")
        tk.Label(f, text="Phase 7B – Dynamic Layout Demo (dummy data)",
                 fg="#aaaaaa", bg="#262626", font=("Segoe UI", 9)
        ).pack(side="left", padx=10)
        tk.Button(f, text="⏹ Close", command=self._on_close,
                  bg="#ff6666", fg="black").pack(side="right", padx=10)

    # ===========================================================
    # === PATCH START ===
    # 📍 TARGET: gui/mastery_dashboard_v7/mastery_dashboard_v7.py:_build_tabs
    # 📆 PATCHED: 2025-11-02Z — ensure notebook is packed into left frame
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def _build_tabs(self, parent):
        nb = ttk.Notebook(parent)
        self.bind_all("<MouseWheel>", lambda e: self.event_generate("<MouseWheel>", delta=e.delta))

        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.tabs = {}
        for name in [
            "Overview",
            "Strategy",
            "Learning",
            "Progress",
            "IQ",
            "Race IQ"
        ]:
            f = tk.Frame(nb, bg=BG_DARK)
            nb.add(f, text=name)
            self.tabs[name] = f

        self._tab_overview()
        self._tab_strategy()
        self._tab_learning()
        self._tab_progress()
        self._tab_intelligence()
        self._tab_race()



    # ===============================================================
    # TAB 1 – OVERVIEW / DIGEST
    def _tab_overview(self):
        f = self.tabs["Overview"]

        canvas = tk.Canvas(f, bg=BG_DARK, highlightthickness=0)
        vsb = ttk.Scrollbar(f, orient="vertical", command=canvas.yview)
        scroll = tk.Frame(canvas, bg=BG_DARK)

        window_id = canvas.create_window((0, 0), window=scroll, anchor="nw")
        _bind_canvas_autosize(canvas, window_id)
        _enable_smooth_scroll(canvas)

        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        con = DBConnection.get()

        # ==========================================================
        # EXECUTION SUMMARY
        # ==========================================================
        card = make_card(scroll, "Execution Summary")

        try:
            row = con.execute("""
                SELECT
                    parents_open,
                    matched_exposure,
                    net_pl_total,
                    net_pl_open,
                    last_activity_ts
                FROM v_dash_overview_execution
            """).fetchone()
            r = dict(row) if row else {}
        except Exception:
            r = {}


        rows = [
            ("Open Parents",        r.get("parents_open", 0)),
            ("Matched Exposure",    f"£{float(r.get('matched_exposure') or 0):,.2f}"),
            ("Total P&L",           f"£{float(r.get('net_pl_total') or 0):,.2f}"),
            ("Open P&L",            f"£{float(r.get('net_pl_open') or 0):,.2f}"),
            ("Last Activity",       r.get("last_activity_ts", "—")),
        ]

        for k, v in rows:
            row = tk.Frame(card, bg=INNER_BG)
            row.pack(fill="x", padx=12, pady=3)
            tk.Label(row, text=k, bg=INNER_BG, fg="#333",
                     width=24, anchor="w").pack(side="left")
            tk.Label(row, text=str(v), bg=INNER_BG, fg="#000",
                     font=("Segoe UI", 10, "bold")).pack(side="left")

        # ==========================================================
        # STRATEGY / ENGINE PERFORMANCE
        # ==========================================================
        strat = make_card(scroll, "Strategy Performance")

        cols = (
            "Engine",
            "Parents",
            "Open",
            "Exposure £",
            "Net P&L",
            "Last Trade",
        )

        table = ttk.Treeview(strat, columns=cols, show="headings", height=8)

        for c in cols:
            table.heading(c, text=c)
            table.column(c, width=160 if c == "Engine" else 120)

        table.pack(fill="both", expand=True, padx=8, pady=6)

        try:
            rows = con.execute("""
                SELECT
                    engine,
                    parents_total,
                    parents_open,
                    matched_exposure,
                    net_pl,
                    last_trade_ts
                FROM v_dash_overview_strategy
                ORDER BY net_pl DESC
            """).fetchall()
        except Exception:
            rows = []

        for r in rows:
            table.insert(
                "",
                "end",
                values=(
                    r["engine"] or "—",
                    r["parents_total"],
                    r["parents_open"],
                    f"£{float(r['matched_exposure'] or 0):,.2f}",
                    f"£{float(r['net_pl'] or 0):,.2f}",
                    r["last_trade_ts"] or "—",
                ),
            )

        # ==========================================================
        # SYSTEM HEALTH SNAPSHOT
        # ==========================================================
        health = make_card(scroll, "System Health Snapshot")

        try:
            row = con.execute("""
                SELECT
                    orders_total,
                    parents_placed,
                    children_placed,
                    stop_losses,
                    greened_up,
                    open_parents
                FROM v_dash_overview_health
            """).fetchone()
            r = dict(row) if row else {}
        except Exception:
            r = {}


        rows = [
            ("Orders",        r.get("orders_total", 0)),
            ("Parents",       r.get("parents_placed", 0)),
            ("Children",      r.get("children_placed", 0)),
            ("Stop Losses",   r.get("stop_losses", 0)),
            ("Greened",       r.get("greened_up", 0)),
            ("Open Parents",  r.get("open_parents", 0)),
        ]

        for k, v in rows:
            row = tk.Frame(health, bg=INNER_BG)
            row.pack(fill="x", padx=12, pady=3)
            tk.Label(row, text=k, bg=INNER_BG, fg="#333",
                     width=24, anchor="w").pack(side="left")
            tk.Label(row, text=str(v), bg=INNER_BG, fg="#000",
                     font=("Segoe UI", 10, "bold")).pack(side="left")


    # ===============================================================
    # TAB 2 – STRATEGY & POSTERIOR EVOLUTION  (v7 Intelligence Wireframe)
    def _tab_strategy(self):
        f = self.tabs["Strategy"]

        canvas = tk.Canvas(f, bg=BG_DARK, highlightthickness=0)
        vsb = ttk.Scrollbar(f, orient="vertical", command=canvas.yview)
        scroll = tk.Frame(canvas, bg=BG_DARK)

        window_id = canvas.create_window((0, 0), window=scroll, anchor="nw")
        _bind_canvas_autosize(canvas, window_id)
        _enable_smooth_scroll(canvas)

        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        con = DBConnection.get()

        # ==========================================================
        # ENGINE EXECUTION PERFORMANCE
        # ==========================================================
        card = make_card(scroll, "Execution by Engine")

        cols = (
            "Engine",
            "Parents",
            "Open",
            "Matched £",
            "Net P&L",
            "Last Trade",
        )

        tv = ttk.Treeview(card, columns=cols, show="headings", height=8)
        for c in cols:
            tv.heading(c, text=c)
            tv.column(c, width=140 if c != "Engine" else 160)

        tv.pack(fill="both", expand=True, padx=8, pady=6)

        rows = con.execute("""
            SELECT
                engine,
                parents_total,
                parents_open,
                matched_exposure,
                net_pl_total,
                last_trade_ts
            FROM v_dash_strategy_exec_engine
            ORDER BY net_pl_total DESC
        """).fetchall()

        for r in rows:
            tv.insert(
                "",
                "end",
                values=(
                    r["engine"],
                    r["parents_total"],
                    r["parents_open"],
                    f"£{r['matched_exposure']:.2f}",
                    f"£{r['net_pl_total']:.2f}",
                    r["last_trade_ts"] or "—",
                ),
            )

        # ==========================================================
        # INTELLIGENCE BY LETTER
        # ==========================================================
        intel = make_card(scroll, "Intelligence by Letter")

        cols = (
            "Letter",
            "Trades",
            "Win %",
            "Avg P&L",
            "Total P&L",
            "Avg Drift",
        )

        tv2 = ttk.Treeview(intel, columns=cols, show="headings", height=10)
        for c in cols:
            tv2.heading(c, text=c)
            tv2.column(c, width=120)

        tv2.pack(fill="both", expand=True, padx=8, pady=6)

        rows = con.execute("""
            SELECT
                letter,
                trades,
                win_pct,
                avg_pnl,
                total_pnl,
                avg_drift
            FROM v_dash_strategy_intel_letter
            ORDER BY total_pnl DESC
        """).fetchall()

        for r in rows:
            tv2.insert(
                "",
                "end",
                values=(
                    r["letter"] or "∅",
                    r["trades"],
                    f"{r['win_pct']:.1f}%",
                    f"£{float(r['avg_pnl'] or 0.0):.2f}",                    f"£{float(r['total_pnl'] or 0.0):.2f}",
                    f"{r['avg_drift']:+.4f}",
                ),
            )

        # ==========================================================
        # POSTERIOR / BIN STATE (NO P&L)
        # ==========================================================
        post = make_card(scroll, "Posterior / Bin State")

        cols = (
            "Bucket",
            "Mean Conf",
            "Bucket Conf",
            "Δ Conf",
            "Samples",
        )

        tv3 = ttk.Treeview(post, columns=cols, show="headings", height=8)
        for c in cols:
            tv3.heading(c, text=c)
            tv3.column(c, width=160 if c == "Bucket" else 120)

        tv3.pack(fill="both", expand=True, padx=8, pady=6)

        rows = con.execute("""
            SELECT
                bucket,
                mean_confidence,
                mean_bucket_confidence,
                delta_confidence,
                sample_count
            FROM v_dash_strategy_posterior
            ORDER BY sample_count DESC
        """).fetchall()

        for r in rows:
            tv3.insert(
                "",
                "end",
                values=(
                    r["bucket"],
                    f"{r['mean_confidence']:.4f}",
                    f"{r['mean_bucket_confidence']:.4f}",
                    f"{r['delta_confidence']:+.4f}",
                    r["sample_count"],
                ),
            )


    # ===============================================================
    # TAB 3 – LEARNING STREAM & PLAN LEDGER  (v7 Mastery Learning Intelligence)
    def _tab_learning(self):
        f = self.tabs["Learning"]

        canvas = tk.Canvas(f, bg=BG_DARK, highlightthickness=0)
        vsb = ttk.Scrollbar(f, orient="vertical", command=canvas.yview)
        scroll = tk.Frame(canvas, bg=BG_DARK)

        window_id = canvas.create_window((0, 0), window=scroll, anchor="nw")
        _bind_canvas_autosize(canvas, window_id)
        _enable_smooth_scroll(canvas)

        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        con = DBConnection.get()

        # ==========================================================
        # LEARNING THROUGHPUT
        # ==========================================================
        card = make_card(scroll, "Learning Throughput")

        r = con.execute("""
            SELECT decisions_total, markets_seen, win_pct, avg_pnl
            FROM v_dash_learning_throughput
        """).fetchone() or {}

        rows = [
            ("Decisions", int(r["decisions_total"] or 0)),
            ("Markets Seen", int(r["markets_seen"] or 0)),
            ("Win %", f"{(r['win_pct'] or 0) * 100:.1f}%"),
            ("Avg PnL", f"£{r['avg_pnl'] or 0:.2f}")
        ]

        for k, v in rows:
            row = tk.Frame(card, bg=INNER_BG)
            row.pack(fill="x", padx=12, pady=3)
            tk.Label(row, text=k, bg=INNER_BG, width=22, anchor="w").pack(side="left")
            tk.Label(row, text=str(v), bg=INNER_BG, font=("Segoe UI", 10, "bold")).pack(side="left")

        # ==========================================================
        # LETTER LEARNING OUTCOMES
        # ==========================================================
        card = make_card(scroll, "Letter Learning Outcomes")

        cols = ("Letter", "Trades", "Win %", "Avg PnL", "Total PnL", "Avg Drift")
        t = ttk.Treeview(card, columns=cols, show="headings", height=8)

        for c in cols:
            t.heading(c, text=c)
            t.column(c, width=120)

        t.pack(fill="both", expand=True, padx=8, pady=6)

        for r in con.execute("""
            SELECT letter, trades, win_pct, avg_pnl, total_pnl, avg_drift
            FROM v_dash_learning_letter_outcomes
            ORDER BY total_pnl DESC
        """):
            t.insert(
                "",
                "end",
                values=(
                    r["letter"] or "—",
                    r["trades"],
                    f"{r['win_pct']:.1f}%",
                    f"£{float(r['avg_pnl'] or 0.0):.2f}",                    
                    f"£{float(r['total_pnl'] or 0.0):.2f}",
                    f"{r['avg_drift']:+.3f}"
                )
            )

        # ==========================================================
        # BUCKET LEARNING STATE
        # ==========================================================
        card = make_card(scroll, "Learning Bucket State")

        cols = ("Bucket", "Mean Conf", "Bucket Conf", "Δ", "Samples")
        t = ttk.Treeview(card, columns=cols, show="headings", height=6)

        for c in cols:
            t.heading(c, text=c)
            t.column(c, width=160 if c == "Bucket" else 110)

        t.pack(fill="both", expand=True, padx=8, pady=6)

        for r in con.execute("""
            SELECT bucket, mean_confidence, mean_bucket_confidence,
                   delta_confidence, sample_count
            FROM v_dash_learning_bucket_state
            ORDER BY sample_count DESC
        """):
            t.insert(
                "",
                "end",
                values=(
                    r["bucket"],
                    f"{r['mean_confidence']:.3f}",
                    f"{r['mean_bucket_confidence']:.3f}",
                    f"{r['delta_confidence']:+.3f}",
                    r["sample_count"]
                )
            )

        # ==========================================================
        # PLAN LEDGER STATE
        # ==========================================================
        card = make_card(scroll, "Plan Ledger State")

        for r in con.execute("""
            SELECT status, plans
            FROM v_dash_learning_plan_state
            ORDER BY plans DESC
        """):
            row = tk.Frame(card, bg=INNER_BG)
            row.pack(fill="x", padx=12, pady=3)
            tk.Label(row, text=r["status"], width=22, anchor="w", bg=INNER_BG).pack(side="left")
            tk.Label(row, text=str(r["plans"]), bg=INNER_BG,
                     font=("Segoe UI", 10, "bold")).pack(side="left")

        # ==========================================================
        # LEARNING SIGNAL STREAM
        # ==========================================================
        card = make_card(scroll, "Recent Learning Signals")

        cols = ("Day", "Letter", "Success", "PnL")
        t = ttk.Treeview(card, columns=cols, show="headings", height=10)

        for c in cols:
            t.heading(c, text=c)
            t.column(c, width=120)

        t.pack(fill="both", expand=True, padx=8, pady=6)

        for r in con.execute("""
            SELECT signal_day, letter, success, pnl
            FROM v_dash_learning_signal_stream
            ORDER BY signal_day DESC
        """):
            t.insert(
                "",
                "end",
                values=(
                    r["signal_day"],
                    r["letter"] or "—",
                    "✓" if r["success"] else "✕",
                    "—" if r["pnl"] is None else f"£{r['pnl']:.2f}"
                )
            )



    # ===============================================================
    # TAB 4 – PROGRESS & PLAYBOOKS  (Phase 7C Wireframe Integration)
    # ===============================================================
    def _tab_progress(self):
        f = self.tabs["Progress"]

        canvas = tk.Canvas(f, bg=BG_DARK, highlightthickness=0)
        vsb = ttk.Scrollbar(f, orient="vertical", command=canvas.yview)
        scroll = tk.Frame(canvas, bg=BG_DARK)

        window_id = canvas.create_window((0, 0), window=scroll, anchor="nw")
        _bind_canvas_autosize(canvas, window_id)
        _enable_smooth_scroll(canvas)

        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        from gui.dashboard_data import _df
        con = DBConnection.get()

        # ==========================================================
        # PLAYBOOK BUILD STATUS
        # ==========================================================
        pb = make_card(scroll, "Playbook Build Status")

        try:
            rows = _df(con, """
                SELECT
                    COUNT(*)                          AS rows,
                    COUNT(DISTINCT blueprint_key)     AS blueprints,
                    COUNT(DISTINCT day)               AS days,
                    ROUND(AVG(net_pl),2)              AS avg_pnl
                FROM playbooks_settled
            """)
            r = rows[0] if rows else {}
        except Exception:
            r = {}

        stats = [
            ("Total Rows",    str(int(r.get("rows", 0)))),
            ("Blueprints",    str(int(r.get("blueprints", 0)))),
            ("Days Covered",  str(int(r.get("days", 0)))),
            ("Avg PnL",       f"£{r.get('avg_pnl', 0):.2f}"),
        ]

        for k, v in stats:
            row = tk.Frame(pb, bg=INNER_BG)
            row.pack(fill="x", padx=12, pady=4)
            tk.Label(row, text=k, bg=INNER_BG, fg="#333", width=22, anchor="w").pack(side="left")
            tk.Label(row, text=v, bg=INNER_BG, fg="#000", font=("Segoe UI", 10, "bold")).pack(side="left")

        # ==========================================================
        # PLAYBOOK PERFORMANCE
        # ==========================================================
        perf = make_card(scroll, "Playbook Performance")

        cols = ("Blueprint", "Trades", "Win %", "PnL")
        table = ttk.Treeview(perf, columns=cols, show="headings", height=10)

        for c in cols:
            table.heading(c, text=c)
            table.column(c, width=160)

        table.pack(fill="both", expand=True, padx=8, pady=6)

        try:
            rows = _df(con, """
                SELECT
                    blueprint_key,
                    COUNT(*)                    AS n,
                    ROUND(AVG(success)*100,1)   AS win_pct,
                    ROUND(SUM(net_pl),2)        AS pnl
                FROM playbooks_settled
                GROUP BY blueprint_key
                ORDER BY pnl DESC
                LIMIT 50
            """)
        except Exception:
            rows = []

        for r in rows:
            table.insert(
                "",
                "end",
                values=(
                    r["blueprint_key"],
                    r["n"],
                    f"{r['win_pct']:.1f}%",
                    f"£{r['pnl']:.2f}",
                ),
            )

        # ==========================================================
        # RECENT PLAYBOOK ACTIVITY
        # ==========================================================
        recent = make_card(scroll, "Recent Playbook Activity")

        cols = ("Day", "Blueprint", "Result", "PnL")
        table2 = ttk.Treeview(recent, columns=cols, show="headings", height=10)

        for c in cols:
            table2.heading(c, text=c)
            table2.column(c, width=160)

        table2.pack(fill="both", expand=True, padx=8, pady=6)

        try:
            rows = _df(con, """
                SELECT
                    day,
                    blueprint_key,
                    outcome_bin,
                    ROUND(net_pl,2) AS pnl
                FROM playbooks_settled
                ORDER BY day DESC
                LIMIT 50
            """)
        except Exception:
            rows = []

        for r in rows:
            table2.insert(
                "",
                "end",
                values=(
                    r["day"],
                    r["blueprint_key"],
                    r["outcome_bin"],
                    f"£{r['pnl']:.2f}",
                ),
            )

    # ===============================================================
    # TAB 5 – MASTER & HORSE INTELLIGENCE
    def _tab_intelligence(self):
        f = self.tabs["IQ"]

        # === Scrollable container (same pattern as other tabs) ===
        canvas = tk.Canvas(f, bg=BG_DARK, highlightthickness=0)
        vsb = ttk.Scrollbar(f, orient="vertical", command=canvas.yview)
        scroll = tk.Frame(canvas, bg=BG_DARK)
        window_id = canvas.create_window((0, 0), window=scroll, anchor="nw")
        _bind_canvas_autosize(canvas, window_id)
        _enable_smooth_scroll(canvas)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        scroll.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        # ── HORSE FORM GROUP SUMMARY (left) ────────────────────────────────
        form_summary = make_card(scroll, "Horse Form Group Summary (v_runner_form_groups)")
        cols_f = ("Form Class", "Horses", "Avg Win %", "Avg Odds", "Avg Win Rate")
        tf = ttk.Treeview(form_summary, columns=cols_f, show="headings", height=6)
        for c0 in cols_f:
            tf.heading(c0, text=c0)
            tf.column(c0, width=130)
        tf.pack(fill="both", expand=True, padx=8, pady=6)
        self.table_form = tf

        def _query_form_summary():
            # schema-verified: v_runner_form_groups(form_class, odds_band, fav_rank_bin,
            #                                       horses, avg_win_rate, avg_odds)
            sql = """
            SELECT 
                form_class,
                SUM(horses)                AS horses,
                ROUND(AVG(avg_win_rate)*100,2) AS avg_win_pct,
                ROUND(AVG(avg_odds),2)     AS avg_odds,
                ROUND(AVG(avg_win_rate),3) AS avg_win_rate
            FROM v_runner_form_groups
            GROUP BY form_class
            ORDER BY avg_win_pct DESC;
            """
            try:
                con = DBConnection.get()
                rows = con.execute(sql).fetchall()
                return [dict(r) for r in rows]
            except Exception as e:
                print(f"[dashboard] form summary warn: {e}")
                return []

        # ==========================================================
        # HORSE FORM → PnL ATTRIBUTION
        # ==========================================================
        pnl_form = make_card(scroll, "Horse Form → PnL Attribution")

        cols = ("Form Class", "Trades", "Win %", "PnL £")
        table_pnl = ttk.Treeview(pnl_form, columns=cols, show="headings", height=6)

        for c in cols:
            table_pnl.heading(c, text=c)
            table_pnl.column(c, width=160)

        table_pnl.pack(fill="both", expand=True, padx=8, pady=6)

        try:
            rows = _df(DBConnection.get(), """
                SELECT
                    f.form_class,
                    COUNT(*)                      AS trades,
                    ROUND(AVG(o.success)*100,1)  AS win_pct,
                    ROUND(SUM(o.net_pl),2)       AS pnl
                FROM v_runner_form_groups f
                JOIN playbooks_settled o
                  ON o.selectionId = f.selectionId
                GROUP BY f.form_class
                ORDER BY pnl DESC
            """)
        except Exception:
            rows = []

        for r in rows:
            table_pnl.insert(
                "",
                "end",
                values=(
                    r["form_class"],
                    r["trades"],
                    f"{r['win_pct']:.1f}%",
                    f"£{r['pnl']:.2f}",
                ),
            )


        # ── MASTERY V7 REINFORCEMENT (right) ───────────────────────────────
        reinforce = make_card(scroll, "Mastery v7 Reinforcement (v_mastery_intel_v7 + v_mastery_live)", height=260)
        tk.Label(
            reinforce,
            text="Aggregated bucket strength and stability (last 7 days)",
            bg=INNER_BG, fg="#333", font=("Segoe UI", 10, "italic")
        ).pack(anchor="w", padx=10, pady=(2, 6))

        cols_r = ("Distance Band", "Fav Rank", "Avg Success %", "Avg PnL £", "Stability Δ")
        tr = ttk.Treeview(reinforce, columns=cols_r, show="headings", height=8)
        for c0 in cols_r:
            tr.heading(c0, text=c0)
            tr.column(c0, width=130 if c0 != "Distance Band" else 150)
        tr.pack(fill="both", expand=True, padx=8, pady=4)
        self.table_reinforce = tr

        def _query_reinforce():
            # schema-verified: v_mastery_intel_v7(day, marketId, selectionId, letter,
            #   pnl, success, pre_drift, band_width, band_stability, ...)
            # and v_mastery_live(distance_band, fav_rank_bin, total_pnl, total_weight)
            sql = """
            WITH daily AS (
              SELECT 
                distance_band AS distance_band,
                fav_rank,
                ROUND(AVG(success)*100,2) AS avg_success,
                ROUND(AVG(pnl),2)          AS avg_pnl,
                ROUND(AVG(band_stability),3) AS stability
              FROM v_mastery_intel_v7
              GROUP BY distance_band, fav_rank
            )
            SELECT 
              d.distance_band,
              d.fav_rank,
              d.avg_success,
              d.avg_pnl,
              ROUND(d.stability - LAG(d.stability,1) OVER (ORDER BY d.distance_band),3) AS delta_stability
            FROM daily d;
            """
            try:
                con = DBConnection.get()
                rows = con.execute(sql).fetchall()
                return [dict(r) for r in rows]
            except Exception as e:
                print(f"[dashboard] reinforce query warn: {e}")
                return []

        # ==========================================================
        # MASTERY SIGNAL EFFECTIVENESS
        # ==========================================================
        mastery_eff = make_card(scroll, "Mastery Signal Effectiveness")

        cols = ("Signal Type", "Trades", "Win %", "Avg PnL £")
        table_me = ttk.Treeview(mastery_eff, columns=cols, show="headings", height=5)

        for c in cols:
            table_me.heading(c, text=c)
            table_me.column(c, width=160)

        table_me.pack(fill="both", expand=True, padx=8, pady=6)

        try:
            rows = _df(DBConnection.get(), """
                SELECT
                    CASE
                        WHEN confidence > 0 THEN 'With Mastery'
                        ELSE 'No Mastery'
                    END AS grp,
                    COUNT(*)                    AS trades,
                    ROUND(AVG(success)*100,1)  AS win_pct,
                    ROUND(AVG(pnl),2)          AS avg_pnl
                FROM v_mastery_intel_v7
                GROUP BY grp
            """)
        except Exception:
            rows = []

        for r in rows:
            table_me.insert(
                "",
                "end",
                values=(
                    r["grp"],
                    r["trades"],
                    f"{r['win_pct']:.1f}%",
                    f"£{float(r['avg_pnl'] or 0.0):.2f}",                ),
            )

        # ==========================================================
        # HORSE-LEVEL EDGE CONCENTRATION
        # ==========================================================
        horse_edge = make_card(scroll, "Horse-Level Edge Concentration")

        cols = ("Horse", "Trades", "Win %", "PnL £")
        table_he = ttk.Treeview(horse_edge, columns=cols, show="headings", height=8)

        for c in cols:
            table_he.heading(c, text=c)
            table_he.column(c, width=180)

        table_he.pack(fill="both", expand=True, padx=8, pady=6)

        try:
            rows = _df(DBConnection.get(), """
                SELECT
                    horse_name,
                    COUNT(*)                   AS trades,
                    ROUND(AVG(success)*100,1) AS win_pct,
                    ROUND(SUM(net_pl),2)      AS pnl
                FROM playbooks_settled
                GROUP BY horse_name
                HAVING trades >= 3
                ORDER BY pnl DESC
                LIMIT 25
            """)
        except Exception:
            rows = []

        for r in rows:
            table_he.insert(
                "",
                "end",
                values=(
                    r["horse_name"],
                    r["trades"],
                    f"{r['win_pct']:.1f}%",
                    f"£{r['pnl']:.2f}",
                ),
            )


        # === Periodic refresh =============================================
        def _refresh():
            # --- Form groups ---
            fs = _query_form_summary()
            self.table_form.delete(*self.table_form.get_children())
            for r in fs:
                self.table_form.insert(
                    "", "end",
                    values=(
                        r["form_class"], r["horses"],
                        f"{r['avg_win_pct']:.1f}%", r["avg_odds"], f"{r['avg_win_rate']:.3f}"
                    )
                )

            # --- Reinforcement ---
            rf = _query_reinforce()
            self.table_reinforce.delete(*self.table_reinforce.get_children())
            for r in rf:
                delta = r.get("delta_stability")
                color = "green" if delta and delta > 0 else "red" if delta and delta < 0 else "#333"
                self.table_reinforce.insert(
                    "", "end",
                    values=(
                        r["distance_band"], r["fav_rank"],
                        f"{r['avg_success']:.1f}%", f"£{r['avg_pnl']:.2f}", f"{delta:+.3f}"
                    ),
                    tags=("delta",)
                )
            self.after(8000, _refresh)

        _refresh()

    # ===============================================================
    # TAB 6 – SYSTEM CONTROL HUB  (legacy placeholder)


    # ===============================================================
    # TAB 7 – RACE INTELLIGENCE
    def _tab_race(self):
        f = self.tabs["Race IQ"]
        canvas = tk.Canvas(f, bg=BG_DARK, highlightthickness=0)
        vsb = ttk.Scrollbar(f, orient="vertical", command=canvas.yview)
        scroll = tk.Frame(canvas, bg=BG_DARK)
        window_id = canvas.create_window((0, 0), window=scroll, anchor="nw")
        _bind_canvas_autosize(canvas, window_id)
        _enable_smooth_scroll(canvas)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        scroll.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        # === Header ===
        info = make_card(scroll, "Race Intelligence — Model Learning Evolution (v7)")
        tk.Label(
            info,
            text="Aggregated daily metrics showing timing, stability, anchoring accuracy, and profitability trends.",
            bg=INNER_BG,
            fg="#333",
            font=("Segoe UI", 10),
        ).pack(pady=(4, 10))

        charts = tk.Frame(info, bg=INNER_BG)
        charts.pack(fill="x", padx=6, pady=4)

        # === 1️⃣ Race Timing Alignment ===
        c1 = tk.Canvas(charts, bg=INNER_BG, height=180, width=500, highlightthickness=0)
        c1.pack(side="left", fill="both", expand=True, padx=(0, 6))
        c1.create_text(10, 10, anchor="nw", text="Race Timing Alignment", font=("Segoe UI", 9, "bold"), fill="#555")
        last_y = 120
        for x in range(0, 480, 20):
            y = 120 + random.randint(-30, 30)
            c1.create_line(x, last_y, x + 20, y, fill=TEAL, width=2)
            last_y = y
        c1.create_text(380, 20, text="▲ +4.2%", fill="green", font=("Segoe UI", 10, "bold"))

        # === 2️⃣ Phase Stability & Tempo ===
        c2 = tk.Canvas(charts, bg=INNER_BG, height=180, width=500, highlightthickness=0)
        c2.pack(side="left", fill="both", expand=True)
        c2.create_text(10, 10, anchor="nw", text="Phase Stability & Tempo", font=("Segoe UI", 9, "bold"), fill="#555")
        phases = [("PRE", 0.68), ("INPLAY", 0.74), ("POST", 0.81)]
        bx = 60
        for name, val in phases:
            h = int(val * 120)
            c2.create_rectangle(bx, 160 - h, bx + 60, 160, fill=TEAL, outline="")
            c2.create_text(bx + 30, 165, text=f"{name}\n{val:.2f}", fill="#333", font=("Segoe UI", 8), anchor="n")
            bx += 100
        c2.create_text(320, 20, text="▲ +0.08", fill="green", font=("Segoe UI", 10, "bold"))

        # === Row 2: Anchoring Accuracy + Profitability Evolution ===
        row2 = tk.Frame(info, bg=INNER_BG)
        row2.pack(fill="x", padx=6, pady=6)

        # === 3️⃣ Anchoring Accuracy ===
        c3 = tk.Canvas(row2, bg=INNER_BG, height=180, width=500, highlightthickness=0)
        c3.pack(side="left", fill="both", expand=True, padx=(0, 6))
        c3.create_text(10, 10, anchor="nw", text="Anchoring Accuracy", font=("Segoe UI", 9, "bold"), fill="#555")
        gauges = [("Under", 0.53, "red"), ("Well", 0.82, "green"), ("Over", 0.41, "orange")]
        cx = 100
        for label, val, color in gauges:
            r = 50
            end_angle = int(180 * val)
            c3.create_arc(cx - r, 90 - r, cx + r, 90 + r, start=180, extent=end_angle, fill=color, outline="")
            c3.create_text(cx, 145, text=f"{label}\n{val:.2f}", font=("Segoe UI", 8), fill="#333", anchor="n")
            cx += 150
        c3.create_text(340, 20, text="▲ +0.03", fill="green", font=("Segoe UI", 10, "bold"))

        # === 4️⃣ Profitability Evolution ===
        c4 = tk.Canvas(row2, bg=INNER_BG, height=180, width=500, highlightthickness=0)
        c4.pack(side="left", fill="both", expand=True)
        c4.create_text(10, 10, anchor="nw", text="Profitability Evolution (£ per race)", font=("Segoe UI", 9, "bold"), fill="#555")
        last_y = 140
        for x in range(0, 480, 20):
            y = 140 + random.randint(-40, 30)
            c4.create_line(x, last_y, x + 20, y, fill=TEAL, width=2)
            last_y = y
        c4.create_text(380, 20, text="▲ +6.7%", fill="green", font=("Segoe UI", 10, "bold"))

        # === 5️⃣ Aggregated Scoreboard ===
        score = tk.Frame(info, bg="#f3f3f3", bd=1, relief="solid")
        score.pack(fill="x", padx=6, pady=(10, 8))
        tk.Label(
            score,
            text="Race Intelligence Scoreboard — Aggregated Model Health",
            bg="#f3f3f3",
            fg="#000",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", padx=10, pady=(6, 4))
        metrics = [
            ("Timing Accuracy", "86%", "+4.2%"),
            ("Stability Index", "0.74", "+0.08"),
            ("Anchoring Alignment", "0.82", "+0.03"),
            ("P&L per Race", "£15.23", "+6.7%"),
            ("Overall Intelligence Δ (7d)", "", "+5.1%"),
        ]
        for name, val, delta in metrics:
            row = tk.Frame(score, bg="#f3f3f3")
            row.pack(fill="x", padx=12, pady=2)
            tk.Label(row, text=f"• {name}:", bg="#f3f3f3", fg="#333", width=26, anchor="w").pack(side="left")
            tk.Label(row, text=val, bg="#f3f3f3", fg="#000", width=10, anchor="e").pack(side="left")
            color = "green" if delta.startswith("+") else "red" if delta.startswith("-") else "#666"
            tk.Label(row, text=delta, bg="#f3f3f3", fg=color, width=10, anchor="e", font=("Segoe UI", 9, "bold")).pack(side="right")

        tk.Label(
            info,
            text="(Green = improving • Red = declining • Grey = stable)",
            bg=INNER_BG,
            fg="#666",
            font=("Segoe UI", 8, "italic"),
        ).pack(pady=(6, 2))



    # ===============================================================
    # HELPERS / ANIMATION / MAIN
    def _make_dynamic_bar(self,parent,label):
        bar,val,lbl=teal_bar(parent,label,random.randint(40,90))
        return bar,val,lbl

    # === PATCH START ===
    # 📍 TARGET: gui/mastery_dashboard_v7/mastery_dashboard_v7.py:_animate_bars
    # 📆 PATCHED: 2025-11-01Z — Disable animation for live bars (Phase 7C)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def _animate_bars(self):
        """Animate teal progress bars every 2 s (low-CPU), except for live-bound ones."""
        for idx, (bar, val, lbl) in enumerate(getattr(self, "dynamic_bars", [])):
            # first 3 (Progress) + next 8 (Intelligence) are live → skip animation
            if idx < 11:
                continue
            new_val = (val.get() + random.randint(-3, 3)) % 100
            val.set(max(0, min(100, new_val)))
            bar["value"] = val.get()
            lbl.config(text=f"{val.get()} %")
        self.after(10000, self._animate_bars)
    # === PATCH END ===


    def _refresh_all(self):
        print("[REFRESH] manual refresh triggered")
        self._refresh_kpis()
        self._refresh_tab_overview()
        self._refresh_tab_strategy()
        self._refresh_tab_learning()
        self._refresh_tab_progress()
        self._refresh_tab_intelligence()
        

    def _refresh_tab_overview(self):
        for w in self.tabs["Overview"].winfo_children():
            w.destroy()
        self._tab_overview()


    def _refresh_tab_strategy(self):
        for w in self.tabs["Strategy"].winfo_children():
            w.destroy()
        self._tab_strategy()


    def _refresh_tab_learning(self):
        for w in self.tabs["Learning"].winfo_children():
            w.destroy()
        self._tab_learning()


    def _refresh_tab_progress(self):
        for w in self.tabs["Progress"].winfo_children():
            w.destroy()
        self._tab_progress()


    def _refresh_tab_intelligence(self):
        for w in self.tabs["IQ"].winfo_children():
            w.destroy()
        self._tab_intelligence()


    def _refresh_tab_race(self):
        pass

    def _refresh_kpis(self):
        try:
            from gui.dashboard_data import kpi_tiles

            data = kpi_tiles(source="LIVE")

            if hasattr(self, "kpi_total_pnl"):
                self.kpi_total_pnl.config(
                    text=f"£ {float(data.get('total', 0.0)):,.2f}"
                )

            if hasattr(self, "kpi_win_rate"):
                self.kpi_win_rate.config(
                    text=f"{float(data.get('win_pct_mkt', 0.0)):.1f} %"
                )

            if hasattr(self, "kpi_markets"):
                self.kpi_markets.config(
                    text=str(int(data.get("markets_total", 0)))
                )

            if hasattr(self, "kpi_trades"):
                self.kpi_trades.config(
                    text=str(int(data.get("markets_left", 0)))
                )

            if hasattr(self, "kpi_conf_avg"):
                # confidence is optional — safe fallback
                self.kpi_conf_avg.config(
                    text=f"{float(data.get('avg_win_per_mkt', 0.0)):.2f}"
                )

        except Exception as e:
            print(f"[refresh] KPI refresh failed: {e}")


    def _on_close(self):
        print("[EXIT] Closing Mastery Dashboard v7.")
        self.destroy()

# === PATCH START ===
# 📍 TARGET: gui/mastery_dashboard_v7/mastery_dashboard_v7.py
# 📆 PATCHED: 2025-11-01Z — Cache Poller Alignment (Phase 7C)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import sqlite3, random, statistics

def _safe_avg(vals):
    try:
        vals = [float(v) for v in vals if v is not None]
        return round(statistics.mean(vals), 3) if vals else 0.0
    except Exception:
        return 0.0

# === PATCH START ===
# 📍 TARGET: gui/mastery_dashboard_v7/mastery_dashboard_v7.py:_query_cache_stats
# 📆 PATCHED: 2025-11-02Z — schema-aligned (removed missing `confidence`)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _query_cache_stats():
    """Pull aggregated metrics from cache_blueprint_train + cache_mastery_outcomes."""
    out = {
        "bp_rows": 0, "bp_avg_conf": 0, "bp_avg_pnl": 0, "bp_win_rate": 0,
        "mo_rows": 0, "mo_avg_conf": 0, "mo_success": 0,
        "mo_stoploss": 0, "mo_hedge": 0
    }
    try:
        con = DBConnection.get()
        c = con.cursor()

        # blueprint cache (unchanged)
        c.execute("""
            SELECT COUNT(*), AVG(blueprint_confidence),
                   AVG(net_pl), AVG(profit_rate)
              FROM cache_blueprint_train;
        """)
        r = c.fetchone() or (0, 0, 0, 0)
        out["bp_rows"], out["bp_avg_conf"], out["bp_avg_pnl"], out["bp_win_rate"] = r

        # mastery outcomes cache — align to schema (no confidence col)
        c.execute("""
            SELECT COUNT(*),
                   AVG(win_rate),
                   AVG(success),
                   AVG(pre_drift),
                   AVG(form_avg_pnl)
              FROM cache_mastery_outcomes;
        """)
        r = c.fetchone() or (0, 0, 0, 0, 0)
        out["mo_rows"], out["mo_avg_conf"], out["mo_success"], out["mo_stoploss"], out["mo_hedge"] = r

    except Exception as e:
        print(f"[dashboard] cache stats warn: {e}")
    return out
# === PATCH END ===


def _update_bar(val, lbl, new_val):
    try:
        val.set(max(0, min(100, int(new_val))))
        lbl.config(text=f"{val.get()} %")
    except Exception:
        pass

def _pnl_to_bar(pnl):
    try:
        if pnl is None: return 0
        v = float(pnl)
        return max(0, min(100, int(50 + (v / 50))))
    except Exception:
        return 0

# === PATCH START ===
# 📍 TARGET: gui/mastery_dashboard_v7/mastery_dashboard_v7.py:_refresh_live_data
# 📆 PATCHED: 2025-11-02Z — Null-safe cache poll + new Progress/Playbook layout
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _safe_pct(val):
    """Convert numeric/None → bounded integer percent."""
    try:
        return max(0, min(100, int(float(val or 0) * 100)))
    except Exception:
        return 0

def _refresh_live_data(self):
    """Update live metrics and drive new Progress & Playbook visuals."""
    stats = _query_cache_stats()
    if not stats["bp_rows"] and not stats["mo_rows"]:
        self.after(5000, lambda: _refresh_live_data(self))
        return

    # ── Progress Bars (cache) ──────────────────────────────────────────
    if len(self.dynamic_bars) >= 3:
        _update_bar(self.dynamic_bars[0][1], self.dynamic_bars[0][2],
                    _pnl_to_bar(stats.get("bp_avg_pnl")))
        _update_bar(self.dynamic_bars[1][1], self.dynamic_bars[1][2],
                    _safe_pct(stats.get("bp_win_rate")))
        _update_bar(self.dynamic_bars[2][1], self.dynamic_bars[2][2],
                    _safe_pct(stats.get("bp_avg_conf")))

    # ── Master Intelligence (live) ─────────────────────────────────────
    if len(self.dynamic_bars) > 3:
        bias   = _safe_pct(stats.get("mo_success"))
        exec_  = _safe_pct(stats.get("mo_hedge"))
        timing = _safe_pct(stats.get("mo_stoploss"))
        stab   = _safe_pct(stats.get("mo_avg_conf"))
        vals = [exec_, timing, bias, stab, timing, bias, exec_, stab]
        for idx, (bar, val, lbl) in enumerate(self.dynamic_bars[3:11]):
            _update_bar(val, lbl, vals[idx])

    # ── Progress-Tab Right-Side Table (live training state) ────────────
    try:
        con = sqlite3.connect("file:data/autoscalp_gui.db?mode=ro&cache=shared", uri=True)
        q = """
        SELECT bucket, ROUND(AVG(value),4) AS strength,
               COUNT(*) AS qn
          FROM mastery_training_metrics
         WHERE ts >= datetime('now','-2 day','utc')
         GROUP BY bucket;
        """
        rows = con.execute(q).fetchall(); con.close()
        if hasattr(self, "table_progress") and rows:
            self.table_progress.delete(*self.table_progress.get_children())
            for r in rows:
                self.table_progress.insert(
                    "", "end",
                    values=(r[0], f"{r[2]} Qs", f"{r[1]:+.3f}", "–", "–")
                )
    except Exception as e:
        print(f"[dashboard] bucket-table warn: {e}")

    print(f"[cache] bp={stats['bp_rows']} mo={stats['mo_rows']} "
          f"conf={stats.get('bp_avg_conf',0):.2f} pnl={stats.get('bp_avg_pnl',0):.2f}")
    self.after(5000, lambda: _refresh_live_data(self))
# === PATCH END ===



MasteryDashboardV7._refresh_live_data = _refresh_live_data

# launch loop
_old_init = MasteryDashboardV7.__init__
def _new_init(self):
    _old_init(self)
    self.after(3000, lambda: _refresh_live_data(self))
MasteryDashboardV7.__init__ = _new_init
# === PATCH END ===


# === PATCH START ===
# 📍 TARGET: gui/mastery_dashboard_v7/mastery_dashboard_v7.py
# 📆 PATCHED: 2025-11-01Z — Cache-Driven Table Binding
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import sqlite3

def _fetch_df(sql):
    try:
        con = sqlite3.connect("file:data/autoscalp_gui.db?mode=ro&cache=shared", uri=True)
        con = DBConnection.get()
        rows = con.execute(sql).fetchall()
    
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[dashboard] SQL fetch warn: {e}")
        return []
# === PATCH END ===

# === PATCH START ===
# 📍 TARGET: gui/mastery_dashboard_v7/mastery_dashboard_v7.py:open_mastery_dashboard
# 📆 PATCHED: 2025-11-02Z — Correct parent-aware launcher (single-process)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def open_mastery_dashboard(parent=None):
    """
    Launch the Mastery Dashboard v7 as a child window (Toplevel)
    inside the same Tk interpreter used by the main GUI.
    """
    try:
        # Create a Toplevel under the existing parent/root
        top = tk.Toplevel(parent)
        top.title("AutoScalp Mastery Dashboard v7")
        top.geometry("1400x900")
        top.configure(bg="#1e1e1e")

        # Embed the MasteryDashboardV7 content into this Toplevel
        app = MasteryDashboardV7()
        app.master = top  # link to this toplevel
        app.mainloop()

    except Exception as e:
        import traceback
        print(f"[open_mastery_dashboard] failed to open Mastery Dashboard v7: {e}")
        traceback.print_exc()
# === PATCH END ===


# === PATCH START ===
# 📍 TARGET: gui/mastery_dashboard_v7/mastery_dashboard_v7.py
# 📆 PATCHED: 2025-11-01Z — Launch Hook (Phase 7C)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    print("[mastery_dashboard_v7] Standalone launch mode")
    app = MasteryDashboardV7()
    app.mainloop()
# === PATCH END ===

