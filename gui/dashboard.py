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
import os, sys

# --------------------------------------------------
# REPO ROOT SHIM (must happen BEFORE engines imports)
# --------------------------------------------------
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.abspath(os.path.join(_here, ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

import sqlite3, threading, time, tkinter as tk
from tkinter import ttk
from datetime import datetime, timezone

# engines imports AFTER root is injected
from engines.live_view.live_view import get_live_view_state, start_live_view_loop
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

# === PATCH START ==============================================================
# 📍 TARGET: gui/dashboard.py
# 🔎 SEARCH: from engines.cashout_calc import cashout_calc
# 🛠 ACTION: Add BUS snapshot access
# 📆 PATCHED: 2026-04-05 — Dashboard uses BUS route snapshot instead of SQL
# PURPOSE:
# - Access runner PX and identity from BUS ctx_map
# - Remove dependency on betsdb px fields
# - Prevent sqlite "no such column: px"
# ==============================================================================

from engines.bus.bus import BUS

# === PATCH END ================================================================

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
        self._last_live_market = None
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
                con.row_factory = sqlite3.Row

                race = con.execute("""
                    SELECT marketId, event_name, market_name, marketStartTime
                    FROM betsdb.bets
                    WHERE marketStartTime > datetime('now','utc')
                    ORDER BY marketStartTime ASC
                    LIMIT 1
                """).fetchone()
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

    def _open_full_dashboard(self):
        """
        Opens a secondary window containing the below-fold dashboard.
        """

        win = tk.Toplevel(self)
        win.title("AutoScalp — Extended Dashboard")
        win.geometry("1400x900")

        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text="Extended Dashboard",
            font=("TkDefaultFont", 14, "bold")
        ).pack(anchor="w", padx=10, pady=10)

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
        # LEFT COLUMN — STACKED EXECUTION CARDS (BUS + INPLAY)
        # ============================================================

        left = ttk.Frame(container)
        left.grid(row=0, column=0, sticky="nsew", padx=6)
        left.columnconfigure(0, weight=1)

        self.exec_stack = ttk.Frame(left)
        self.exec_stack.pack(fill="both", expand=True)

        # --------------------------------------------------
        # 🚌 BUS HEADER CARD
        # --------------------------------------------------

        self.bus_header_card = ttk.Frame(self.exec_stack, padding=10, relief="ridge")
        self.bus_header_card.pack(fill="x", pady=6)

        ttk.Label(
            self.bus_header_card,
            text="🚌 V7 BUS LIVE STATE",
            font=("TkDefaultFont", 11, "bold")
        ).pack(anchor="w")

        # --------------------------------------------------
        # 🚌 ROUTE INFO CARD
        # --------------------------------------------------

        self.bus_route_card = ttk.Frame(self.exec_stack, padding=8, relief="ridge")
        self.bus_route_card.pack(fill="x", pady=6)

        self.bus_vars = {
            "route": tk.StringVar(value="Route ID: -"),
            "stop":  tk.StringVar(value="Bus Stop: -"),
            "tick":  tk.StringVar(value="Tick ID: -"),
            "hz":    tk.StringVar(value="Hz: -"),
            "fill":  tk.StringVar(value="Fill Rate: -"),
        }

        for v in self.bus_vars.values():
            ttk.Label(self.bus_route_card, textvariable=v).pack(anchor="w")

        # ======================================================================================================
        # 📍 TARGET: gui/dashboard.py
        # 🔎 SEARCH: 🚌 NEXT STOP RUNNERS HEADER
        # 🧩 ACTION: Replace BUS stop runner panel with Runner P&L Intelligence panels
        # 📆 PATCHED: 2026-03-12 — Top Winners / Losers + Live Exposure cards
        #
        # PURPOSE
        # -------
        # Replace NEXT BUS STOP runners with actionable trading intelligence.
        #
        # SECTION 1
        #   💰 TODAY — REALISED P&L
        #   - Top 5 winning runners
        #   - Top 5 losing runners
        #   - Totals
        #   - Net impact
        #
        # SECTION 2
        #   ⚡ NEXT MARKETS — LIVE EXPOSURE
        #   - Top 5 potential winners
        #   - Top 5 potential losers
        #   - Totals
        #   - Net exposure
        # ======================================================================================================

        # --------------------------------------------------
        # 💰 TODAY — REALISED P&L HEADER
        # --------------------------------------------------

        self.pnl_today_header = ttk.Frame(self.exec_stack, padding=8, relief="ridge")
        self.pnl_today_header.pack(fill="x", pady=6)

        ttk.Label(
            self.pnl_today_header,
            text="💰 TODAY — REALISED P&L",
            font=("TkDefaultFont", 10, "bold")
        ).pack(anchor="w")

        # --------------------------------------------------
        # WIN / LOSS HEADER ROW
        # --------------------------------------------------

        self.pnl_today_headers = ttk.Frame(self.exec_stack)
        self.pnl_today_headers.pack(fill="x")

        self.pnl_today_headers.columnconfigure(0, weight=1)
        self.pnl_today_headers.columnconfigure(1, weight=1)

        win_header = ttk.Frame(self.pnl_today_headers, padding=6, relief="ridge")
        win_header.grid(row=0, column=0, padx=4, pady=4, sticky="ew")

        ttk.Label(
            win_header,
            text="🟢 TOP WINNERS",
            font=("TkDefaultFont",9,"bold")
        ).pack(anchor="w")

        loss_header = ttk.Frame(self.pnl_today_headers, padding=6, relief="ridge")
        loss_header.grid(row=0, column=1, padx=4, pady=4, sticky="ew")

        ttk.Label(
            loss_header,
            text="🔴 TOP LOSERS",
            font=("TkDefaultFont",9,"bold")
        ).pack(anchor="w")

        # --------------------------------------------------
        # WIN / LOSS CARD GRID
        # --------------------------------------------------

        self.pnl_today_grid = ttk.Frame(self.exec_stack)
        self.pnl_today_grid.pack(fill="x")

        self.pnl_today_grid.columnconfigure(0, weight=1)
        self.pnl_today_grid.columnconfigure(1, weight=1)

        # --------------------------------------------------
        # ⚡ LIVE EXPOSURE HEADER
        # --------------------------------------------------

        self.live_exposure_header = ttk.Frame(self.exec_stack, padding=8, relief="ridge")
        self.live_exposure_header.pack(fill="x", pady=6)

        ttk.Label(
            self.live_exposure_header,
            text="⚡ NEXT MARKETS — LIVE EXPOSURE",
            font=("TkDefaultFont",10,"bold")
        ).pack(anchor="w")

        # --------------------------------------------------
        # LIVE WIN / LOSS HEADERS
        # --------------------------------------------------

        self.live_headers = ttk.Frame(self.exec_stack)
        self.live_headers.pack(fill="x")

        self.live_headers.columnconfigure(0, weight=1)
        self.live_headers.columnconfigure(1, weight=1)

        live_win_header = ttk.Frame(self.live_headers, padding=6, relief="ridge")
        live_win_header.grid(row=0, column=0, padx=4, pady=4, sticky="ew")

        ttk.Label(
            live_win_header,
            text="🟢 POTENTIAL WINNERS",
            font=("TkDefaultFont",9,"bold")
        ).pack(anchor="w")

        live_loss_header = ttk.Frame(self.live_headers, padding=6, relief="ridge")
        live_loss_header.grid(row=0, column=1, padx=4, pady=4, sticky="ew")

        ttk.Label(
            live_loss_header,
            text="🔴 POTENTIAL LOSERS",
            font=("TkDefaultFont",9,"bold")
        ).pack(anchor="w")

        # --------------------------------------------------
        # LIVE GRID
        # --------------------------------------------------

        self.live_exposure_grid = ttk.Frame(self.exec_stack)
        self.live_exposure_grid.pack(fill="x")

        self.live_exposure_grid.columnconfigure(0, weight=1)
        self.live_exposure_grid.columnconfigure(1, weight=1)


        # --------------------------------------------------
        # 🧠 V7 MARKET LIFECYCLE
        # --------------------------------------------------

        self.lifecycle_header = ttk.Frame(self.exec_stack, padding=8, relief="ridge")
        self.lifecycle_header.pack(fill="x", pady=6)

        ttk.Label(
            self.lifecycle_header,
            text="🧠 V7 MARKET LIFECYCLE",
            font=("TkDefaultFont", 11, "bold")
        ).pack(anchor="w")

        # ⚠ DELAYED MARKET
        self.delayed_card = ttk.Frame(self.exec_stack, padding=8, relief="ridge")
        self.delayed_card.pack(fill="x", pady=4)

        self.delayed_vars = {
            "market": tk.StringVar(value="Market: —"),
            "state":  tk.StringVar(value="State: —"),
            "time":   tk.StringVar(value="—"),
        }

        ttk.Label(
            self.delayed_card,
            text="⚠ DELAYED MARKET",
            font=("TkDefaultFont",10,"bold")
        ).pack(anchor="w")

        ttk.Label(
            self.delayed_card,
            textvariable=self.delayed_vars["market"]
        ).pack(anchor="w")

        ttk.Label(
            self.delayed_card,
            textvariable=self.delayed_vars["state"]
        ).pack(anchor="w")

        ttk.Label(
            self.delayed_card,
            textvariable=self.delayed_vars["time"]
        ).pack(anchor="w")


        # 🔥 CURRENT MARKET
        self.current_card = ttk.Frame(self.exec_stack, padding=8, relief="ridge")
        self.current_card.pack(fill="x", pady=4)

        self.current_vars = {
            "market": tk.StringVar(value="Market: —"),
            "state":  tk.StringVar(value="State: —"),
            "time":   tk.StringVar(value="Starts In: —"),
        }

        ttk.Label(
            self.current_card,
            text="🔥 CURRENT MARKET",
            font=("TkDefaultFont",10,"bold")
        ).pack(anchor="w")

        ttk.Label(
            self.current_card,
            textvariable=self.current_vars["market"]
        ).pack(anchor="w")

        ttk.Label(
            self.current_card,
            textvariable=self.current_vars["state"]
        ).pack(anchor="w")

        ttk.Label(
            self.current_card,
            textvariable=self.current_vars["time"]
        ).pack(anchor="w")


        # NEXT MARKET
        self.next_card = ttk.Frame(self.exec_stack, padding=8, relief="ridge")
        self.next_card.pack(fill="x", pady=4)

        self.next_vars = {
            "market": tk.StringVar(value="Market: —"),
            "state":  tk.StringVar(value="State: PRE"),
            "time":   tk.StringVar(value="Starts In: —"),
        }

        ttk.Label(
            self.next_card,
            text="NEXT MARKET",
            font=("TkDefaultFont",10,"bold")
        ).pack(anchor="w")

        ttk.Label(
            self.next_card,
            textvariable=self.next_vars["market"]
        ).pack(anchor="w")

        ttk.Label(
            self.next_card,
            textvariable=self.next_vars["state"]
        ).pack(anchor="w")

        ttk.Label(
            self.next_card,
            textvariable=self.next_vars["time"]
        ).pack(anchor="w")
        # ============================================================
        # RIGHT COLUMN (BANKSTATE + ROUTER + LIVE VIEW)
        # ============================================================
        right = ttk.Frame(container)
        right.grid(row=0, column=1, sticky="nsew", padx=6)

        # 🔥 CRITICAL: configure grid weights
        right.columnconfigure(0, weight=1)
        right.columnconfigure(1, weight=1)
        right.rowconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        # ------------------------------------------------------------
        # BANKSTATE (row 0, col 0)
        # ------------------------------------------------------------
        self.bank_frame = ttk.LabelFrame(right, text="BANKSTATE")
        self.bank_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 6))

        self.bank_cards = ttk.Frame(self.bank_frame)
        self.bank_cards.pack(fill="x", expand=False, padx=8, pady=8)
        self.bank_cards.pack(fill="both", expand=True, padx=8, pady=8)

        # ------------------------------------------------------------
        # ROUTER (row 0, col 1)
        # ------------------------------------------------------------
        self.router_frame = ttk.LabelFrame(right, text="ROUTER")
        self.router_frame.grid(row=0, column=1, sticky="nsew", pady=(0, 6))

        self.router_stack = ttk.Frame(self.router_frame)
        self.router_stack.pack(fill="both", expand=True, padx=8, pady=8)

        # ------------------------------------------------------------
        # LIVE VIEW (row 1, full width)
        # ------------------------------------------------------------
        self.live_view_frame = ttk.LabelFrame(right, text="LIVE VIEW")
        self.live_view_frame.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="nsew",
            padx=6,
            pady=6
        )

        self.live_view_container = ttk.Frame(self.live_view_frame)
        self.live_view_container.pack(fill="both", expand=True)

        # Layout inside LIVE VIEW
        self.live_view_container.columnconfigure(0, weight=1)
        self.live_view_container.columnconfigure(1, weight=3)
        self.live_view_container.rowconfigure(0, weight=1)

        # Overview (left)
        self.live_overview_card = ttk.Frame(
            self.live_view_container,
            padding=12,
            relief="ridge"
        )
        self.live_overview_card.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=6,
            pady=6
        )

        # 2x4 Runner Grid (right)
        self.live_grid = ttk.Frame(self.live_view_container)
        self.live_grid.grid(
            row=0,
            column=1,
            sticky="nsew",
            padx=6,
            pady=6
        )

        for c in range(4):
            self.live_grid.columnconfigure(c, weight=1)

        # === PATCH START ===
        # 📍 TARGET: gui/dashboard.py:_render_bankstate_report
        # 🛠 ACTION: compact Unified telemetry layout
        # PURPOSE:
        # Remove vertical gaps so telemetry sits directly under header
        # ===

        self.bank_cards.rowconfigure(0, weight=0)
        self.bank_cards.rowconfigure(1, weight=0)
        self.bank_cards.rowconfigure(2, weight=0)

        # === PATCH END ===

        self.live_view_container.columnconfigure(0, weight=1)
        self.live_view_container.columnconfigure(1, weight=3)

        self._refresh_execution_intelligence()

    def _refresh_execution_intelligence(self):

        import sqlite3
        from engines.config_paths import autoscalp_db

        try:
            con = _dashboard_con()
            con.row_factory = sqlite3.Row

            # Use the same race as V7 INPLAY LIVE STATE
            race = con.execute("""
                SELECT marketId, event_name, market_name, marketStartTime
                FROM betsdb.bets
                WHERE marketStartTime > datetime('now','utc')
                ORDER BY marketStartTime ASC
                LIMIT 1
            """).fetchone()

            bank_row = con.execute("""
                SELECT *
                FROM bankstate_runtime_snapshot
                WHERE date(ts) = date('now','utc')
                ORDER BY ts DESC
                LIMIT 1
            """).fetchone()

            row = con.execute("""
                SELECT *
                FROM router_runtime_snapshot
                WHERE date(ts) = date('now','utc')
                ORDER BY ts DESC
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
# === PATCH START ==============================================================
# 📍 TARGET: gui/dashboard.py
# 🔎 SEARCH: FROM bus_runtime_snapshot
# 🛠 ACTION: detect new snapshot and force immediate refresh
# 📆 PATCHED: 2026-03-05 — instant BUS telemetry update
# PURPOSE:
# - Remove dashboard lag when BUS starts
# - Update UI immediately when new snapshot arrives
# ==============================================================================
# === PATCH START ==============================================================
# 📍 TARGET: gui/dashboard.py
# 🔎 SEARCH: FROM bus_runtime_snapshot
# 🛠 ACTION: read BUS snapshot directly from memory
# 📆 PATCHED: 2026-03-05 — dashboard uses BUS in-memory telemetry
# PURPOSE:
# - eliminate SQLite polling
# - instantaneous dashboard updates
# ==============================================================================

            row = con.execute("""
                SELECT *
                FROM bus_runtime_snapshot
                ORDER BY ts DESC
                LIMIT 1
            """).fetchone()

            if row:

                row = dict(row)

                self.bus_vars["route"].set(f"Route ID: {row['route_id']}")
                self.bus_vars["stop"].set(f"Bus Stop: {row['bus_stop']}")
                self.bus_vars["tick"].set(f"Tick ID: {row['tick_id']}")
                self.bus_vars["hz"].set(f"Hz: {row['hz']}")

# ======================================================================================================
# 📍 TARGET: gui/dashboard.py
# 🔎 ANCHOR: inside _refresh_execution_intelligence(), after bus snapshot section
# 🧩 ACTION: Render runner intelligence from bankstate_runner_snapshot
# 📆 PATCHED: 2026-XX-XX — Dashboard wired to BankState runner snapshot
#
# PURPOSE
# -------
# Replace placeholder logic with snapshot-driven runner intelligence.
#
# DATA SOURCE
# -----------
# bankstate_runner_snapshot
#
# FIELDS
# ------
# ts
# marketId
# selectionId
# horse_name
# pnl_if_win
# engine
#
# PANELS
# ------
# 1️⃣ TODAY — REALISED P&L
#    Top 5 winners
#    Top 5 losers
#
# 2️⃣ NEXT MARKETS — LIVE EXPOSURE
#    Highest potential win
#    Highest potential loss
#
# INVARIANT
# ---------
# Dashboard NEVER recomputes PnL.
# It only reads the BankState snapshot.
# ======================================================================================================

            # --------------------------------------------------
            # RUNNER SNAPSHOT (BankState intelligence)
            # --------------------------------------------------

            runner_rows = con.execute("""
                SELECT
                    marketId,
                    selectionId,
                    horse_name,
                    pnl_if_win
                FROM bankstate_runner_snapshot
                WHERE ts = (
                    SELECT MAX(ts)
                    FROM bankstate_runner_snapshot
                )
            """).fetchall()

            # --------------------------------------------------
            # Clear existing cards
            # --------------------------------------------------

            for w in self.pnl_today_grid.winfo_children():
                w.destroy()

            for w in self.live_exposure_grid.winfo_children():
                w.destroy()

            if runner_rows:

                # -------------------------
                # Sort winners / losers
                # -------------------------

                winners = sorted(
                    runner_rows,
                    key=lambda r: float(r["pnl_if_win"]),
                    reverse=True
                )[:5]

                losers = sorted(
                    runner_rows,
                    key=lambda r: float(r["pnl_if_win"])
                )[:5]

                # -------------------------
                # WINNERS
                # -------------------------

                for i, r in enumerate(winners):

                    card = ttk.Frame(self.pnl_today_grid, padding=8, relief="ridge")
                    card.grid(row=i, column=0, padx=4, pady=4, sticky="ew")

                    ttk.Label(
                        card,
                        text=r["horse_name"],
                        font=("TkDefaultFont",9,"bold")
                    ).pack(anchor="w")

                    ttk.Label(
                        card,
                        text=f"£{float(r['pnl_if_win']):.2f}",
                        foreground="#2ecc71"
                    ).pack(anchor="w")

                # -------------------------
                # LOSERS
                # -------------------------

                for i, r in enumerate(losers):

                    card = ttk.Frame(self.pnl_today_grid, padding=8, relief="ridge")
                    card.grid(row=i, column=1, padx=4, pady=4, sticky="ew")

                    ttk.Label(
                        card,
                        text=r["horse_name"],
                        font=("TkDefaultFont",9,"bold")
                    ).pack(anchor="w")

                    ttk.Label(
                        card,
                        text=f"£{float(r['pnl_if_win']):.2f}",
                        foreground="#e74c3c"
                    ).pack(anchor="w")

                # --------------------------------------------------
                # LIVE EXPOSURE (next markets)
                # --------------------------------------------------

                potential_win = sorted(
                    runner_rows,
                    key=lambda r: float(r["pnl_if_win"]),
                    reverse=True
                )[:5]

                potential_loss = sorted(
                    runner_rows,
                    key=lambda r: float(r["pnl_if_win"])
                )[:5]

                for i, r in enumerate(potential_win):

                    card = ttk.Frame(self.live_exposure_grid, padding=8, relief="ridge")
                    card.grid(row=i, column=0, padx=4, pady=4, sticky="ew")

                    ttk.Label(
                        card,
                        text=r["horse_name"],
                        font=("TkDefaultFont",9,"bold")
                    ).pack(anchor="w")

                    ttk.Label(
                        card,
                        text=f"£{float(r['pnl_if_win']):.2f}",
                        foreground="#2ecc71"
                    ).pack(anchor="w")

                for i, r in enumerate(potential_loss):

                    card = ttk.Frame(self.live_exposure_grid, padding=8, relief="ridge")
                    card.grid(row=i, column=1, padx=4, pady=4, sticky="ew")

                    ttk.Label(
                        card,
                        text=r["horse_name"],
                        font=("TkDefaultFont",9,"bold")
                    ).pack(anchor="w")

                    ttk.Label(
                        card,
                        text=f"£{float(r['pnl_if_win']):.2f}",
                        foreground="#e74c3c"
                    ).pack(anchor="w")

                # --------------------------------------------------
                # BUS tick synchronisation
                # --------------------------------------------------
                tick = row["tick_id"]

                if tick != getattr(self, "_last_bus_tick", None):
                    self._last_bus_tick = tick
                    self.after(10, self._refresh_execution_intelligence)


# === PATCH START ==============================================================
# 📍 TARGET: gui/dashboard.py
# 🔎 SEARCH: Fill Rate
# 🛠 ACTION: compute fill rate from raw counters
# 📆 PATCHED: 2026-03-05 — dashboard derives fill rate
# ==============================================================================

                generated = row["plans_generated"] if "plans_generated" in row.keys() else 0
                routed = row["plans_routed"] if "plans_routed" in row.keys() else 0

                rate = (routed / generated * 100.0) if generated else 0.0

                self.bus_vars["fill"].set(
                    f"Plans: {generated}  Routed: {routed}  Fill: {rate:.1f}%"
                )

            # ─────────────────────────────────────────
            # INPLAY SNAPSHOT (Unified Authority)
            # ─────────────────────────────────────────

            unified_row = con.execute("""
                SELECT *
                FROM unified_runtime_snapshot
                ORDER BY ts DESC
                LIMIT 1
            """).fetchone()

            # --------------------------------------------------
            # UNIFIED MARKET LIFECYCLE (Dashboard Cards)
            # --------------------------------------------------

            unified_row = con.execute("""
                SELECT *
                FROM unified_runtime_snapshot
                ORDER BY ts DESC
                LIMIT 1
            """).fetchone()

            if unified_row:

                now_utc = datetime.now(timezone.utc)

                # --------------------------------------------------
                # ⚠ DELAYED MARKET
                # --------------------------------------------------

                delayed_market_id = unified_row["delayed_market_id"]

                if delayed_market_id:

                    self.delayed_card.pack(fill="x", pady=4)

                    r = con.execute("""
                        SELECT event_name, market_name, marketStartTime
                        FROM betsdb.bets
                        WHERE marketId = ?
                        LIMIT 1
                    """,(delayed_market_id,)).fetchone()

                    if r:

                        off = datetime.fromisoformat(
                            r["marketStartTime"].replace("Z","+00:00")
                        ).strftime("%H:%M")

                        self.current_vars["market"].set(
                            f"{r['event_name']} {r['market_name']}  ({off})"
                        )

                        mto = minutes_to_off(r["marketStartTime"], now_utc)

                        self.delayed_vars["market"].set(
                            f"{r['event_name']} {r['market_name']}  ({off})"
                        )

                        self.delayed_vars["state"].set("State: DELAYED")

                        if mto is not None:
                            mins = int(mto)
                            secs = int((mto-mins)*60)
                            self.delayed_vars["time"].set(f"{mins}m {secs}s")

                else:
                    self.delayed_card.pack_forget()

                # --------------------------------------------------
                # 🔥 CURRENT MARKET
                # --------------------------------------------------

                current_market_id = unified_row["current_market_id"]
                current_market_state = unified_row["current_market_state"]

                if current_market_id:

                    r = con.execute("""
                        SELECT event_name, market_name, marketStartTime
                        FROM betsdb.bets
                        WHERE marketId = ?
                        LIMIT 1
                    """,(current_market_id,)).fetchone()

                    if r:

                        mto = minutes_to_off(r["marketStartTime"], now_utc)

                        self.current_vars["market"].set(
                            f"{r['event_name']} {r['market_name']}"
                        )

                        self.current_vars["state"].set(
                            f"State: {current_market_state}"
                        )

                        if mto is not None:

                            mins = int(mto)
                            secs = int((mto - mins) * 60)

                            self.current_vars["time"].set(
                                f"Starts In: {mins}m {secs}s"
                            )

                else:

                    self.current_vars["market"].set("Market: —")
                    self.current_vars["state"].set("State: —")
                    self.current_vars["time"].set("Starts In: —")

                # --------------------------------------------------
                # NEXT MARKET
                # --------------------------------------------------

                next_market_id = unified_row["next_market_id"]

                if not next_market_id:
                    nxt = con.execute("""
                        SELECT marketId
                        FROM betsdb.bets
                        WHERE marketStartTime > datetime('now','utc')
                        ORDER BY marketStartTime ASC
                        LIMIT 2
                    """).fetchall()

                    if len(nxt) == 2:
                        next_market_id = nxt[1]["marketId"]

                if next_market_id:

                    r = con.execute("""
                        SELECT event_name, market_name, marketStartTime
                        FROM betsdb.bets
                        WHERE marketId = ?
                        LIMIT 1
                    """,(next_market_id,)).fetchone()

                    if r:

                        off = datetime.fromisoformat(
                            r["marketStartTime"].replace("Z","+00:00")
                        ).strftime("%H:%M")

                        mto = minutes_to_off(r["marketStartTime"], now_utc)

                        self.next_vars["market"].set(
                            f"{r['event_name']} {r['market_name']}  ({off})"
                        )

                        self.next_vars["state"].set("State: PRE")

                        if mto is not None:
                            mins = int(mto)
                            secs = int((mto-mins)*60)
                            self.next_vars["time"].set(f"Starts In: {mins}m {secs}s")

                else:
                    self.next_vars["market"].set("Market: —")
                    self.next_vars["state"].set("State: PRE")
                    self.next_vars["time"].set("Starts In: —")


            if unified_row:


                # --------------------------------------------------
                # Find next race (dashboard timing control)
                # --------------------------------------------------
                race = con.execute("""
                    SELECT marketId, event_name, market_name, marketStartTime
                    FROM betsdb.bets
                    WHERE marketStartTime > datetime('now','utc')
                    ORDER BY marketStartTime ASC
                    LIMIT 1
                """).fetchone()

                if race:

                    mto = minutes_to_off(race["marketStartTime"])

                    if mto is not None:
                        mins = int(mto)
                        secs = int((mto - mins) * 60)
                    else:
                        mins = 0
                        secs = 0

                    # Map legacy timing update to CURRENT MARKET lifecycle card

                    self.current_vars["market"].set(
                        f"{race['event_name']} {race['market_name']}"
                    )

                    self.current_vars["state"].set("State: PRE")

                    self.current_vars["time"].set(
                        f"Starts In: {mins}m {secs}s"
                    )

                    # === PATCH START ==============================================================
                    # 📍 TARGET: gui/dashboard.py
                    # 🔎 SEARCH: FROM betsdb.bets WHERE marketId = ?
                    # 🛠 ACTION: Upcoming race runners panel
                    # PURPOSE:
                    # - Show the four runners in the next upcoming race
                    # - Pull px from BUS snapshot if available
                    # ==============================================================================

                    state = get_live_view_state() or {}

                    lowest_odds = state.get("lowest_odds", [])
                    most_money = state.get("most_money", [])

                    display = lowest_odds + most_money

                    for i, r in enumerate(display):

                        card = ttk.Frame(
                            self.live_grid,
                            padding=8,
                            relief="ridge"
                        )

                        card.grid(
                            row=i // 4,
                            column=i % 4,
                            padx=6,
                            pady=6,
                            sticky="nsew"
                        )

                        ttk.Label(
                            card,
                            text=r["horse_name"],
                            font=("TkDefaultFont",9,"bold")
                        ).pack(anchor="w")

                        ttk.Label(
                            card,
                            text=f"Odds: {float(r['odds']):.2f}"
                        ).pack(anchor="w")

                        ttk.Label(
                            card,
                            text="PnL if Win: £0.00"
                        ).pack(anchor="w")

                    # === PATCH END ================================================================

                else:

                    ttk.Label(
                        self.inplay_runner_grid,
                        text="No market currently in-play",
                        font=("TkDefaultFont", 10, "italic")
                    ).grid(row=0, column=0, columnspan=2, pady=8)

            # ─────────────────────────────────────────
            # LIVE VIEW PANEL (DYNAMIC RACE VIEW)
            # ─────────────────────────────────────────

            # Clear previous cards
            for w in self.live_grid.winfo_children():
                w.destroy()

            for w in self.live_overview_card.winfo_children():
                w.destroy()

            # --------------------------------------------------
            # SELECT RACE FOR LIVE VIEW (snapshot-aware)
            # --------------------------------------------------



            if race:

                mid = race["marketId"]

                # -------------------------------
                # LIVE VIEW OVERVIEW PANEL
                # -------------------------------

                runners_total = con.execute("""
                    SELECT COUNT(DISTINCT selectionId)
                    FROM betsdb.bets
                    WHERE marketId = ?
                """, (race["marketId"],)).fetchone()[0]

                spread = con.execute("""
                    SELECT MIN(px) AS lo, MAX(px) AS hi
                    FROM bus_route_runtime_snapshot
                    WHERE marketId = ?
                """, (race["marketId"],)).fetchone()

                fav = con.execute("""
                    SELECT horse_name
                    FROM bus_route_runtime_snapshot
                    WHERE marketId = ?
                    ORDER BY px ASC
                    LIMIT 1
                """, (race["marketId"],)).fetchone()

                spread_txt = "—"
                if spread and spread["lo"] and spread["hi"]:
                    spread_txt = f"{spread['lo']:.2f} → {spread['hi']:.0f}"

                fav_name = fav["horse_name"] if fav else "—"

                # --------------------------------------------------
                # MARKET PnL (if this market settles now)
                # --------------------------------------------------
                market_pnl = con.execute("""
                    SELECT SUM(
                        CASE
                            WHEN side='LAY' THEN entry_stake
                            WHEN side='BACK' THEN -entry_stake
                            ELSE 0
                        END
                    )
                    FROM orders
                    WHERE marketId=?
                      AND role='PARENT'
                      AND entry_status='MATCHED'
                      AND date(opened_at)=date('now','utc')
                """, (race["marketId"],)).fetchone()[0]

                market_pnl = float(market_pnl or 0.0)

                overview_lines = [
                    "Next Race",
                    f"{race['event_name']} — {race['market_name']}",
                    "",
                    f"Runners: {runners_total}",
                    f"Spread: {spread_txt}",
                    "",
                    f"Favourite: {fav_name}",
                    f"Market PnL: £{market_pnl:.2f}",
                    "Steam Bias: —",
                    "Liquidity: —"
                ]

                for line in overview_lines:
                    ttk.Label(
                        self.live_overview_card,
                        text=line,
                        font=("TkDefaultFont",10),
                        anchor="w"
                    ).pack(anchor="w")

# === PATCH START ==============================================================
# 📍 TARGET: gui/dashboard.py
# 🔎 SEARCH: COALESCE(px, odds, 0) AS px
# 🛠 ACTION: Populate live view runners from BUS snapshot
# 📆 PATCHED: 2026-03-05 — Live View powered by route ctx
# PURPOSE:
# - Remove invalid px column usage
# - Align dashboard with execution state
# ==============================================================================

                runners = con.execute("""
                    SELECT
                        marketId,
                        selectionId,
                        horse_name,
                        px,
                        0 AS pnl_if_win
                    FROM bus_route_runtime_snapshot
                    WHERE marketId = ?
                    ORDER BY ABS(px - 7.0)
                    LIMIT 4
                """, (race["marketId"],)).fetchall()

                if not runners:

                    runners = con.execute("""
                        SELECT
                            marketId,
                            selectionId,
                            horse_name,
                            0 AS px,
                            0 AS pnl_if_win
                        FROM betsdb.bets
                        WHERE marketId = ?
                          AND date(marketStartTime) = date('now','utc')
                        GROUP BY selectionId, horse_name
                        LIMIT 4
                    """, (race["marketId"],)).fetchall()
# === PATCH END ================================================================

                if runners:

                    # ------------------------------
                    # ranking sets
                    # ------------------------------

                    by_odds = sorted(
                        runners,
                        key=lambda r: float(r["px"] or 999)
                    )[:4]

                    by_pnl = sorted(
                        runners,
                        key=lambda r: (
                            -float(r["pnl_if_win"] or 0),   # primary → max pnl
                            float(r["px"] or 999)           # secondary → lowest odds
                        )
                    )[:4]

                    display = by_odds + by_pnl

                    for i, r in enumerate(display):

                        horse = r["horse_name"]
                        px = float(r["px"] or 0)

                        # --------------------------------------------------
                        # PnL IF THIS RUNNER WINS
                        # --------------------------------------------------
                        pnl_if_win = con.execute("""
                            SELECT SUM(
                                CASE
                                    WHEN side='LAY' AND selectionId=? THEN -entry_stake*(entry_odds-1)
                                    WHEN side='LAY' AND selectionId!=? THEN entry_stake
                                    WHEN side='BACK' AND selectionId=? THEN entry_stake*(entry_odds-1)
                                    WHEN side='BACK' AND selectionId!=? THEN -entry_stake
                                    ELSE 0
                                END
                            )
                            FROM orders
                            WHERE marketId=?
                              AND role='PARENT'
                              AND entry_status='MATCHED'
                              AND date(opened_at)=date('now','utc')
                        """, (
                            r["selectionId"],
                            r["selectionId"],
                            r["selectionId"],
                            r["selectionId"],
                            race["marketId"]
                        )).fetchone()[0]

                        pnl_if_win = float(pnl_if_win or 0.0)

                        mm = con.execute("""
                            SELECT band, rank
                            FROM market_monitor_snapshot
                            WHERE marketId = ?
                            AND selectionId = ?
                            ORDER BY ts DESC
                            LIMIT 1
                        """, (race["marketId"], r["selectionId"])).fetchone()

                        band = mm["band"] if mm else "—"
                        rank = mm["rank"] if mm else "—"

                        card = ttk.Frame(
                            self.live_grid,
                            padding=8,
                            relief="ridge"
                        )

                        card.grid(
                            row=i // 4,
                            column=i % 4,
                            padx=6,
                            pady=6,
                            sticky="nsew"
                        )

                        ttk.Label(
                            card,
                            text=horse,
                            font=("TkDefaultFont",9,"bold")
                        ).pack(anchor="w")

                        ttk.Label(
                            card,
                            text=f"Odds: {px:.2f}"
                        ).pack(anchor="w")

                        ttk.Label(
                            card,
                            text=f"Band: {band}"
                        ).pack(anchor="w")

                        ttk.Label(
                            card,
                            text=f"Rank: {rank}"
                        ).pack(anchor="w")

                        ttk.Label(
                            card,
                            text=f"PnL if Win: £{pnl_if_win:.2f}"
                        ).pack(anchor="w")


            con.close()

        except Exception as e:
            import traceback
            traceback.print_exc()
            print("Execution Intelligence Error:", e)

        # --- FORCE GEOMETRY REFRESH (prevents resize requirement) ---
        try:
            self.update_idletasks()
        except Exception:
            pass

        self.after(500, self._refresh_execution_intelligence)

# === PATCH START ==============================================================
# 📍 TARGET: gui/dashboard.py
# 🔎 SEARCH: def _render_router_report(self, con):
# 🛠 ACTION: Replace ASCII router render with stacked card layout
# 📆 PATCHED: 2026-03-02 — Router V1 Stacked Card Renderer
# PURPOSE:
# - Preserve table format
# - Preserve emojis
# - Preserve trade summary
# - Match BankState card style
# ==============================================================================

    def _render_router_report(self, con):

        rows = con.execute("""
            SELECT *
            FROM router_runtime_snapshot
            WHERE date(ts) = date('now','utc')
            ORDER BY ts DESC
        """).fetchall()

        # Clear previous stack
        for w in self.router_stack.winfo_children():
            w.destroy()

        if not rows:
            ttk.Label(
                self.router_stack,
                text="No trades yet today.",
                font=("TkDefaultFont", 10, "italic")
            ).pack(anchor="w")
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
        # Compute totals
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

        total_matched = sum(p.get("MATCHED",0) for p in parents.values()) + \
                        sum(c.get("MATCHED",0) for c in children.values())

        total_closed = sum(p.get("CLOSED",0) for p in parents.values()) + \
                       sum(c.get("CLOSED",0) for c in children.values())

        # --------------------------------------------------
        # 🟢 ROUTER LIVE STATE CARD
        # --------------------------------------------------

        state_icon = "🟢"
        if total_open > 150:
            state_icon = "🔥"
        elif total_open > 75:
            state_icon = "⚡"

        state_card = ttk.Frame(self.router_stack, padding=10, relief="ridge")
        state_card.pack(fill="x", pady=6)

        ttk.Label(
            state_card,
            text=f"{state_icon} V7 ROUTER LIVE STATE",
            font=("TkDefaultFont", 11, "bold")
        ).pack(anchor="w")

        # --------------------------------------------------
        # 📦 PARENTS HEADER CARD
        # --------------------------------------------------

        parents_header = ttk.Frame(self.router_stack, padding=8, relief="ridge")
        parents_header.pack(fill="x", pady=6)

        ttk.Label(
            parents_header,
            text="📦 PARENTS — ENTRY / EXIT STATUS (BY ENGINE)",
            font=("TkDefaultFont", 10, "bold")
        ).pack(anchor="w")

        # --------------------------------------------------
        # 📦 PARENTS DATA CARD
        # --------------------------------------------------

        parents_data = ttk.Frame(self.router_stack, padding=8, relief="ridge")
        parents_data.pack(fill="x", pady=6)

        table = ttk.Frame(parents_data)
        table.pack(fill="x")

        columns = ["ENGINE", "QUEUED", "PLACING", "PLACED", "MATCHED", "CANCELLED", "CLOSED"]

        # Configure columns to stretch evenly
        for col in range(len(columns)):
            table.columnconfigure(col, weight=1)

        # Header row
        for col, name in enumerate(columns):
            ttk.Label(
                table,
                text=name,
                font=("TkDefaultFont", 9, "bold")
            ).grid(row=0, column=col, sticky="nsew", padx=4, pady=2)

        # Data rows
        for row_idx, engine in enumerate(sorted(parents.keys()), start=1):
            p = parents[engine]

            values = [
                engine,
                p.get("QUEUED", 0),
                p.get("PLACING", 0),
                p.get("PLACED", 0),
                p.get("MATCHED", 0),
                p.get("CANCELLED", 0),
                p.get("CLOSED", 0),
            ]

            for col, value in enumerate(values):
                ttk.Label(
                    table,
                    text=str(value)
                ).grid(row=row_idx, column=col, sticky="nsew", padx=4, pady=1)

        # --------------------------------------------------
        # 👶 CHILDREN HEADER CARD
        # --------------------------------------------------

        children_header = ttk.Frame(self.router_stack, padding=8, relief="ridge")
        children_header.pack(fill="x", pady=6)

        ttk.Label(
            children_header,
            text="👶 CHILDREN — ENTRY / EXIT STATUS (BY ENGINE)",
            font=("TkDefaultFont", 10, "bold")
        ).pack(anchor="w")

        # --------------------------------------------------
        # 👶 CHILDREN DATA CARD
        # --------------------------------------------------

        children_data = ttk.Frame(self.router_stack, padding=8, relief="ridge")
        children_data.pack(fill="x", pady=6)

        table = ttk.Frame(children_data)
        table.pack(fill="x")

        for col in range(len(columns)):
            table.columnconfigure(col, weight=1)

        # Header row
        for col, name in enumerate(columns):
            ttk.Label(
                table,
                text=name,
                font=("TkDefaultFont", 9, "bold")
            ).grid(row=0, column=col, sticky="nsew", padx=4, pady=2)

        # Data rows
        for row_idx, engine in enumerate(sorted(children.keys()), start=1):
            c = children[engine]

            values = [
                engine,
                c.get("QUEUED", 0),
                c.get("PLACING", 0),
                c.get("PLACED", 0),
                c.get("MATCHED", 0),
                c.get("CANCELLED", 0),
                c.get("CLOSED", 0),
            ]

            for col, value in enumerate(values):
                ttk.Label(
                    table,
                    text=str(value)
                ).grid(row=row_idx, column=col, sticky="nsew", padx=4, pady=1)    
        # --------------------------------------------------
        # ⚡ TRADE SUMMARY CARD
        # --------------------------------------------------

        summary_card = ttk.Frame(self.router_stack, padding=10, relief="ridge")
        summary_card.pack(fill="x", pady=6)

        ttk.Label(
            summary_card,
            text="⚡ TRADE SUMMARY",
            font=("TkDefaultFont", 10, "bold")
        ).pack(anchor="w")

        ttk.Label(summary_card, text=f"Total Open     : ⚡ {total_open}").pack(anchor="w")
        ttk.Label(summary_card, text=f"Total Matched  : ✅ {total_matched}").pack(anchor="w")
        ttk.Label(summary_card, text=f"Total Closed   : {total_closed}").pack(anchor="w")

        btn = ttk.Button(
            summary_card,
            text="Open Full Dashboard",
            command=self._open_full_dashboard
        )

        btn.pack(anchor="e", pady=(6,0))

# === PATCH END ==============================================================
# === PATCH START ==============================================================
# 📍 TARGET: gui/dashboard.py
# 🔎 SEARCH: def _render_bankstate_report(self, con):
# 🛠 ACTION: Replace ASCII renderer with 3x2 card grid renderer
# 📆 PATCHED: 2026-03-02 — BankState Card Rendering
# PURPOSE:
# - Render engine capital as cards
# - Show Pot / Matched (FLOOR) / Reserved / Headroom
# - Remove negative AVAIL alarm semantics
# ==============================================================================

    def _render_bankstate_report(self, con):

        rows = con.execute("""
            SELECT *
            FROM bankstate_engine_snapshot
            WHERE date(ts) = date('now','utc')
              AND ts = (
                  SELECT MAX(ts)
                  FROM bankstate_engine_snapshot
                  WHERE date(ts) = date('now','utc')
              )
        """).fetchall()

# === PATCH START ==============================================================
# 📍 TARGET: gui/dashboard.py
# 🔎 SEARCH: for w in self.bank_cards.winfo_children():
# 🛠 ACTION: Insert structured GLOBAL CAPITAL OVERVIEW header above cards
# 📆 PATCHED: 2026-03-02 — Add BankState Overview Header
# PURPOSE:
# - Restore global summary
# - Keep it structured
# - Keep inside BANKSTATE block
# ==============================================================================

        # Clear previous cards
        for w in self.bank_cards.winfo_children():
            w.destroy()

        # ─────────────────────────────────────────────
        # GLOBAL CAPITAL OVERVIEW (Top Section)
        # ─────────────────────────────────────────────

# ======================================================================================================
# 📍 TARGET: gui/dashboard.py:_render_bankstate_report
# 🔎 SEARCH: unified_row = con.execute(
# 🧩 ACTION: ADD unified header
# 📆 PATCHED: 2026-03-12
# ======================================================================================================

        # === PATCH START ===
        # 📍 TARGET: gui/dashboard.py:_render_bankstate_report
        # 🔎 SEARCH: header = ttk.Frame(self.bank_cards
        # 🛠 ACTION: convert unified header to compact label frame
        # 📆 PATCHED: 2026-03-12
        # PURPOSE:
        # Make the Unified Engine header match BUS/ROUTER header height
        # (compact strip, no vertical expansion)

        # === PATCH START ===
        # 📍 TARGET: gui/dashboard.py:_render_bankstate_report
        # 🔎 SEARCH: V7 UNIFIED ENGINE STATS
        # 🛠 ACTION: match header style with BUS / ROUTER headers
        # 📆 PATCHED: 2026-03-12

        unified_header = ttk.Frame(self.bank_cards, padding=8, relief="ridge")
        unified_header.grid(row=0, column=0, columnspan=3, padx=8, pady=6, sticky="ew")

        ttk.Label(
            unified_header,
            text="🧠 V7 UNIFIED ENGINE STATS",
            font=("TkDefaultFont", 11, "bold")
         ).pack(anchor="w")

        # === PATCH END ===

        # prevent vertical stretch
        self.bank_cards.rowconfigure(0, weight=0)

        # === PATCH END ===
# ======================================================================================================
# 📍 TARGET: gui/dashboard.py:_render_bankstate_report
# 🔎 SEARCH: # GLOBAL CAPITAL OVERVIEW (Top Section)
# 🧩 ACTION: REPLACE — Unified Intelligence Dashboard (6 panels)
# 📆 PATCHED: 2026-03-12
#
# PURPOSE
# -------
# Replace the old Global Capital Overview with Unified Engine telemetry.
#
# PANELS
# ------
# 1. Timing
# 2. Volatility
# 3. Drift
# 4. Structure
# 5. Signal Intelligence
# 6. P&L per Engine
#
# DESIGN
# ------
# Uses the same card style as existing BankState panels.
# Layout: 3 × 2 grid.
#
# SAFE
# ----
# Read-only dashboard surface.
# No effect on trading.
# ======================================================================================================

        unified_row = con.execute("""
            SELECT *
            FROM unified_runtime_snapshot
            ORDER BY ts DESC
            LIMIT 1
        """).fetchone()

        if unified_row:

            # --------------------------------------------------
            # UPDATE MARKET LIFECYCLE CARDS
            # --------------------------------------------------

            now_utc = datetime.now(timezone.utc)

            # ⚠ DELAYED MARKET
            delayed_market_id = unified_row["delayed_market_id"]

            if delayed_market_id:
                r = con.execute("""
                    SELECT event_name, market_name, marketStartTime
                    FROM betsdb.bets
                    WHERE marketId = ?
                    LIMIT 1
                """,(delayed_market_id,)).fetchone()

                if r:
                    mto = minutes_to_off(r["marketStartTime"], now_utc)

                    self.delayed_vars["market"].set(
                        f"{r['event_name']} {r['market_name']}"
                    )

                    self.delayed_vars["state"].set("State: DELAYED")

                    if mto is not None:
                        mins = int(mto)
                        secs = int((mto-mins)*60)
                        self.delayed_vars["time"].set(f"{mins}m {secs}s")
            else:
                self.delayed_vars["market"].set("Market: —")
                self.delayed_vars["state"].set("State: —")
                self.delayed_vars["time"].set("—")


            # 🔥 CURRENT MARKET
            current_market_id = unified_row["current_market_id"]
            current_market_state = unified_row["current_market_state"]

            if current_market_id:
                r = con.execute("""
                    SELECT event_name, market_name, marketStartTime
                    FROM betsdb.bets
                    WHERE marketId = ?
                    LIMIT 1
                """,(current_market_id,)).fetchone()

                if r:
                    mto = minutes_to_off(r["marketStartTime"], now_utc)

                    self.current_vars["market"].set(
                        f"{r['event_name']} {r['market_name']}"
                    )

                    self.current_vars["state"].set(f"State: {current_market_state}")

                    if mto is not None:
                        mins = int(mto)
                        secs = int((mto-mins)*60)
                        self.current_vars["time"].set(f"Starts In: {mins}m {secs}s")
            else:
                self.current_vars["market"].set("Market: —")
                self.current_vars["state"].set("State: —")
                self.current_vars["time"].set("Starts In: —")


            # NEXT MARKET
            next_market_id = unified_row["next_market_id"]

            if next_market_id:
                r = con.execute("""
                    SELECT event_name, market_name, marketStartTime
                    FROM betsdb.bets
                    WHERE marketId = ?
                    LIMIT 1
                """,(next_market_id,)).fetchone()

                if r:
                    mto = minutes_to_off(r["marketStartTime"], now_utc)

                    self.next_vars["market"].set(
                        f"{r['event_name']} {r['market_name']}"
                    )

                    self.next_vars["state"].set("State: PRE")

                    if mto is not None:
                        mins = int(mto)
                        secs = int((mto-mins)*60)
                        self.next_vars["time"].set(f"Starts In: {mins}m {secs}s")
            else:
                self.next_vars["market"].set("Market: —")
                self.next_vars["state"].set("State: PRE")
                self.next_vars["time"].set("Starts In: —")

            report = None

            try:
                import json
                report = json.loads(unified_row["report_json"])
            except Exception:
                report = None



            # configure responsive grid
            for c in range(3):
                self.bank_cards.columnconfigure(c, weight=1, uniform="col")

            for r in range(2):
                self.bank_cards.rowconfigure(r, weight=0)

            # --------------------------------------------------
            # TIMING
            # --------------------------------------------------

            timing_card = ttk.Frame(self.bank_cards, padding=12, relief="ridge")
            timing_card.grid(row=1, column=2, padx=8, pady=8, sticky="ew")

            ttk.Label(
                timing_card,
                text="⏱ TIMING",
                font=("TkDefaultFont",10,"bold")
            ).pack(anchor="w")

            markets = report.get("timing", {}).get("markets", []) if report else []
            ttk.Label(
                timing_card,
                text=f"Markets tracked: {len(markets)}"
            ).pack(anchor="w")

            # --------------------------------------------------
            # VOLATILITY
            # --------------------------------------------------

            vol = report.get("volatility", {}) if report else {}

            card = ttk.Frame(self.bank_cards, padding=12, relief="ridge")
            card.grid(row=1, column=1, padx=8, pady=8, sticky="ew")

            ttk.Label(card, text="🌊 VOLATILITY",
                      font=("TkDefaultFont",10,"bold")).pack(anchor="w")

            ttk.Label(
                card,
                text=f"Runners moved: {vol.get('runners_moved_last_window',0)}"
            ).pack(anchor="w")

            ttk.Label(
                card,
                text=f"Energy: {vol.get('structural_energy','LOW')}"
            ).pack(anchor="w")

            # --------------------------------------------------
            # DRIFT
            # --------------------------------------------------

            drift_rows = report.get("drift", {}).get("runners", []) if report else []

            card = ttk.Frame(self.bank_cards, padding=12, relief="ridge")
            card.grid(row=2, column=1, padx=8, pady=8, sticky="ew")

            ttk.Label(card, text="📉 DRIFT",
                      font=("TkDefaultFont",10,"bold")).pack(anchor="w")

            ttk.Label(card, text=f"Runners tracked: {len(drift_rows)}").pack(anchor="w")

            # --------------------------------------------------
            # STRUCTURE
            # --------------------------------------------------

            sweet_rows = report.get("sweet_spot", {}).get("runners", []) if report else []

            card = ttk.Frame(self.bank_cards, padding=12, relief="ridge")
            card.grid(row=2, column=0, padx=8, pady=8, sticky="ew")

            ttk.Label(card, text="🧠 STRUCTURE",
                      font=("TkDefaultFont",10,"bold")).pack(anchor="w")

            ttk.Label(card, text=f"Sweet spot runners: {len(sweet_rows)}").pack(anchor="w")

            # --------------------------------------------------
            # SIGNAL INTELLIGENCE
            # --------------------------------------------------

            layer2 = report.get("layer2", {}) if report else {}

            card = ttk.Frame(self.bank_cards, padding=12, relief="ridge")
            card.grid(row=2, column=2, padx=8, pady=8, sticky="ew")

            ttk.Label(card, text="📊 SIGNAL INTEL",
                      font=("TkDefaultFont",10,"bold")).pack(anchor="w")

            candidates = layer2.get("candidates", [])
            ttk.Label(card, text=f"Candidates: {len(candidates)}").pack(anchor="w")

            # --------------------------------------------------
            # P&L PER ENGINE
            # --------------------------------------------------

            pnl_rows = con.execute("""
                SELECT engine, used
                FROM bankstate_engine_snapshot
                WHERE ts = (
                    SELECT MAX(ts)
                    FROM bankstate_engine_snapshot
                    WHERE date(ts)=date('now','utc')
                )
            """).fetchall()

            card = ttk.Frame(self.bank_cards, padding=12, relief="ridge")
            card.grid(row=1, column=0, padx=8, pady=8, sticky="nsew")

            ttk.Label(card, text="💰 P&L PER ENGINE",
                      font=("TkDefaultFont",10,"bold")).pack(anchor="w")

            for r in pnl_rows:
                ttk.Label(
                    card,
                    text=f"{r['engine']}   £{float(r['used'] or 0):.2f}"
                ).pack(anchor="w")


        if not rows:
            ttk.Label(
                self.bank_cards,
                text="No capital data available",
                font=("TkDefaultFont", 10, "italic")
            ).grid(row=0, column=0, columnspan=3, pady=8, sticky="nsew")
            return

        # Configure responsive 3x2 grid
        # --------------------------------------------------
        # STABLE BANKSTATE GRID
        # --------------------------------------------------
        # Columns stretch horizontally
        for c in range(3):
            self.bank_cards.columnconfigure(c, weight=1)

        # Rows do NOT stretch vertically (prevents gaps)
        for r in range(10):
            self.bank_cards.rowconfigure(r, weight=0)

        # === PATCH START ===
        # 📍 TARGET: gui/dashboard.py:_render_bankstate_report
        # 🛠 ACTION: add Engine Pot section header
        # PURPOSE:
        # visually separate Unified telemetry from engine capital cards
        # ===

        engine_header = ttk.Frame(self.bank_cards, padding=8, relief="ridge")
        engine_header.grid(row=3, column=0, columnspan=3, padx=8, pady=(4,6), sticky="ew")

        ttk.Label(
            engine_header,
            text="💰 ENGINE POT",
            font=("TkDefaultFont", 11, "bold")
        ).pack(anchor="w")

        # === PATCH END ===

        for i, r in enumerate(rows[:6]):

            engine = r["engine"]
            pot = float(r["pot"] or 0)
            floor = float(r["floor"] or 0)
            unmatched = float(r["unmatched"] or 0)

            headroom = pot - floor
            utilisation = (floor / pot) * 100 if pot > 0 else 0

            # Fire indicator based on utilisation
            if utilisation > 50:
                icon = "🔥"
            elif utilisation > 25:
                icon = "⚡"
            else:
                icon = ""

            card = ttk.Frame(self.bank_cards, padding=12, relief="ridge")
            card.grid(row=(i // 3) + 4, column=i % 3, padx=8, pady=8, sticky="nsew")

            ttk.Label(
                card,
                text=f"{icon} {engine}",
                font=("TkDefaultFont", 10, "bold")
            ).pack(anchor="w")

            ttk.Label(card, text=f"Pot        £{pot:,.2f}").pack(anchor="w")
            ttk.Label(card, text=f"Matched    £{floor:,.2f}").pack(anchor="w")
            ttk.Label(card, text=f"Reserved   £{unmatched:,.2f}").pack(anchor="w")
            ttk.Label(card, text=f"Headroom   £{headroom:,.2f}").pack(anchor="w")

            ttk.Label(
                card,
                text=f"Utilisation {utilisation:.1f}%",
                font=("TkDefaultFont", 9, "italic")
            ).pack(anchor="w", pady=(4, 0))

# === PATCH END ==============================================================

      
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

    # Let Tk handle geometry naturally
    root.after(50, root.update_idletasks)

    print("[dashboard] Tk mainloop starting …")
    root.mainloop()

