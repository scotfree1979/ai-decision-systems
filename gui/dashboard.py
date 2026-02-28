#!/usr/bin/env python3
# === PATCH START ===
# 📍 TARGET: gui/dashboard.py (top imports + root shim)
# 📆 PATCHED: 2025-10-27Z — root-import shim + LIVE-safe DB connector
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
gui/dashboard.py — Minimal Live/Test dashboard
AutoScalp v6 — LIVE-safe version with proper repo-root shim
"""

from __future__ import annotations
import os, sys, sqlite3, threading, time, tkinter as tk
from tkinter import ttk
from datetime import datetime, timezone

# --- ensure repo root is importable ---
_here = os.path.dirname(os.path.abspath(__file__))          # .../gui
_root = os.path.abspath(os.path.join(_here, ".."))          # .../analytics_beta_dev
if _root not in sys.path:
    sys.path.insert(0, _root)

# ── Imports ────────────────────────────────────────────────────────────
from engines import config_paths as cp
from engines.session_secrets import get_secret, set_secret
from gui.dashboard_data import kpi_tiles
from engines.cashout_calc import cashout_calc

# ── DB path enforcement ────────────────────────────────────────────────
try:
    cp.set_db_paths(mode="live", quiet=True)
    print(f"[dashboard] forced LIVE db routing → {cp.autoscalp_db()}")
except Exception as e:
    print(f"[dashboard] live routing warn: {e}")

# === PATCH START ===
# 📍 TARGET: gui/dashboard.py (top-level helpers, below imports)
# 📆 PATCHED: 2025-10-29Z — add unified minutes_to_off clock for dashboard timing
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def minutes_to_off(off_at_utc: str, now_utc: datetime | None = None) -> float | None:
    """
    Return the minutes between the given off_at_utc (ISO string, possibly with Z)
    and the current UTC time. Positive = future, Negative = past.
    Example: +5.3 means race starts in 5m; -2.0 means 2m past off time.
    """
    if not off_at_utc:
        return None
    now = now_utc or datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(off_at_utc.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        try:
            dt = datetime.strptime(off_at_utc[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return (dt - now).total_seconds() / 60.0
# === PATCH END ===


# ── Settlements resolver ───────────────────────────────────────────────
def _ensure_settlements_path() -> str:
    """Ensure cp.settlements_db() exists and returns /data/settlements.db."""
    try:
        if hasattr(cp, "settlements_db"):
            path = cp.settlements_db()
            if path and os.path.exists(path):
                return path
        base = os.path.join(os.path.dirname(cp.autoscalp_db()), "settlements.db")
        setattr(cp, "settlements_db", lambda: base)
        return base
    except Exception:
        base = os.path.join(os.path.dirname(cp.autoscalp_db()), "settlements.db")
        setattr(cp, "settlements_db", lambda: base)
        return base

print(f"[dashboard] settlements.db path verified → {_ensure_settlements_path()}")

# ── Thread-safe UI helper ─────────────────────────────────────────────
def run_in_mainthread(func, *args, **kwargs):
    """Run Tkinter widget code safely in the main thread."""
    root = tk._default_root
    if threading.current_thread() is threading.main_thread():
        return func(*args, **kwargs)
    elif root:
        root.after(0, lambda: func(*args, **kwargs))

# ── DB connector (LIVE-safe) ───────────────────────────────────────────
# === PATCH START ===
# 📍 TARGET: gui/dashboard.py:_dashboard_con
# 📆 PATCHED: 2025-11-05Z — make LIVE connection writable so cashout_calc() can create temp tables
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _dashboard_con() -> sqlite3.Connection:
    """
    Return a unified connection for dashboard queries.
    Mirrors the TEST connector logic but points at the LIVE database.
    """
    import sqlite3, time, os
    auto = cp.autoscalp_db()
    bets = cp.bets_db()
    setl = _ensure_settlements_path()

    # Use the same URI-based shared-cache connection that TEST used
    con = sqlite3.connect(f"file:{auto}?cache=shared", uri=True, timeout=6)
    con.row_factory = sqlite3.Row

    # Attach the other databases with the same shared-cache semantics
    con.execute(f"ATTACH DATABASE 'file:{bets}?cache=shared' AS betsdb")
    con.execute(f"ATTACH DATABASE 'file:{setl}?cache=shared' AS setdb")

    # Light checkpoint to keep the WAL visible to other processes
    for _ in range(3):
        try:
            con.execute("PRAGMA wal_checkpoint(PASSIVE);")
            break
        except sqlite3.OperationalError:
            time.sleep(0.05)
    return con

def _live_conn_safe() -> sqlite3.Connection:
    return _dashboard_con()

# ── Money formatter ───────────────────────────────────────────────────
def _fmt_money(x: float) -> str:
    try:
        return f"£{float(x):,.2f}"
    except Exception:
        return "£0.00"
# === PATCH END ===

# --- shared helper for dashboard sections ---
def _status_for_mto(mto: float | None) -> str:
    """Return a status string consistent with _render_card()."""
    if mto is None:
        return "—"
    elif mto > 0:
        return "Upcoming"
    elif -15 <= mto <= 0:
        return "In-Play"
    else:
        return "Settled"


# ── Dashboard View ────────────────────────────────────────────────────
class DashboardView(ttk.Frame):
    """Lean Dashboard: KPIs + 3 markets + Risk sidebar + Mastery bars."""

    def __init__(self, parent, app=None, *, source="LIVE"):
        super().__init__(parent)
        self.app = app
        self.source_var = tk.StringVar(value=(source or "LIVE").upper())
        self.kpi_vars = {}

        # --- Heartbeat state ---
        self._heartbeat_started = False
        self._heartbeat_state = 0
        self._pulse_on = False
        self._pulse_speed = 1200
        self._last_snapshot_ts = None
        self._build_ui()
        self._init_heartbeat_styles()
        self._start_loops()

    # ── UI layout ─────────────────────────────────────────────────────
    def _build_ui(self):
        root = ttk.Frame(self)
        root.pack(fill="both", expand=True)

        # Header + KPIs
        header = ttk.Frame(root, padding=(10, 8, 10, 4))
        header.pack(fill="x")
        ttk.Label(header, text="Auto Scalping — Minimal Dashboard",
                  font=("TkDefaultFont", 16, "bold")).pack(side="left")
        ttk.Label(header, text="Source").pack(side="right")
        src_sel = ttk.Combobox(header, width=7, state="readonly",
                               values=("TEST", "LIVE", "REPLAY"),
                               textvariable=self.source_var)
        src_sel.pack(side="right", padx=(0, 10))
        src_sel.bind("<<ComboboxSelected>>", self._on_source_change)

        kpi_frame = ttk.LabelFrame(root, text="KPIs")
        self._init_kpis(kpi_frame)
        kpi_frame.pack(fill="x", padx=8, pady=(0, 6))

# === PATCH START ===
# 📍 TARGET: gui/dashboard.py:_build_ui
# 📆 PATCHED: 2025-10-29Z — Add Open Mastery Dashboard button (Phase 1)

# === PATCH START ===
# 📍 TARGET: gui/dashboard.py:_build_ui
# 📆 PATCHED: 2025-10-29Z — visual: black text for Mastery button

        mastery_btn = tk.Button(header, text="Open Mastery Dashboard",
                                command=self._open_mastery_dashboard,
                                bg="#d9d9d9", fg="black")
        mastery_btn.pack(side="right", padx=5)
# === PATCH END ===
        kpi_frame.pack(fill="x", padx=8, pady=(0, 6))
        self._build_execution_intelligence(root)

        # Scrollable container
        canvas = tk.Canvas(root, highlightthickness=0)
        vsb = ttk.Scrollbar(root, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        frame_id = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        def _resize(_): canvas.itemconfig(frame_id, width=canvas.winfo_width())
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", _resize)

        # Enable mousewheel scrolling across all platforms
        def _on_mousewheel(event):
            delta = 0
            if event.num == 4:
                delta = -1
            elif event.num == 5:
                delta = 1
            elif event.delta:
                delta = -1 * int(event.delta / 120)
            canvas.yview_scroll(delta, "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        canvas.bind_all("<Button-4>", _on_mousewheel)
        canvas.bind_all("<Button-5>", _on_mousewheel)


        # ── Recent Settlements frame ─────────────────────────────────────────
        self.inplay_frame = ttk.LabelFrame(scroll_frame, text="Recent Settlements")
        self.inplay_frame.pack(fill="x", padx=8, pady=(4, 8))
        self.inplay_cards = ttk.Frame(self.inplay_frame)
        self.inplay_cards.pack(fill="x", padx=6, pady=4)

        # ── Shared renderer helper ───────────────────────────────────────────
        def _render_card(parent, event_name, market_name, pnl, liab, mto):
            """
            Draw a market card with unified logic.
            mto = minutes_to_off()  (positive=future, negative=past)
            """
            # --- determine status ---
            if mto is None:
                status = "—"
            elif mto > 0:
                status = "Upcoming"
            elif -15 <= mto <= 0:
                status = "In-Play"
            else:
                status = "Settled"

            # --- liability reset for settled ---
            if status == "Settled":
                liab = 0.0

            color = "#2ecc71" if pnl > 0 else "#e74c3c" if pnl < 0 else "#bdc3c7"
            btn_txt = "CASH-OUT" if status != "Settled" else "SETTLED"
            btn_state = "normal" if status != "Settled" else "disabled"

            card = ttk.Frame(parent, padding=12, relief="ridge")
            ttk.Label(
                card,
                text=f"{event_name} ({market_name})",
                font=("TkDefaultFont", 9, "bold"),
                anchor="center", justify="center", wraplength=260
            ).pack(fill="x", pady=(0, 4))
            ttk.Label(
                card,
                text=f"Cash-Out: £{pnl:,.2f}",
                foreground=color,
                font=("TkDefaultFont", 12, "bold")
            ).pack(anchor="center")
            ttk.Label(
                card,
                text=f"Liability: £{liab:,.2f}",
                font=("TkDefaultFont", 9)
            ).pack(anchor="center", pady=(0, 4))
            ttk.Button(
                card,
                text=btn_txt,
                state=btn_state
            ).pack(ipadx=6, ipady=2)
            ttk.Label(
                card,
                text=f"Status: {status}",
                font=("TkDefaultFont", 8, "italic")
            ).pack(anchor="center", pady=(2, 0))
            return card


        def _refresh_inplay():
            """Show the three most recently settled markets (zero liability)."""
            try:
                con = _dashboard_con()
                # 1️⃣ fetch last 3 races that have already started
                rows = con.execute("""
                    SELECT DISTINCT b.marketId,
                           COALESCE(b.event_name,'')  AS event_name,
                           COALESCE(b.market_name,'') AS market_name,
                           b.marketStartTime          AS start_time
                      FROM betsdb.bets b
                      WHERE b.marketId IN (
                          SELECT DISTINCT marketId
                            FROM setdb.bf_cleared_orders
                           WHERE datetime(replace(settledDate,'Z','+00:00')) >= datetime('now','-48 hour')                      )
                  ORDER BY datetime(replace(b.marketStartTime,'T',' ')) DESC
                     LIMIT 3
                """).fetchall()


                # 2️⃣ live and cleared pnl maps
                live, cleared = {}, {}
                try:
                    live = cashout_calc(con, write_to_db=False)
                    for r in con.execute("""
                        SELECT marketId, SUM(COALESCE(profit,0.0)) AS settled_pnl
                         FROM setdb.bf_cleared_orders
                         WHERE settledDate IS NOT NULL
                         GROUP BY marketId
                    """):
                        mid = r["marketId"]
                        if mid in live:
                            live[mid]["total"] += float(r["settled_pnl"] or 0.0)
                        else:
                            live[mid] = {"total": float(r["settled_pnl"] or 0.0), "liability": 0.0}

                except Exception as e:
                    print(f"[settled] calc warn: {e}")
                con.close()

                # 3️⃣ rebuild cards safely on main thread
                def rebuild():
                    for w in self.inplay_cards.winfo_children():
                        w.destroy()

                    if not rows:
                        ttk.Label(
                            self.inplay_cards,
                            text="(no settled markets yet)",
                            font=("TkDefaultFont", 10, "italic")
                        ).grid(row=0, column=0, columnspan=3, pady=8, sticky="nsew")
                        return

                    for c in range(3):
                        self.inplay_cards.columnconfigure(c, weight=1, uniform="col")
                    self.inplay_cards.rowconfigure(0, weight=1, uniform="row")

                    for i, r in enumerate(rows[:3]):
                        mid = r["marketId"]
                        pnl  = float(live.get(mid, {}).get("total", 0.0))
                        liab = 0.0                                   # always zero for settled
                        spnl = cleared.get(mid)
                        disp = spnl if spnl is not None else pnl
                        color = "#2ecc71" if disp > 0 else "#e74c3c" if disp < 0 else "#bdc3c7"

                        card = ttk.Frame(self.inplay_cards, padding=12, relief="ridge")
                        card.grid(row=0, column=i, padx=10, pady=10, sticky="nsew")

                        ttk.Label(
                            card,
                            text=f"{r['event_name']} ({r['market_name']})",
                            font=("TkDefaultFont", 9, "bold"),
                            anchor="center",
                            justify="center",
                            wraplength=260
                        ).pack(fill="x", pady=(0, 4))
                        ttk.Label(
                            card,
                            text=f"Cash-Out: £{disp:,.2f}",
                            foreground=color,
                            font=("TkDefaultFont", 12, "bold")
                        ).pack(anchor="center")
                        ttk.Label(
                            card,
                            text=f"Liability: £{liab:,.2f}",
                            font=("TkDefaultFont", 9)
                        ).pack(anchor="center", pady=(0, 4))
                        ttk.Button(
                            card,
                            text="SETTLED",
                            state="disabled"
                        ).pack(ipadx=6, ipady=2)
                        ttk.Label(
                            card,
                            text="Status: SETTLED",
                            font=("TkDefaultFont", 8, "italic")
                        ).pack(anchor="center", pady=(2, 0))

                run_in_mainthread(rebuild)

            except Exception as e:
                print(f"[settled] refresh error: {e}")

            # 4️⃣ keep auto-scroll alive for long dashboards
            try:
                canvas.configure(scrollregion=canvas.bbox("all"))
            except Exception:
                pass

            self.after(5000, _refresh_inplay)

        # ── Real In-Play frame ─────────────────────────────────────────────
        self.real_inplay_frame = ttk.LabelFrame(scroll_frame, text="Real In-Play")
        self.real_inplay_frame.pack(fill="x", padx=8, pady=(4, 8))
        self.real_inplay_cards = ttk.Frame(self.real_inplay_frame)
        self.real_inplay_cards.pack(fill="x", padx=6, pady=4)

        def _refresh_real_inplay():
            """Show markets currently in-play (−15 ≤ mto ≤ 0)."""
            try:
                con = _dashboard_con()
                rows = con.execute("""
                    SELECT b.marketId,
                           COALESCE(b.event_name,'') AS event_name,
                           COALESCE(b.market_name,'') AS market_name,
                           b.marketStartTime AS start_time
                      FROM betsdb.bets b
                     WHERE b.marketStartTime IS NOT NULL
                       AND datetime(replace(b.marketStartTime,'T',' '))
                           BETWEEN datetime('now','utc','-15 minute')
                               AND datetime('now','utc','+15 minute')
                  GROUP BY b.marketId
                """).fetchall()
                pnlmap = cashout_calc(con, write_to_db=False)

                # --- merge cleared P&L from settlements.db (UTC-safe join) ---
                try:
                    for r in con.execute("""
                        SELECT marketId, SUM(COALESCE(profit,0.0)) AS settled_pnl
                          FROM setdb.bf_cleared_orders
                         WHERE datetime(replace(settledDate,'Z','+00:00')) >= datetime('now','-7 day')
                         GROUP BY marketId
                    """):
                        mid = r["marketId"]
                        if mid in pnlmap:
                            pnlmap[mid]["total"] += float(r["settled_pnl"] or 0.0)
                        else:
                            pnlmap[mid] = {"total": float(r["settled_pnl"] or 0.0), "liability": 0.0}
                except Exception as e:
                    print(f"[cashout] merge warn: {e}")

                con.close()


                now_utc = datetime.now(timezone.utc)
                inplay_rows = [r for r in rows
                               if -15 <= (minutes_to_off(r["start_time"], now_utc) or 99) <= 0]

                def rebuild():
                    for w in self.real_inplay_cards.winfo_children():
                        w.destroy()
                    if not inplay_rows:
                        ttk.Label(self.real_inplay_cards,
                                  text="(no races currently in-play)",
                                  font=("TkDefaultFont",10,"italic")).grid(
                                      row=0,column=0,columnspan=3,pady=8,sticky="nsew")
                        return
                    for c in range(3):
                        self.real_inplay_cards.columnconfigure(c,weight=1,uniform="col")
                    self.real_inplay_cards.rowconfigure(0,weight=1,uniform="row")

                    for i,r in enumerate(inplay_rows[:3]):
                        mid=r["marketId"]
                        mdata=pnlmap.get(mid,{})
                        pnl=float(mdata.get("total",0.0))
                        liab=float(mdata.get("liability",0.0))
                        color="#2ecc71" if pnl>0 else "#e74c3c" if pnl<0 else "#bdc3c7"
                        card=_render_card(self.real_inplay_cards,
                                          r["event_name"],r["market_name"],
                                          pnl,liab,minutes_to_off(r["start_time"],now_utc))
                        card.grid(row=0,column=i,padx=10,pady=10,sticky="nsew")

                run_in_mainthread(rebuild)

            except Exception as e:
                print(f"[real-inplay] refresh error: {e}")
            try:
                canvas.configure(scrollregion=canvas.bbox("all"))
            except Exception:
                pass
            self.after(5000,_refresh_real_inplay)

        # start timer for real-inplay
        self.after(2000,_refresh_real_inplay)

        # ── Cash-Out frame ─────────────────────────────────────────────
        self.cashout_frame = ttk.LabelFrame(scroll_frame,text="Cash-Out Monitor")
        self.cashout_frame.pack(fill="x",padx=8,pady=(4,8))
        self.cashout_cards = ttk.Frame(self.cashout_frame)
        self.cashout_cards.pack(fill="x",padx=6,pady=4)

        def _refresh_cashout():
            """Next six markets (24 h forward window)."""
            try:
                con=_dashboard_con()
                rows = con.execute("""
                    SELECT DISTINCT b.marketId,
                           COALESCE(b.event_name,'')  AS event_name,
                           COALESCE(b.market_name,'') AS market_name,
                           b.marketStartTime          AS start_time
                      FROM betsdb.bets b
                     WHERE b.marketStartTime IS NOT NULL
                       AND datetime(replace(b.marketStartTime,'T',' ')) > datetime('now','utc')
                       AND b.marketId NOT IN (
                           SELECT DISTINCT marketId FROM setdb.bf_cleared_orders
                       )
                  ORDER BY datetime(replace(b.marketStartTime,'T',' ')) ASC
                     LIMIT 6
                """).fetchall()

                # --- Exclusion filter: remove markets that are already In-Play or Settled ---
                now_utc = datetime.now(timezone.utc)
                filtered_rows = []
                for r in rows:
                    mto = minutes_to_off(r["start_time"], now_utc)
                    status = _status_for_mto(mto)
                    if status not in ("In-Play", "Settled"):
                        filtered_rows.append(r)
                rows = filtered_rows
                pnlmap=cashout_calc(con,write_to_db=False)
                con.close()

                def rebuild():
                    for w in self.cashout_cards.winfo_children():
                        w.destroy()
                    if not rows:
                        ttk.Label(self.cashout_cards,
                                  text="(no upcoming markets in next 24 h)",
                                  font=("TkDefaultFont",10,"italic")).grid(
                                      row=0,column=0,columnspan=3,pady=8,sticky="nsew")
                        return
                    for c in range(3):
                        self.cashout_cards.columnconfigure(c,weight=1,uniform="col")
                    for r_i in range(2):
                        self.cashout_cards.rowconfigure(r_i,weight=1,uniform="row")



                    for i,r in enumerate(rows[:6]):
                        mto=minutes_to_off(r["start_time"],now_utc)




                        mdata=pnlmap.get(r["marketId"],{})
                        pnl=float(mdata.get("total",0))
                        liab=float(mdata.get("liability",0))
                        color="#2ecc71" if pnl>0 else "#e74c3c" if pnl<0 else "#bdc3c7"

     



                        card=_render_card(self.cashout_cards,
                                          r["event_name"],r["market_name"],
                                          pnl,liab,mto)
                        card.grid(row=i//3,column=i%3,padx=10,pady=10,sticky="nsew")
 

                run_in_mainthread(rebuild)

            except Exception as e:
                print(f"[cashout] refresh error: {e}")
            try:
                canvas.configure(scrollregion=canvas.bbox("all"))
            except Exception:
                pass
            self.after(5000,_refresh_cashout)

        # independent Tk timers
        self.after(1500,_refresh_inplay)
        self.after(2500,_refresh_cashout)

    # ── KPI / misc loops ───────────────────────────────────────────────
    def _init_kpis(self, parent):
        self.kpi_vars = {k: tk.StringVar(value="£0.00") for k in
            ["total","today","yday","d7","d30","pnlhr","avgwin","avgloss","livepnl"]}
        self.kpi_vars["win"] = tk.StringVar(value="0.0%")
        self.kpi_vars["mleft"] = tk.StringVar(value="0")
        self.kpi_vars["mtotal"] = tk.StringVar(value="0")
        frame = ttk.Frame(parent); frame.pack(fill="x", padx=8, pady=6)
        tiles = [
            ("Total","total"),("Yesterday","yday"),("Today","today"),
            ("7 days","d7"),("30 days","d30"),("Win% mkt","win"),
            ("P&L / hour","pnlhr"),("Markets total","mtotal"),
            ("Markets left","mleft"),("Avg win/mkt","avgwin"),
            ("Avg loss/mkt","avgloss"),("Live P&L (Unsettled)","livepnl"),
        ]
        for i,(lab,key) in enumerate(tiles):
            box = ttk.Frame(frame)
            ttk.Label(box,text=lab,font=("TkDefaultFont",10,"bold")).pack(anchor="w")
            ttk.Label(box,textvariable=self.kpi_vars[key],
                      font=("TkDefaultFont",14)).pack(anchor="w")
            box.grid(row=0,column=i,padx=10,pady=4,sticky="w")
            frame.grid_columnconfigure(i,weight=1)

    def _init_heartbeat_styles(self):
        style = ttk.Style()

        # Soft white base
        style.configure("Bank.Rest.TLabelframe", background="#f8fbff")
        style.configure("Bank.Build.TLabelframe", background="#fff7e6")
        style.configure("Bank.Race.TLabelframe", background="#ffeaea")
        style.configure("Bank.Crit.TLabelframe", background="#ffd6d6")

        style.configure("Router.Rest.TLabelframe", background="#f8fbff")
        style.configure("Router.Build.TLabelframe", background="#fff7e6")
        style.configure("Router.Race.TLabelframe", background="#ffeaea")
        style.configure("Router.Crit.TLabelframe", background="#ffd6d6")

    def _compute_heartbeat_state(self, bank_row, row):

        if not bank_row:
            return 0

        total_pot = float(bank_row["total_pot"] or 0)
        total_used = float(bank_row["total_used"] or 0)
        available = float(bank_row["total_available"] or 0)

        exposure_ratio = (total_used / total_pot) if total_pot else 0

        if exposure_ratio > 0.9:
            return 2  # force racing mode

        # 💀 CRITICAL
        if available < 0:
            return 3

        # 🔴 RACING — any open lifecycle activity?
        if row:
            open_activity = (
                int(row["queued"] or 0)
                + int(row["placing"] or 0)
                + int(row["placed"] or 0)
            )
            if open_activity > 0:
                return 2

        # 🟡 BUILDING
        if exposure_ratio > 0.7:
            return 1

        # 🟢 REST
        return 0

    def _heartbeat_loop(self):

        if not self._heartbeat_started:
            return

        # Pulse effect
        if self._pulse_on:
            self.bank_frame.configure(style="")
            self.router_frame.configure(style="")
        else:
            if self._heartbeat_state == 0:
                style = "Bank.Rest.TLabelframe"
            elif self._heartbeat_state == 1:
                style = "Bank.Build.TLabelframe"
            elif self._heartbeat_state == 2:
                style = "Bank.Race.TLabelframe"
            else:
                style = "Bank.Crit.TLabelframe"

            self.bank_frame.configure(style=style)
            self.router_frame.configure(style=style.replace("Bank", "Router"))

        self._pulse_on = not self._pulse_on
        self.after(self._pulse_speed, self._heartbeat_loop)



    def _refresh_kpis(self):
        try:
            d = kpi_tiles(source=self.source_var.get())
        except Exception:
            d = {}
        def _m(x): 
            try: return f"£{float(x):.2f}"
            except: return "£0.00"
        self.kpi_vars["total"].set(_m(d.get("total",0)))
        self.kpi_vars["today"].set(_m(d.get("today",0)))
        self.kpi_vars["yday"].set(_m(d.get("yesterday",0)))
        self.kpi_vars["d7"].set(_m(d.get("d7",0)))
        self.kpi_vars["d30"].set(_m(d.get("d30",0)))
        self.kpi_vars["win"].set(f"{float(d.get('win_pct_mkt',0)):.1f}%")
        self.kpi_vars["pnlhr"].set(_m(d.get("pnl_per_hour",0)))
        self.kpi_vars["avgwin"].set(_m(d.get("avg_win_per_mkt",0)))
        self.kpi_vars["avgloss"].set(_m(d.get("avg_loss_per_mkt",0)))
        self.kpi_vars["mtotal"].set(str(int(d.get("markets_total",0))))
        self.kpi_vars["mleft"].set(str(int(d.get("markets_left",0))))
        self.kpi_vars["livepnl"].set(_m(d.get("live_pnl_unsettled",0)))
        self.after(5000, self._refresh_kpis)

    # ===============================================================
    # EXECUTION INTELLIGENCE BLOCK
    # ===============================================================
# === PATCH START ==============================================================
# 📍 TARGET: gui/dashboard.py
# 📆 PATCHED: 2026-02-27 — Execution Intelligence v2 (DB-driven, 4 panels)
# PURPOSE:
#   • Read from structured runtime tables
#   • Zero dependency on BUS/router objects
#   • Matches final wireframe
# ==============================================================================

    def _build_execution_intelligence(self, parent):

        container = ttk.LabelFrame(parent, text="Execution Intelligence")
        container.pack(fill="x", padx=8, pady=(0, 8))

        # 2 main columns
        container.columnconfigure(0, weight=1, minsize=260)  # LEFT
        container.columnconfigure(1, weight=1)  # RIGHT

        # ============================================================
        # LEFT COLUMN (BUS + INPLAY stacked)
        # ============================================================
        left = ttk.Frame(container)
        left.grid(row=0, column=0, sticky="ns", padx=6)
        left.columnconfigure(0, weight=1)

        # BUS
        self.bus_frame = ttk.LabelFrame(left, text="BUS")
        self.bus_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 6))

        self.bus_vars = {
            "route": tk.StringVar(value="Route ID: 0"),
            "stop":  tk.StringVar(value="Bus Stop: 0"),
            "tick":  tk.StringVar(value="Tick ID: 0"),
            "hz":    tk.StringVar(value="Hz: 0"),
            "fill":  tk.StringVar(value="Fill Rate: 0%"),
        }

        for i, v in enumerate(self.bus_vars.values()):
            ttk.Label(self.bus_frame, textvariable=v).grid(
                row=i, column=0, sticky="w", padx=6, pady=2
            )

        # INPLAY
        self.inplay_frame = ttk.LabelFrame(left, text="INPLAY")
        self.inplay_frame.grid(row=1, column=0, sticky="nsew")

        self.inplay_vars = {
            "market": tk.StringVar(value="Market: -"),
            "runner": tk.StringVar(value="Runner: -"),
            "price": tk.StringVar(value="Price: -"),
            "armed": tk.StringVar(value="Armed: -"),
            "triggered": tk.StringVar(value="Triggered: -"),
        }

        for i, v in enumerate(self.inplay_vars.values()):
            ttk.Label(self.inplay_frame, textvariable=v).grid(
                row=i, column=0, sticky="w", padx=6, pady=2
            )

        # ============================================================
        # RIGHT COLUMN (BANKSTATE + ROUTER side-by-side)
        # ============================================================
        right = ttk.Frame(container)
        right.grid(row=0, column=1, sticky="nsew", padx=6)

        # BANKSTATE and ROUTER side by side
        right.columnconfigure(0, weight=1)
        right.columnconfigure(1, weight=1)

        # BANKSTATE (left half of right column)
        self.bank_frame = ttk.LabelFrame(right, text="BANKSTATE")
        self.bank_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        self.bank_text = tk.Text(
            self.bank_frame,
            height=28,
            font=("Courier New", 11, "bold"),
            wrap="none",
            bg="#ffffff",
            relief="flat",
            borderwidth=0
        )
        self.bank_text.pack(fill="both", expand=True, padx=8, pady=8)

        # ROUTER (right half)
        self.router_frame = ttk.LabelFrame(right, text="ROUTER")
        self.router_frame.grid(row=0, column=1, sticky="nsew")

        self.router_text = tk.Text(
            self.router_frame,
            height=28,
            font=("Courier New", 11, "bold"),
            wrap="none",
            bg="#ffffff",
            relief="flat",
            borderwidth=0
        )
        self.router_text.pack(fill="both", expand=True, padx=8, pady=8)

        self._refresh_execution_intelligence()

    def _refresh_execution_intelligence(self):

        import sqlite3
        from engines.config_paths import autoscalp_db

        try:
            con = _dashboard_con()
            con.row_factory = sqlite3.Row

            bank_row = con.execute("""
                SELECT * FROM bankstate_runtime_snapshot
                ORDER BY ts DESC LIMIT 1
            """).fetchone()

            row = con.execute("""
                SELECT * FROM router_runtime_snapshot
                ORDER BY ts DESC LIMIT 1
            """).fetchone()

            self._render_router_report(con)
            self._render_bankstate_report(con)

            # 🔥 HEARTBEAT CONTROL (single location only)
            if bank_row and not self._heartbeat_started:
                self._heartbeat_started = True
                self._heartbeat_loop()

            if bank_row:
                self._heartbeat_state = self._compute_heartbeat_state(bank_row, row)

            if self._heartbeat_state == 0:
                self._pulse_speed = 1400
            elif self._heartbeat_state == 1:
                self._pulse_speed = 900
            elif self._heartbeat_state == 2:
                self._pulse_speed = 600
            else:
                self._pulse_speed = 350
            # ─────────────────────────────────────────
            # BUS SNAPSHOT
            # ─────────────────────────────────────────
            row = con.execute("""
                SELECT * FROM bus_runtime_snapshot
                ORDER BY ts DESC LIMIT 1
            """).fetchone()

            if row:
                self.bus_vars["route"].set(f"Route ID: {row['route_id']}")
                self.bus_vars["stop"].set(f"Bus Stop: {row['bus_stop']}")
                self.bus_vars["tick"].set(f"Tick ID: {row['tick_id']}")
                self.bus_vars["hz"].set(f"Hz: {row['hz']}")
                self.bus_vars["fill"].set(
                    f"Fill Rate: {round((row['fill_rate'] or 0)*100,1)}%"
                )

            # ─────────────────────────────────────────
            # INPLAY SNAPSHOT
            # ─────────────────────────────────────────
            row = con.execute("""
                SELECT * FROM inplay_runtime_snapshot
                ORDER BY ts DESC LIMIT 1
            """).fetchone()

            if row:
                name_row = con.execute("""
                    SELECT horse_name, event_name
                    FROM betsdb.bets
                    WHERE marketId=? AND selectionId=?
                    LIMIT 1
                """, (row["marketId"], row["selectionId"])).fetchone()

                horse = name_row["horse_name"] if name_row else row["selectionId"]
                event = name_row["event_name"] if name_row else row["marketId"]

                self.inplay_vars["market"].set(f"Market: {event}")
                self.inplay_vars["runner"].set(f"Runner: {horse}")
                self.inplay_vars["price"].set(f"Price: {row['px']}")
                self.inplay_vars["armed"].set(
                    f"Armed: L={row['armed_lay']} B={row['armed_back']}"
                )
                self.inplay_vars["triggered"].set(
                    f"Triggered: {row['triggered']}"
                )

            con.close()

        except Exception as e:
            print("Execution Intelligence Error:", e)

        self.after(2000, self._refresh_execution_intelligence)

    def _render_router_report(self, con):

        rows = con.execute("""
            SELECT *
            FROM router_runtime_snapshot
            ORDER BY ts DESC
        """).fetchall()

        if not rows:
            return

        from collections import defaultdict

        parents = defaultdict(lambda: defaultdict(int))
        children = defaultdict(lambda: defaultdict(int))

        for r in rows:
            engine = r["engine"]
            role = r["role"]

            bucket_map = {
                "QUEUED": r["queued"],
                "PLACING": r["placing"],
                "PLACED": r["placed"],
                "MATCHED": r["matched"],
                "CANCELLED": r["cancelled"],
                "CLOSED": r["closed"],
            }

            if role == "PARENT":
                parents[engine] = bucket_map
            elif role == "CHILD":
                children[engine] = bucket_map

        # --------------------------------------------------
        # Totals
        # --------------------------------------------------

        total_parents_open = sum(
            p.get("QUEUED",0) + p.get("PLACING",0) + p.get("PLACED",0)
            for p in parents.values()
        )

        total_children_open = sum(
            c.get("QUEUED",0) + c.get("PLACING",0) + c.get("PLACED",0)
            for c in children.values()
        )

        total_open = total_parents_open + total_children_open

        total_matched = sum(
            p.get("MATCHED",0) for p in parents.values()
        ) + sum(
            c.get("MATCHED",0) for c in children.values()
        )

        total_closed = sum(
            p.get("CLOSED",0) for p in parents.values()
        ) + sum(
            c.get("CLOSED",0) for c in children.values()
        )

        # --------------------------------------------------
        # Dynamic header 🔥⚡🟢
        # --------------------------------------------------

        if total_open > 200:
            header = "🔥 V7 ROUTER LIVE STATE"
        elif total_open > 100:
            header = "⚡ V7 ROUTER LIVE STATE"
        else:
            header = "🟢 V7 ROUTER LIVE STATE"

        text = []
        text.append(header)
        text.append("=" * 60)
        text.append("")

        # --------------------------------------------------
        # 📦 PARENTS
        # --------------------------------------------------

        text.append("📦 PARENTS — ENTRY / EXIT STATUS (BY ENGINE)")
        text.append("-" * 60)
        text.append("ENGINE            QUEUED  PLACING  PLACED  MATCHED  CANCELLED  CLOSED")
        text.append("-" * 60)

        for engine in sorted(parents.keys()):
            p = parents[engine]
            text.append(
                f"{engine:<16} "
                f"{p.get('QUEUED',0):>6} "
                f"{p.get('PLACING',0):>8} "
                f"{p.get('PLACED',0):>8} "
                f"{p.get('MATCHED',0):>8} "
                f"{p.get('CANCELLED',0):>10} "
                f"{p.get('CLOSED',0):>8}"
            )

        # --------------------------------------------------
        # 👶 CHILDREN
        # --------------------------------------------------

        text.append("")
        text.append("👶 CHILDREN — ENTRY / EXIT STATUS (BY ENGINE)")
        text.append("-" * 60)
        text.append("ENGINE            QUEUED  PLACING  PLACED  MATCHED  CANCELLED  CLOSED")
        text.append("-" * 60)

        for engine in sorted(children.keys()):
            c = children[engine]
            text.append(
                f"{engine:<16} "
                f"{c.get('QUEUED',0):>6} "
                f"{c.get('PLACING',0):>8} "
                f"{c.get('PLACED',0):>8} "
                f"{c.get('MATCHED',0):>8} "
                f"{c.get('CANCELLED',0):>10} "
                f"{c.get('CLOSED',0):>8}"
            )

        # --------------------------------------------------
        # ⚡ TRADE SUMMARY
        # --------------------------------------------------

        text.append("")
        text.append("⚡ TRADE SUMMARY")
        text.append("-" * 40)

        if total_open > 150:
            open_display = f"🔥 {total_open}"
        elif total_open > 75:
            open_display = f"⚡ {total_open}"
        else:
            open_display = f"{total_open}"

        text.append(f"Total Open      : {open_display}")
        text.append(f"Total Matched   : ✅ {total_matched}")
        text.append(f"Total Closed    : {total_closed}")

        self.router_text.delete("1.0", tk.END)
        self.router_text.insert("1.0", "\n".join(text))

    def _render_bankstate_report(self, con):

        row = con.execute("""
            SELECT * FROM bankstate_runtime_snapshot
            ORDER BY ts DESC
            LIMIT 1
        """).fetchone()

        if not row:
            return

        ts = row["ts"]

        text = []
        text.append("GLOBAL SUMMARY")
        text.append("-" * 60)
        text.append(f"Open Exposure        : {row['total_exposure']:.2f}")
        text.append(f"Total Pot            : {row['total_pot']:.2f}")
        text.append(f"Total Used           : {row['total_used']:.2f}")
        available = float(row["total_available"] or 0)

        if available < 0:
            avail_display = f"🔴 {available:.2f}"
        else:
            avail_display = f"🟢 {available:.2f}"

        text.append(f"Total Available      : {avail_display}")
        text.append("-" * 60)
        text.append("")

        text.append("🏦 V7 BANKSTATE — LIVE CAPITAL ENGINE")
        text.append("-" * 70)
        text.append("{:<18}{:>10}{:>10}{:>10}{:>12}{:>12}".format(
            "ENGINE","POT","USED","AVAIL","FLOOR","UNMATCHED"
        ))
        text.append("-" * 70)

        rows = con.execute("""
            SELECT *
            FROM bankstate_engine_snapshot
            WHERE ts = (
                SELECT MAX(ts)
                FROM bankstate_engine_snapshot
            ) 
        """).fetchall()

        for r in rows:
            used = float(r["used"] or 0)
            pot = float(r["pot"] or 0)
            avail = float(r["available"] or 0)

            # 🔥 Over-extended engine
            if avail < 0:
                engine_name = f"🔥 {r['engine']}"
            elif used > pot * 0.8:
                engine_name = f"⚡ {r['engine']}"
            else:
                engine_name = f"{r['engine']}"

            floor = float(r["floor"] or 0)
            unmatched = float(r["unmatched"] or 0)

            text.append("{:<18}{:>10.2f}{:>10.2f}{:>10.2f}{:>12.2f}{:>12.2f}".format(
                engine_name,
                pot,
                used,
                avail,
                floor,
                unmatched
            ))
            text.append("-" * 70)

        self.bank_text.delete("1.0", tk.END)
        self.bank_text.insert("1.0", "\n".join(text))

      
    def _on_source_change(self,_=None):
        try:
            cp.set_db_paths(mode=self.source_var.get().lower(), quiet=True)
            print(f"[dashboard] source switched → {self.source_var.get()}")
        except Exception as e:
            print(f"[dashboard] source switch warn: {e}")

    def _start_loops(self):
        self.after(200, self._refresh_kpis)

# === PATCH START ===
# 📍 TARGET: gui/dashboard.py
# 🔎 SEARCH: class DashboardView
# 📆 PATCHED: 2025-10-29Z — Fix Mastery Dashboard open for non-.root parent

    def _open_mastery_dashboard(self):
        """Open the standalone Mastery Dashboard window."""
        try:
            from gui.mastery_dashboard_v7.mastery_dashboard_v7 import open_mastery_dashboard
            # Use self.master if .root doesn’t exist (Tkinter base)
            parent = getattr(self, "root", None) or getattr(self, "master", None)
            open_mastery_dashboard(parent)
        except Exception as e:
            import traceback
            print(f"[MASTERY] failed to open: {e}")
            traceback.print_exc()
# === PATCH END ===


# ── Launcher ──────────────────────────────────────────────────────────
def open_dashboard_window(master=None, *, source="LIVE", app=None):
    win = tk.Toplevel(master)
    win.title("Auto Scalping — Dashboard")
    container = ttk.Frame(win)
    container.pack(fill="both", expand=True)
    view = DashboardView(container, app=app, source=source)
    view.pack(fill="both", expand=True)
    return win, view

# === PATCH START ===
# 📍 TARGET: gui/dashboard.py (end of file)
# 📆 PATCHED: 2025-10-27Z — standalone launcher for direct execution
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    print("[dashboard] standalone launch (direct execution mode)")
    root = tk.Tk()
    root.title("AutoScalp — LIVE Dashboard")
    root.geometry("1280x800")

    view = DashboardView(root, source="LIVE")
    view.pack(fill="both", expand=True)

    print("[dashboard] Tk mainloop starting …")
    root.update_idletasks()
    root.update()
    root.after(100, lambda: root.event_generate("<Configure>"))
    # --- Force initial render (fixes blank-on-launch issue) ---
    def redraw(event=None):
        """Force a one-time geometry recalculation so cards render immediately."""
        try:
            root.update_idletasks()
            root.update()
            # trigger layout for every Frame and Canvas child
            for child in root.winfo_children():
                child.event_generate("<Configure>")
        except Exception as e:
            print(f"[dashboard] redraw warn: {e}")

    # trigger once, 200ms after the window is visible
    root.after(200, redraw)

    root.mainloop()
# === PATCH END ===

