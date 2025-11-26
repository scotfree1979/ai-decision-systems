#!/usr/bin/env python3
# --- Bootstrap so this file runs from anywhere ---
import os, sys
_here = os.path.dirname(os.path.abspath(__file__))            # .../analytics_beta/engines
_root = os.path.dirname(_here)                                # .../analytics_beta
if _root not in sys.path:
    sys.path.insert(0, _root)
# Make sure 'engines' is a package
eng_init = os.path.join(_here, "__init__.py")
if not os.path.exists(eng_init):
    open(eng_init, "a").close()

from typing import Optional


from datetime import datetime, UTC





# at top of file
# AUTOSCALP_GUI IMPORTS — add these, remove legacy run_coop_session imports

# Add:
# (lazy import in handlers) — do not import engines.gui_hooks here

# Remove/avoid:
# from scalper_coop import run_coop_session
# from scalper_module import keep_alive_loop  # keep if you still want GUI toggle to spawn it

from tkinter import ttk, messagebox
import tkinter as tk

# Ensure GUI DB exists (safe import)
try:
    from engines.gui_db import ensure_gui_db  # make sure GUI DB exists before any logging
except Exception:
    def ensure_gui_db():
        pass



REFRESH_MS = 1500  # UI refresh cadence (static placeholders for now)




# -------------------------
# App Shell
# -------------------------
class AutoScalpApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Auto Scalping")
        self.geometry("1100x730")
        self.minsize(1000, 680)

        container = ttk.Frame(self)
        container.pack(fill="both", expand=True)

        self.views = {}
        for V in (StartupView, DashboardView, ScalperView):
            frame = V(parent=container, app=self)
            self.views[V.__name__] = frame
            frame.grid(row=0, column=0, sticky="nsew")

        self.show("StartupView")
        ensure_gui_db()

    def show(self, name: str):
        self.views[name].tkraise()
        if hasattr(self.views[name], "on_show"):
            self.views[name].on_show()

# -------------------------
# Startup View
# -------------------------


class StartupView(ttk.Frame):
    def __init__(self, parent, app: "AutoScalpApp"):
        super().__init__(parent)
        self.app = app

        pad = {"padx": 12, "pady": 8}
        title = ttk.Label(self, text="AUTO SCALPING — STARTUP", font=("SF Pro Text", 16, "bold"))
        title.pack(anchor="w", **pad)

        body = ttk.Frame(self); body.pack(fill="both", expand=True, **pad)

        # Session
        session = LabeledFrame(body, "Session"); session.grid(row=0, column=0, sticky="nsew", **pad)
        ttk.Label(session.body, text="Betfair Session Token:").grid(row=0, column=0, sticky="w", pady=(0,4))
        self.token = ttk.Entry(session.body, width=48); self.token.grid(row=0, column=1, sticky="we", pady=(0,4))

        ttk.Label(session.body, text="App Key (optional):").grid(row=1, column=0, sticky="w", pady=(0,4))
        self.appkey = ttk.Entry(session.body, width=48); self.appkey.grid(row=1, column=1, sticky="we", pady=(0,4))

        # AFTER (rename + same handler)
        # replace: ttk.Button(session.body, text="Test", command=self._on_test) ...
        ttk.Button(session.body, text="Launch", command=self._on_launch).grid(row=0, column=2, padx=(8,0))



        self.keepalive = tk.BooleanVar(value=True)
        ttk.Checkbutton(session.body, text="Keep-Alive (60s)", variable=self.keepalive).grid(row=2, column=1, sticky="w", pady=(4,0))

        # Mode
        mode = LabeledFrame(body, "Mode"); mode.grid(row=0, column=1, sticky="nsew", **pad)
        self.mode = tk.StringVar(value="SIM")
        ttk.Radiobutton(mode.body, text="Simulation", value="SIM", variable=self.mode).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(mode.body, text="Live",        value="LIVE", variable=self.mode).grid(row=0, column=1, sticky="w")


        # Engine Init
        init = LabeledFrame(body, "Engine Init"); init.grid(row=1, column=0, sticky="nsew", **pad)
        self.daily_reset = tk.BooleanVar(value=True)
        self.start_threads = tk.BooleanVar(value=True)
        ttk.Checkbutton(init.body, text="Daily reset on start", variable=self.daily_reset).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(init.body, text="Start monitoring threads", variable=self.start_threads).grid(row=0, column=1, sticky="w", padx=(14,0))

        ttk.Label(init.body, text="Blueprints:").grid(row=1, column=0, sticky="w", pady=(6,0))
        ttk.Button(init.body, text="Run", command=self._on_run_blueprints).grid(row=1, column=1, sticky="w", pady=(6,0))
        self.bp_status = ttk.Label(init.body, text="Status: ✖ not run"); self.bp_status.grid(row=1, column=2, sticky="w", padx=(8,0), pady=(6,0))

        # Readiness (unchanged UI)
        ready = LabeledFrame(body, "Readiness Check"); ready.grid(row=1, column=1, sticky="nsew", **pad)
        cols = (("OC Bands","❌"),("Confidence","❌"),("Blueprints","❌"),("RAM Snapshots","❌"),("Liability","✅"),("Story Chapters","❌"))
        self.ready_labels = []
        for i,(k,v) in enumerate(cols):
            lbl = ttk.Label(ready.body, text=f"{k:15} {v}"); lbl.grid(row=i//2, column=i%2, sticky="w", padx=(0,22), pady=(2,2))
            self.ready_labels.append(lbl)

        # Footer
        footer = ttk.Frame(self); footer.pack(fill="x", **pad)
        ttk.Button(footer, text="SETTINGS", command=lambda: None).pack(side="left")
        ttk.Button(footer, text="EXIT", command=self.app.destroy).pack(side="right")
        ttk.Button(footer, text="START ENGINE", command=self._on_start_engine).pack(side="right", padx=(0,8))

        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

    # ---- handlers (inside the class) ----



    def _on_run_blueprints(self):
        from engines.gui_hooks import gui_run_blueprints
        try:
            gui_run_blueprints()

            self.bp_status.configure(text="Status: ✔ current")
            messagebox.showinfo("Blueprints", "Blueprints built for today.")
        except Exception as e:
            self.bp_status.configure(text="Status: ✖ failed")
            messagebox.showerror("Blueprints", f"Failed: {e}")

    # AUTOSCALP_GUI — replace your StartupView._on_launch with this

    def _on_launch(self):
        from engines.gui_hooks import gui_set_credentials, gui_start_engine
        token = self.token.get().strip()
        appkey = self.appkey.get().strip() or None
        if not token:
            messagebox.showwarning("Session", "Please enter your Betfair session token.")
            return
        try:
            # 1) set creds (and optionally keepalive)
            gui_set_credentials(token, app_key=appkey, keep_alive=self.keepalive.get())

            # 2) start the data-only engine (2-day seed + OC bands)
            gui_start_engine(self.mode.get().upper())

            # 3) switch view
            messagebox.showinfo("Engine", "Engine started. Collecting data…")
            self.app.show("DashboardView")

        except Exception as e:
            messagebox.showerror("Engine", f"Start failed: {e}")





    def _on_start_engine(self):
        # mirror Launch
        self._on_launch()


    def on_show(self):  # optional
        pass

# -------------------------
# Dashboard View
# -------------------------
class DashboardView(ttk.Frame):
    def __init__(self, parent, app: AutoScalpApp):
        super().__init__(parent)
        self.app = app
        pad = {"padx": 12, "pady": 8}

        title = ttk.Label(self, text="AUTO SCALPING — LIVE DASHBOARD", font=("SF Pro Text", 16, "bold"))
        title.pack(anchor="w", **pad)

        # Top grid (System Readiness | Daily Overview | Budget)
        top = ttk.Frame(self)
        top.pack(fill="x", **pad)

        self.card_readiness = InfoCard(top, "System Readiness",
            rows=[
                ("OC Bands", "❌"),
                ("Confidence", "❌"),
                ("Blueprints", "❌"),
                ("RAM Snapshots", "❌"),
                ("Liability Check", "✅"),
                ("Story Chapters", "❌"),
            ])
        self.card_readiness.grid(row=0, column=0, sticky="nsew", padx=(0,8))

        self.card_daily = InfoCard(top, "Daily Overview",
            rows=[
                ("Races Today", "19"),
                ("Runners Today", "416"),
                ("First OC1 in", "<live>")
            ])
        self.card_daily.grid(row=0, column=1, sticky="nsew", padx=8)

        self.card_budget = InfoCard(top, "Budget / Risk",
            rows=[
                ("Available", "£622.78"),
                ("Current Risk", "£0.00"),
                ("Utilization", "0.0%"),
                ("Scalps/hr", "0"),
                ("Blocked", "0"),
            ])
        self.card_budget.grid(row=0, column=2, sticky="nsew", padx=(8,0))

        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=1)
        top.columnconfigure(2, weight=1)

        # Mid grid (Tier Summary | Blueprint Matching)
        mid = ttk.Frame(self)
        mid.pack(fill="x", **pad)

        self.card_tiers = InfoCard(mid, "Runner Tier Summary",
            rows=[("Active","0"),("Passive","0"),("Ignored","0")], three_across=True)
        self.card_tiers.grid(row=0, column=0, sticky="nsew", padx=(0,8))

        self.card_bp = InfoCard(mid, "Blueprint Matching",
            rows=[("Exploratory","0"),("Partial","0"),("Full","0")], three_across=True)
        self.card_bp.grid(row=0, column=1, sticky="nsew", padx=(8,0))

        mid.columnconfigure(0, weight=1)
        mid.columnconfigure(1, weight=1)

        # Alerts/Events
        alerts = LabeledFrame(self, "Recent Events / Alerts")
        alerts.pack(fill="x", **pad)
        self.txt_alerts = tk.Text(alerts.body, height=5)
        self.txt_alerts.pack(fill="x")
        self._append_alert("[{}] Engine started; threads up; awaiting OC bands..."
                           .format(datetime.now(UTC).strftime("%H:%M:%S")))

        # Top Runners Snapshot table
        runners = LabeledFrame(self, "Top Runners Snapshot (live, top 5)")
        runners.pack(fill="both", expand=True, **pad)
        self.tbl = ttk.Treeview(runners.body, columns=("market","runner","odds","trend","oc","conf","dir","range"), show="headings", height=7)
        for c, w in (("market",200),("runner",160),("odds",60),("trend",80),("oc",60),("conf",60),("dir",80),("range",140)):
            self.tbl.heading(c, text=c.title())
            self.tbl.column(c, width=w, anchor="center")
        self.tbl.pack(fill="both", expand=True)
        self._seed_table()

        # Footer buttons
        footer = ttk.Frame(self)
        footer.pack(fill="x", **pad)
        ttk.Button(footer, text="Settings", command=self._noop).pack(side="left")
        ttk.Button(footer, text="Pause Engine", command=self._noop).pack(side="left", padx=(8,0))
        ttk.Button(footer, text="Open Scalping Window", command=lambda: self.app.show("ScalperView")).pack(side="right")
        ttk.Button(footer, text="Export Report", command=self._noop).pack(side="right", padx=(0,8))
        ttk.Button(footer, text="Back to Startup", command=lambda: self.app.show("StartupView")).pack(side="right", padx=(0,8))

        self.after(REFRESH_MS, self._tick)

    def _seed_table(self):
        data = [
            ("Catterick 14:05","Midnight Sun","6.4","steam","OC3","0.72","B→L","5.8–6.9"),
            ("Bath 14:20","Aurora Sky","8.2","drift","OC2","0.66","L→B","7.5–8.8"),
            ("Windsor 14:30","Copper Leaf","5.6","steam","OC4","0.70","B→L","5.2–6.1"),
            ("Lingfield 14:40","River Stone","9.8","drift","OC3","0.61","L→B","9.2–10.4"),
            ("Thirsk 14:50","Paper Kite","7.2","steam","OC2","0.68","B→L","6.7–7.9"),
        ]
        for row in data:
            self.tbl.insert("", "end", values=row)

    def _append_alert(self, line: str):
        self.txt_alerts.insert("end", line + "\n")
        self.txt_alerts.see("end")

    def _noop(self):
        pass

    def _tick(self):
        # placeholders only (no data changes yet)
        self.after(REFRESH_MS, self._tick)

    def on_show(self):
        self._append_alert("[{}] Dashboard ready."
                           .format(datetime.now(UTC).strftime("%H:%M:%S")))

# -------------------------
# Scalper View
# -------------------------
class ScalperView(ttk.Frame):
    def __init__(self, parent, app: AutoScalpApp):
        super().__init__(parent)
        self.app = app
        pad = {"padx": 12, "pady": 8}

        title = ttk.Label(self, text="SCALPING — ACTIVE TRADES", font=("SF Pro Text", 16, "bold"))
        title.pack(anchor="w", **pad)

        # Active Position Card
        active = LabeledFrame(self, "Active Position")
        active.pack(fill="x", **pad)

        grid = active.body
        # Example row
        ttk.Label(grid, text="Market / Runner:").grid(row=0, column=0, sticky="w")
        ttk.Label(grid, text="Catterick 14:05 — Midnight Sun").grid(row=0, column=1, sticky="w", padx=(8,0))
        ttk.Label(grid, text="Entry:").grid(row=1, column=0, sticky="w")
        ttk.Label(grid, text="LAY 6.4 £10").grid(row=1, column=1, sticky="w", padx=(8,0))
        ttk.Label(grid, text="Direction:").grid(row=2, column=0, sticky="w")
        ttk.Label(grid, text="lay→back   Hedge @ 6.0").grid(row=2, column=1, sticky="w", padx=(8,0))
        ttk.Label(grid, text="Confidence:").grid(row=3, column=0, sticky="w")
        ttk.Label(grid, text="0.72 (Partial Match)").grid(row=3, column=1, sticky="w", padx=(8,0))
        ttk.Label(grid, text="Unrealized:").grid(row=4, column=0, sticky="w")
        ttk.Label(grid, text="£+0.80").grid(row=4, column=1, sticky="w", padx=(8,0))
        ttk.Label(grid, text="Liability:").grid(row=5, column=0, sticky="w")
        ttk.Label(grid, text="£4.40").grid(row=5, column=1, sticky="w", padx=(8,0))

        btns = ttk.Frame(active.body)
        btns.grid(row=0, column=2, rowspan=6, padx=(24,0))
        ttk.Button(btns, text="Green Up", command=self._noop).grid(row=0, column=0, sticky="ew", pady=(0,6))
        ttk.Button(btns, text="Close", command=self._noop).grid(row=1, column=0, sticky="ew")
        ttk.Button(btns, text="Cancel Hedge", command=self._noop).grid(row=2, column=0, sticky="ew", pady=(6,0))

        # Open Orders
        orders = LabeledFrame(self, "Open Orders")
        orders.pack(fill="both", expand=True, **pad)
        self.tbl = ttk.Treeview(orders.body, columns=("betid","side","odds","stake","status","mr"),
                                show="headings", height=6)
        for c, w in (("betid",160),("side",80),("odds",70),("stake",80),("status",120),("mr",380)):
            self.tbl.heading(c, text=c.upper())
            self.tbl.column(c, width=w, anchor="center")
        self.tbl.pack(fill="both", expand=True)
        self._seed_orders()

        # P&L Panels
        pnl = ttk.Frame(self)
        pnl.pack(fill="x", **pad)

        self.pnl_pattern = InfoCard(pnl, "Pattern P&L (today)", rows=[("Total", "£+3.40")])
        self.pnl_pattern.grid(row=0, column=0, sticky="nsew", padx=(0,8))
        self.pnl_runner = InfoCard(pnl, "Runner P&L (today)", rows=[("Midnight Sun","£+0.80")])
        self.pnl_runner.grid(row=0, column=1, sticky="nsew", padx=8)
        self.pnl_market = InfoCard(pnl, "Market P&L (today)", rows=[("Catterick 14:05","£+0.80")])
        self.pnl_market.grid(row=0, column=2, sticky="nsew", padx=8)
        self.pnl_total = InfoCard(pnl, "Total P&L (today)", rows=[("Total","£+3.40")])
        self.pnl_total.grid(row=0, column=3, sticky="nsew", padx=(8,0))

        pnl.columnconfigure(0, weight=1)
        pnl.columnconfigure(1, weight=1)
        pnl.columnconfigure(2, weight=1)
        pnl.columnconfigure(3, weight=1)

        # Bottom bar
        bottom = ttk.Frame(self)
        bottom.pack(fill="x", **pad)
        ttk.Button(bottom, text="Back to Dashboard", command=lambda: self.app.show("DashboardView")).pack(side="right")

    def _seed_orders(self):
        data = [
            ("BET-1001","LAY","6.4","£10.00","matched","Catterick 14:05 / Midnight Sun"),
            ("BET-1002","BACK","6.0","£10.00","unmatched","Catterick 14:05 / Midnight Sun"),
        ]
        for row in data:
            self.tbl.insert("", "end", values=row)

    def _noop(self):
        pass

    def on_show(self):
        pass

# -------------------------
# Small UI helpers
# -------------------------
class LabeledFrame(ttk.Frame):
    def __init__(self, parent, title: str):
        super().__init__(parent)
        outer = ttk.Frame(self, padding=(8,8,8,6))
        outer.pack(fill="both", expand=True)
        lbl = ttk.Label(outer, text=title, font=("SF Pro Text", 12, "bold"))
        lbl.pack(anchor="w")
        self.body = ttk.Frame(outer, padding=(6,4,6,2))
        self.body.pack(fill="both", expand=True)

class InfoCard(ttk.Frame):
    def __init__(self, parent, title: str, rows: list[tuple[str,str]], three_across=False):
        super().__init__(parent, padding=(8,8,8,8))
        self["borderwidth"] = 1
        self["relief"] = "solid"

        ttk.Label(self, text=title, font=("SF Pro Text", 12, "bold")).pack(anchor="w")
        grid = ttk.Frame(self)
        grid.pack(fill="x", pady=(6,0))

        if three_across:
            for i,(k,v) in enumerate(rows):
                card = ttk.Frame(grid, padding=6)
                card.grid(row=0, column=i, sticky="nsew", padx=(0,8))
                ttk.Label(card, text=k).pack(anchor="center")
                ttk.Label(card, text=v, font=("SF Pro Text", 12, "bold")).pack(anchor="center")
            for i in range(3):
                grid.columnconfigure(i, weight=1)
        else:
            for r,(k,v) in enumerate(rows):
                ttk.Label(grid, text=f"{k}:").grid(row=r, column=0, sticky="w", padx=(0,6), pady=2)
                ttk.Label(grid, text=v, font=("SF Pro Text", 11, "bold")).grid(row=r, column=1, sticky="w", pady=2)



# -------------------------
# Main
# -------------------------

def main():
    if "--dry-run" in sys.argv:
        print("autoscalp_gui dry-run ok")
        return
    app = AutoScalpApp()
    app.mainloop()

if __name__ == "__main__":
    main()


