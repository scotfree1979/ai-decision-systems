# === PATCH START ===
# 📍 TARGET: gui/mastery_dashboard.py
# 📆 PATCHED: 2025-10-29Z — Dummy-data Mastery Dashboard (Phase 1)

import tkinter as tk
from tkinter import ttk
import random, datetime

class MasteryDashboard(tk.Toplevel):
    """Standalone Mastery Dashboard window (dummy data)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.title("🧠 Mastery Dashboard")
        self.geometry("1400x800")
        self.configure(bg="#1e1e1e")
        self.protocol("WM_DELETE_WINDOW", self.close)

        # Header Bar -----------------------------------------------------------
        header = tk.Frame(self, bg="#262626", height=40)
        header.pack(fill="x")
        tk.Label(header, text="Mastery Intelligence Suite", fg="white",
                 bg="#262626", font=("Segoe UI", 14, "bold")).pack(side="left", padx=10)
        tk.Button(header, text="Refresh", command=self.refresh).pack(side="right", padx=5)
        tk.Button(header, text="Export CSV", command=self.export_csv).pack(side="right", padx=5)
# === PATCH START ===
# 📍 TARGET: gui/mastery_dashboard.py:__init__
# 📆 PATCHED: 2025-10-29Z — visual: black text for Close button

        tk.Button(header, text="Close", command=self.close,
                  bg="#ff6666", fg="black").pack(side="right", padx=5)
# === PATCH END ===


        # KPI Row --------------------------------------------------------------
        self.kpi_frame = tk.Frame(self, bg="#202020")
        self.kpi_frame.pack(fill="x", pady=5)
        self._populate_kpis()

        # Notebook (Tabs) ------------------------------------------------------
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=10)

        self._tab_letters(nb)
        self._tab_posteriors(nb)
        self._tab_events(nb)
        self._tab_plan(nb)
        self._tab_progress(nb)

    # -------------------------------------------------------------------------
    def _populate_kpis(self):
        metrics = {
            "Accuracy": f"{random.uniform(70, 99):.2f}%",
            "Confidence": f"{random.uniform(60, 95):.2f}%",
            "Trades": f"{random.randint(5000,15000)}",
            "Maturity": f"{random.randint(40,95)}%"
        }
        for i,(k,v) in enumerate(metrics.items()):
            lbl = tk.Label(self.kpi_frame, text=f"{k}: {v}", fg="white",
                           bg="#202020", font=("Segoe UI",12,"bold"))
            lbl.grid(row=0, column=i, padx=20, pady=5)

    # -------------------------------------------------------------------------
    def _tab_letters(self, nb):
        f = tk.Frame(nb, bg="#1e1e1e")
        nb.add(f, text="Strategy Letters")
        letters = [chr(i) for i in range(65, 91)]
        grid = tk.Frame(f, bg="#1e1e1e")
        grid.pack(pady=20)
        for i,l in enumerate(letters):
            color = f"#{random.randint(50,255):02x}{random.randint(100,255):02x}{random.randint(50,255):02x}"
            btn = tk.Label(grid, text=l, bg=color, width=4, height=2,
                           font=("Segoe UI",14,"bold"), relief="ridge")
            btn.grid(row=i//10, column=i%10, padx=5, pady=5)

    # -------------------------------------------------------------------------
    def _tab_posteriors(self, nb):
        f = tk.Frame(nb, bg="#1e1e1e")
        nb.add(f, text="Posterior Evolution")
        canvas = tk.Canvas(f, bg="#202020", height=300)
        canvas.pack(fill="both", expand=True, padx=10, pady=10)
        # Dummy line chart
        w, h = 1200, 300
        last_y = h/2
        for x in range(0, w, 20):
            y = h/2 + random.randint(-80, 80)
            canvas.create_line(x, last_y, x+20, y, fill="#00ff99", width=2)
            last_y = y

    # -------------------------------------------------------------------------
    def _tab_events(self, nb):
        f = tk.Frame(nb, bg="#1e1e1e")
        nb.add(f, text="Learning Stream")
        txt = tk.Text(f, bg="#111", fg="#ccc", font=("Consolas",10))
        txt.pack(fill="both", expand=True, padx=10, pady=10)
        for i in range(20):
            t = datetime.datetime.utcnow().strftime("%H:%M:%S")
            txt.insert("end", f"[{t}]  Letter {random.choice('ABCDEFGXYZ')} learned new pattern Δconf={random.uniform(0.01,0.15):.2f}\n")
        txt.config(state="disabled")

    # -------------------------------------------------------------------------
    def _tab_plan(self, nb):
        f = tk.Frame(nb, bg="#1e1e1e")
        nb.add(f, text="Plan Ledger Snapshot")
        cols = ("Letter","Conf","Dir","Target","Status","Why","Updated")
        tree = ttk.Treeview(f, columns=cols, show="headings", height=15)
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=110)
        tree.pack(fill="both", expand=True, padx=10, pady=10)
        for _ in range(15):
            tree.insert("", "end", values=[
                random.choice("ABCFGKMRX"), f"{random.uniform(60,95):.1f}%",
                random.choice(["LAY","BACK"]),
                f"{random.randint(1,5)} ticks",
                random.choice(["active","done","fail"]),
                random.choice(["bias","vol","liq"]),
                datetime.datetime.utcnow().strftime("%H:%M:%S")
            ])

    # -------------------------------------------------------------------------
    def _tab_progress(self, nb):
        f = tk.Frame(nb, bg="#1e1e1e")
        nb.add(f, text="Progress / Learning Status")
        bar = ttk.Progressbar(f, length=400, mode="determinate")
        bar.pack(pady=40)
        bar["value"] = random.randint(10,90)
        tk.Label(f, text="Learning Completion", bg="#1e1e1e", fg="white").pack()

    # -------------------------------------------------------------------------
    def refresh(self):
        for w in self.kpi_frame.winfo_children():
            w.destroy()
        self._populate_kpis()

    def export_csv(self):
        print("[EXPORT] Pretend to export CSV (stub)")

    def close(self):
        self.destroy()


def open_mastery_dashboard(parent=None):
    """Launch MasteryDashboard window from main dashboard."""
    dash = MasteryDashboard(parent)
    dash.focus_set()
    return dash
# === PATCH END ===
