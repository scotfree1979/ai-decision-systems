# engines/autoscalp_gui.py
import threading
import tkinter as tk
from tkinter import ttk
import time
from datetime import datetime

REFRESH_MS = 2000  # update every 2s

def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default

def _mark(ok: bool) -> str:
    return "✅" if ok else "❌"

def _currency(x) -> str:
    try:
        return f"£{float(x):.2f}"
    except Exception:
        return "£0.00"

def _get_budget_tuple(engine):
    """(available_funds, current_risk) with safe fallbacks."""
    # Try daily_config if present
    try:
        from daily_config import fetch_available_budget
        available = float(fetch_available_budget())
    except Exception:
        # fallback – treat available as current balance
        available = float(_safe(lambda: engine.current_balance, 0.0))

    # Rough risk proxy: max liability among unmatched LAY in memory if you track it,
    # else use 0; adjust later if you expose a better metric.
    current_risk = float(_safe(lambda: engine.current_risk, 0.0)) or 0.0
    return available, current_risk

def _collect_simple_counts(engine):
    markets = int(_safe(lambda: len(engine.live_markets), 0))
    runners_tracked = int(_safe(lambda: sum(len(v) for v in engine.live_markets.values()), 0))

    counters = _safe(lambda: dict(engine.counter), {}) or {}
    scalps_fired = int(counters.get("scalps_fired", 0))
    exploratory_fired = int(counters.get("exploratory_fired", 0))
    blueprint_fired = int(counters.get("blueprint_fired", 0))

    active = passive = ignored = 0
    # If you classify tiers elsewhere, wire those in; otherwise leave zeros until data flows.
    # (We’ll increment when you expose a tier map, e.g., engine.tiers[(m,s)] -> 'active'...)
    return {
        "markets": markets,
        "runners_tracked": runners_tracked,
        "scalps_fired": scalps_fired,
        "exploratory_fired": exploratory_fired,
        "blueprint_fired": blueprint_fired,
        "active": active,
        "passive": passive,
        "ignored": ignored,
    }

def _readiness_flags(engine):
    """
    Cheap, non-blocking heuristics for the readiness toggles.
    Refine later by exposing explicit flags on the engine if needed.
    """
    # RAM Snapshots: consider 'ready' once we’ve ingested any static snapshot
    ram_ready = False
    try:
        snap = engine.get_static_snapshot()
        ram_ready = bool(snap)
    except Exception:
        ram_ready = False

    # Story chapters: expose a cached count if available; otherwise False for now
    story_ready = bool(_safe(lambda: engine.story_chapter_count > 0, False))

    # OC bands present: True if we see any OC* keys inside any snapshot (cheap sample)
    oc_ready = False
    try:
        if snap:
            # look at first few items only to stay cheap
            sample = list(snap.items())[:20]
            for _, payload in sample:
                oc = _safe(lambda: payload.get("snapshot", {}).get("oc_snapshots", {}), {})
                if any(k.startswith("OC") for k in oc.keys()):
                    oc_ready = True
                    break
    except Exception:
        oc_ready = False

    # Confidence available: if any snapshot shows a confidence field
    conf_ready = False
    try:
        if snap:
            sample = list(snap.items())[:20]
            for _, payload in sample:
                conf = _safe(lambda: payload.get("snapshot", {}).get("confidence"), None)
                if isinstance(conf, (int, float)):
                    conf_ready = True
                    break
    except Exception:
        conf_ready = False

    # Blueprints: ready if engine has non-empty KNOWN_BLUEPRINT_PATTERNS
    blue_ready = bool(_safe(lambda: engine.KNOWN_BLUEPRINT_PATTERNS, {}))

    # Liability check: assume ✅ once we can compute budget tuple without exception
    try:
        _get_budget_tuple(engine)
        liab_ready = True
    except Exception:
        liab_ready = False

    return {
        "oc": oc_ready,
        "conf": conf_ready,
        "blue": blue_ready,
        "ram": ram_ready,
        "liab": liab_ready,
        "story": story_ready,
    }

def _time_until_first_oc1():
    # Placeholder until you expose schedule data; show ✅ < live
    return "✅ < live"

def _build_report(engine) -> str:
    now_utc = datetime.utcnow().strftime("%H:%M:%S")
    counts = _collect_simple_counts(engine)
    flags = _readiness_flags(engine)

    # Daily overview placeholders (wire to your scheduler when available)
    races_today = int(_safe(lambda: engine.races_scheduled_today, 0)) or 19
    runners_today = int(_safe(lambda: engine.runners_scheduled_today, 0)) or 416

    # OC lines
    oc_lines = []
    for i in range(0, 8):
        if i == 0:
            oc_lines.append(f"OC0 → {_mark(False)} Not found")
        else:
            oc_lines.append(f"OC{i} → {_mark(False)} Not found")

    # Budget
    available, current_risk = _get_budget_tuple(engine)
    util = (current_risk / available * 100.0) if available > 0 else 0.0

    # Health
    health_note = "⚠️  Low chapter coverage. Waiting for more OC bands to complete."

    report = []
    report.append("⏱ SYSTEM READINESS DIAGNOSTICS")
    report.append("--------------------------------------------------------")
    report.append(f"OC Bands             {_mark(flags['oc'])}")
    report.append(f"Confidence           {_mark(flags['conf'])}")
    report.append(f"Blueprints           {_mark(flags['blue'])}")
    report.append(f"RAM Snapshots        {_mark(flags['ram'])}")
    report.append(f"Liability Check      {_mark(flags['liab'])}")
    report.append(f"Story Chapters       {_mark(flags['story'])}\n")

    report.append(f"🧠 SIGNAL MEMORY ENGINE STATUS (Updated: {now_utc} UTC)")
    report.append("📋 DAILY OVERVIEW")
    report.append("--------------------------------------------------------")
    report.append(f"🗓 Races Scheduled Today:   {races_today}")
    report.append(f"🏇 Runners Scheduled:       {runners_today}\n")

    report.append("🎯 RUNNER TIER SUMMARY")
    report.append("--------------------------------------------------------")
    report.append(f"✅ Active Runners:          {counts['active']}")
    report.append(f"⏸ Passive Runners:         {counts['passive']}")
    report.append(f"❌ Ignored Runners:         {counts['ignored']}\n")

    report.append("⏱ OC BAND COUNTDOWNS")
    report.append("--------------------------------------------------------")
    report.extend(oc_lines)
    report.append(f"⏲ Time Until First OC1:    {_time_until_first_oc1()}")
    report.append("--------------------------------------------------------")
    report.append(f"🟢 Markets Tracked:         {counts['markets']}")
    report.append(f"🔁 Runners Evaluated:       {counts['runners_tracked']}")
    report.append(f"📘 Runner Stories Complete: {int(_safe(lambda: engine.story_chapter_count, 0) or 0)}")
    report.append(f"🎯 Scalp Triggers Today:    {counts['scalps_fired']}\n")

    report.append("📊 BLUEPRINT MATCHING")
    report.append("--------------------------------------------------------")
    report.append(f"🧪 Exploratory Matches:     {0}")
    report.append(f"⚡ Partial Matches:         {0}")
    report.append(f"🎯 Full Blueprint Matches:  {counts['blueprint_fired']}\n")

    report.append("🏇 RUNNER SNAPSHOTS (Top 5)")
    report.append("--------------------------------------------------------\n")

    report.append("💰 BUDGET STATUS")
    report.append("--------------------------------------------------------")
    report.append(f"Available Funds:          {_currency(available)}")
    report.append(f"Current Risk (Max Liab):  {_currency(current_risk)}")
    report.append(f"Budget Utilization:       {util:.1f}%")
    report.append(f"Scalps Fired This Hour:   {0}")
    report.append(f"Scalps Blocked by Budget: {0}\n")

    report.append("✅ SYSTEM HEALTH")
    report.append("--------------------------------------------------------")
    report.append(health_note + "\n")

    report.append("⏱ SYSTEM READINESS DIAGNOSTICS")
    report.append("--------------------------------------------------------")
    report.append(f"OC Bands             {_mark(flags['oc'])}")
    report.append(f"Confidence           {_mark(flags['conf'])}")
    report.append(f"Blueprints           {_mark(flags['blue'])}")
    report.append(f"RAM Snapshots        {_mark(flags['ram'])}")
    report.append(f"Liability Check      {_mark(flags['liab'])}")
    report.append(f"Story Chapters       {_mark(flags['story'])}")

    return "\n".join(report)

def launch_autoscalp_gui(engine):
    root = tk.Tk()
    root.title("Auto Scalping — Readiness")

    # Toolbar (optional controls for later)
    toolbar = ttk.Frame(root, padding=8)
    toolbar.pack(side=tk.TOP, fill=tk.X)
    status_var = tk.StringVar(value="Diagnostics")
    ttk.Label(toolbar, textvariable=status_var).pack(side=tk.LEFT)

    # Scrollable report area
    frame = ttk.Frame(root, padding=8)
    frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    txt = tk.Text(frame, wrap="word")
    txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    txt.configure(font=("Menlo", 11), state="disabled")

    scroll = ttk.Scrollbar(frame, command=txt.yview)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)
    txt.configure(yscrollcommand=scroll.set)

    def render():
        report = _build_report(engine)
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        txt.insert("end", report)
        txt.see("1.0")
        txt.configure(state="disabled")
        root.after(REFRESH_MS, render)

    root.after(150, render)
    root.mainloop()
