#!/usr/bin/env python3
"""
GUI phase-runner: build in micro-steps with hard gates.
Steps implemented:
  1) Set credentials (token via UI; app key from daily_config.json or env)
  2) Ensure DB + start writer + seed bets (runners only) for today/tomorrow
  3) Seed anchor odds (OC0) into bets and oc_series
  4) Start OC1 polling loop (records into oc_series)

After each step: prints "READY: next step" to terminal.
"""

import os, sys, json, threading, time, logging
from datetime import datetime, timedelta
from typing import Optional, Iterable, Set, Tuple

import tkinter as tk
from tkinter import ttk, messagebox

# --- make repo root importable ------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))            # .../analytics_beta/gui
_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir))       # .../analytics_beta
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# --- imports from repo --------------------------------------------------------
from config_paths import DB_PATH
from engines.path_guard import ensure_parent
from engines.db_migrations import ensure_tables
from engines.database_hijack_monitor import (
    launch_db_writer, enqueue_write, enqueue_read, priority_queue as DBQ
)
from engines.utils.api_tools import fetch_live_odds

# get_markets function we’ll use to seed runners
from engines.get_markets import get_markets_and_insert

# ------------------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------------------

def _load_app_key_from_daily_config() -> Optional[str]:
    """
    Order:
      1) BETFAIR_APP_KEY env
      2) analytics_beta/daily_config.json  -> {"app_key": "..."}
      3) engines/daily_config.json         -> {"app_key": "..."}
      4) engines/upgrade_import_patch.get_app_key() if available
    """
    k = os.environ.get("BETFAIR_APP_KEY") or os.environ.get("APP_KEY")
    if k:
        return k.strip()

    candidates = [
        os.path.join(_ROOT, "daily_config.json"),
        os.path.join(_ROOT, "engines", "daily_config.json"),
    ]
    for path in candidates:
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    data = json.load(f)
                v = (data.get("app_key") or "").strip()
                if v:
                    return v
        except Exception:
            pass

    try:
        from engines.upgrade_import_patch import get_app_key  # type: ignore
        v = (get_app_key() or "").strip()
        return v or None
    except Exception:
        return None


def _install_upgrade_patch_shim(token: str, app_key: Optional[str]):
    """
    Some legacy helpers expect engines.upgrade_import_patch.{get,set}_session_token/app_key.
    We install/refresh those here so older code (e.g., market fetchers) can work.
    """
    try:
        import engines.upgrade_import_patch as uip  # type: ignore
        if hasattr(uip, "set_session_token"):
            uip.set_session_token(token)
        if app_key and hasattr(uip, "set_app_key"):
            uip.set_app_key(app_key)
        return
    except Exception:
        pass

    import types
    m = types.ModuleType("engines.upgrade_import_patch")
    m._tok = token
    m._app = app_key or ""

    def set_session_token(t: str): m._tok = t or ""
    def get_session_token() -> str: return m._tok
    def set_app_key(k: str): m._app = k or ""
    def get_app_key() -> str: return m._app

    m.set_session_token = set_session_token
    m.get_session_token = get_session_token
    m.set_app_key = set_app_key
    m.get_app_key = get_app_key

    sys.modules["engines.upgrade_import_patch"] = m


def _iso_date_from_start(start: Optional[str]) -> Optional[str]:
    if not start:
        return None
    try:
        s = start.replace("Z", "+00:00") if "Z" in start else start
        return datetime.fromisoformat(s).date().isoformat()
    except Exception:
        return None


def _filter_markets_by_dates(markets: Iterable[dict], target_dates: Set[str]) -> list:
    out = []
    for m in markets or []:
        d = _iso_date_from_start(m.get("marketStartTime"))
        if d and d in target_dates:
            out.append(m)
    return out


def _band_from_value(v: Optional[float], pct: float = 0.02):
    if v is None:
        return None, None, None
    low, high = v * (1 - pct), v * (1 + pct)
    return low, high, json.dumps([low, v, high])


def _record_oc_series(market_id: str, selection_id: int, stage: str, odd: Optional[float], source: str = "SIM", meta: Optional[dict] = None):
    low, high, band_json = _band_from_value(odd)
    sql = ("INSERT INTO oc_series (marketId, selectionId, stage, snapshot_ts, odd, "
           "band_low, band_high, band_json, meta_json, source) "
           "VALUES (?,?,?,?,?,?,?,?,?,?)")
    params = [
        market_id, selection_id, stage, datetime.utcnow().isoformat(), odd,
        low, high, band_json, json.dumps(meta or {}), source
    ]
    enqueue_write(sql, params, priority=3)


def _update_bets_anchor(market_id: str, selection_id: int, lay: Optional[float]):
    low, high, band_json = _band_from_value(lay)
    sql = (
        "UPDATE bets SET "
        "anchor_odd = COALESCE(anchor_odd, ?), "
        "OC0 = COALESCE(OC0, ?), "
        "OC0_band = COALESCE(OC0_band, ?), "
        "placed_at = COALESCE(placed_at, ?), "
        "timestamp = COALESCE(timestamp, ?) "
        "WHERE marketId = ? AND selectionId = ?"
    )
    now_iso = datetime.utcnow().isoformat()
    params = [lay, lay, band_json, now_iso, now_iso, market_id, selection_id]
    enqueue_write(sql, params, priority=3)


def _start_keepalive_thread(get_token, get_app_key, interval=60):
    try:
        import requests
    except Exception:
        logging.warning("[keepalive] 'requests' module missing; keep-alive disabled")
        return None

    def tick():
        tok = (get_token() or "").strip()
        if not tok:
            logging.warning("[session] no token yet; skipping keep-alive tick")
            return
        try:
            r = requests.post(
                "https://identitysso.betfair.com/api/keepAlive",
                headers={"X-Authentication": tok, "X-Application": (get_app_key() or "")},
                timeout=8,
            )
            if r.status_code == 200:
                logging.info("✅ [session] keep-alive OK")
            else:
                logging.warning(f"⚠️ [session] keep-alive HTTP {r.status_code}: {r.text[:120]}")
        except Exception as e:
            logging.error(f"❌ [session] keep-alive error: {e}")

    def loop():
        while True:
            tick(); time.sleep(interval)

    t = threading.Thread(target=loop, name="KeepAliveThread", daemon=True)
    t.start()
    return t

# ------------------------------------------------------------------------------
# GUI
# ------------------------------------------------------------------------------

class PhaseGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AutoScalp – Step Runner")
        self.geometry("520x270")
        self.resizable(False, False)

        # state
        self._token = tk.StringVar()
        self._app_key = _load_app_key_from_daily_config() or ""
        self._markets: list[dict] = []
        self._oc1_thread = None

        # layout
        frm = ttk.Frame(self, padding=14)
        frm.pack(fill="both", expand=True)

        # Token
        ttk.Label(frm, text="Session Token:").grid(row=0, column=0, sticky="w")
        ent = ttk.Entry(frm, textvariable=self._token, width=58, show="•")
        ent.grid(row=0, column=1, columnspan=2, sticky="we", padx=(8,0))

        # App key (readonly)
        ttk.Label(frm, text="App Key (daily_config):").grid(row=1, column=0, sticky="w", pady=(8,0))
        self.appkey_lbl = ttk.Label(frm, text=self._mask(self._app_key))
        self.appkey_lbl.grid(row=1, column=1, columnspan=2, sticky="w", padx=(8,0), pady=(8,0))

        # Buttons
        self.btn1 = ttk.Button(frm, text="Step 1: Set Credentials", command=self._step1)
        self.btn2 = ttk.Button(frm, text="Step 2: Seed Bets (today+tomorrow)", command=self._step2, state="disabled")
        self.btn3 = ttk.Button(frm, text="Step 3: Seed Anchor Odds (OC0)", command=self._step3, state="disabled")
        self.btn4 = ttk.Button(frm, text="Step 4: Start OC1 Loop", command=self._step4, state="disabled")

        self.btn1.grid(row=3, column=0, sticky="we", pady=(16,6))
        self.btn2.grid(row=3, column=1, sticky="we", padx=8, pady=(16,6))
        self.btn3.grid(row=3, column=2, sticky="we", pady=(16,6))
        self.btn4.grid(row=4, column=0, columnspan=3, sticky="we", pady=(6,0))

        # Status
        self.status = ttk.Label(frm, text="• Ready to run Step 1", foreground="#2c7")
        self.status.grid(row=5, column=0, columnspan=3, sticky="w", pady=(16,0))

        # logging to console
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    @staticmethod
    def _mask(s: str) -> str:
        if not s: return "(not found)"
        if len(s) <= 6: return "*" * len(s)
        return s[:3] + "…" + s[-3:]

    # ---------- Steps ----------
    def _step1(self):
        token = (self._token.get() or "").strip()
        if not token:
            messagebox.showwarning("Credentials", "Please paste your Betfair session token.")
            return

        # Install/refresh the legacy token/app-key shim for older modules
        _install_upgrade_patch_shim(token, self._app_key)

        # Start a keep-alive pinger so we immediately see if the token is valid (in terminal)
        try:
            from engines.upgrade_import_patch import get_session_token, get_app_key  # type: ignore
        except Exception:
            # fall back to lambdas from our shim
            from types import SimpleNamespace
            module = sys.modules.get("engines.upgrade_import_patch")
            get_session_token = (lambda: getattr(module, "_tok", token))
            get_app_key = (lambda: getattr(module, "_app", self._app_key))

        _start_keepalive_thread(get_session_token, get_app_key, interval=60)

        print("✅ Step 1 OK — token/app key set.")
        print("READY: next step")
        self.status.config(text="• Step 1 complete → Run Step 2", foreground="#2c7")
        self.btn2.config(state="normal")

    def _step2(self):
        """Ensure DB + launch writer + seed runners (no odds) for today/tomorrow."""
        self.btn2.config(state="disabled")
        def run():
            try:
                ensure_parent(DB_PATH)
                ensure_tables()                 # oc_series + helpful columns on bets
                launch_db_writer()              # single writer thread

                today = datetime.utcnow().date()
                tgt = {(today + timedelta(days=d)).isoformat() for d in (0,1)}

                # get_markets_and_insert is assumed to: (a) fetch markets, (b) insert minimal rows
                markets = get_markets_and_insert() or []
                markets = _filter_markets_by_dates(markets, tgt)
                self._markets = markets

                print(f"✅ Step 2 OK — seeded {sum(len(m.get('runners') or []) for m in markets)} runners "
                      f"across {len(markets)} markets for {sorted(tgt)}.")
                print("READY: next step")
                self.status.config(text="• Step 2 complete → Run Step 3", foreground="#2c7")
                self.btn3.config(state="normal")
            except Exception as e:
                print(f"❌ Step 2 failed: {e}")
                messagebox.showerror("Seed Bets", str(e))
                self.btn2.config(state="normal")

        threading.Thread(target=run, daemon=True).start()

    def _step3(self):
        """Fetch anchor (lay) odds for each runner → update bets + record OC0 in oc_series."""
        if not self._markets:
            messagebox.showwarning("Anchors", "No markets in memory. Run Step 2 first.")
            return
        self.btn3.config(state="disabled")

        def run():
            try:
                from engines.upgrade_import_patch import get_session_token  # type: ignore
                token = (get_session_token() or "").strip()
            except Exception:
                token = (self._token.get() or "").strip()

            total = 0
            for m in self._markets:
                mid = m.get("marketId")
                for r in (m.get("runners") or []):
                    sid = r.get("selectionId")
                    if not mid or sid is None:
                        continue
                    try:
                        odds = fetch_live_odds(token, mid, sid) or {}
                        lay = odds.get("lay")
                        _update_bets_anchor(mid, sid, lay)
                        _record_oc_series(mid, sid, "OC0", lay, source="SIM", meta={"odds": odds})
                        total += 1
                    except Exception as e:
                        logging.warning(f"[anchors] {mid}/{sid} fetch error: {e}")

            # wait for queue to flush so the DB is consistent before proceeding
            try:
                DBQ.join()
            except Exception:
                pass

            print(f"✅ Step 3 OK — anchors written for ~{total} runners (OC0 + bands).")
            print("READY: next step")
            self.status.config(text="• Step 3 complete → Run Step 4 (start OC1 loop)", foreground="#2c7")
            self.btn4.config(state="normal")

        threading.Thread(target=run, daemon=True).start()

    def _step4(self):
        """Start OC1 polling loop (records into oc_series every N seconds)."""
        if not self._markets:
            messagebox.showwarning("OC1", "No markets in memory. Run Step 2 first.")
            return
        if self._oc1_thread and self._oc1_thread.is_alive():
            messagebox.showinfo("OC1", "OC1 loop already running.")
            return

        def loop():
            try:
                from engines.upgrade_import_patch import get_session_token  # type: ignore
                token = (get_session_token() or "").strip()
            except Exception:
                token = (self._token.get() or "").strip()

            while True:
                for m in self._markets:
                    mid = m.get("marketId")
                    for r in (m.get("runners") or []):
                        sid = r.get("selectionId")
                        if not mid or sid is None:
                            continue
                        try:
                            odds = fetch_live_odds(token, mid, sid) or {}
                            lay = odds.get("lay")
                            _record_oc_series(mid, sid, "OC1", lay, source="SIM", meta={"odds": odds})
                        except Exception as e:
                            logging.warning(f"[OC1] {mid}/{sid} fetch error: {e}")
                # Let the writer breathe & then continue
                try:
                    DBQ.join()
                except Exception:
                    pass
                time.sleep(30)  # OC1 interval

        self._oc1_thread = threading.Thread(target=loop, name="OC1_Loop", daemon=True)
        self._oc1_thread.start()

        print("✅ Step 4 OK — OC1 loop started (30s interval).")
        print("READY: next step")
        self.status.config(text="• Step 4 running (OC1 loop). You can proceed to later phases as we add them.", foreground="#2c7")

# ------------------------------------------------------------------------------
def main():
    print(f"[GUI] DB_PATH = {DB_PATH}")
    print("[GUI] Start app → run Step 1 first.")
    app = PhaseGUI()
    app.mainloop()

if __name__ == "__main__":
    main()
