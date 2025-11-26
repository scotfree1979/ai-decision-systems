from typing import Callable, Dict
def validate_learning(*, logger: Callable[[str], None], minutes_recent: int = 2) -> Dict:
    """
    LEARNING-mode health checks that also work in quiet hours:
      - Force routing to LEARNING
      - Anchors (OC0) coverage
      - OC1 flow: recent window (minutes_recent) OR quiet fallback (last 24h)
      - Learning loop thread running (and kick if not)
      - Context + policy sanity (allows quiet-hour INFO if no context yet)
      - Recent decisions (INFO in quiet hours)
      - Orders table sanity
      - Optional API probe (if a probe module is present)
    """
    import sqlite3, threading, time
    from datetime import datetime as _dt
    from typing import Dict
    import engines.config_paths as cp

    # small helpers
    def _table_exists(con: sqlite3.Connection, name: str) -> bool:
        return bool(con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone())

    def _has_col(con: sqlite3.Connection, table: str, col: str) -> bool:
        return col in [r[1] for r in con.execute(f"PRAGMA table_info({table})")]

    out: Dict = {"summary": {"passed": 0, "failed": 0}}

    def PASS(msg): logger(f"[PASS] {msg}"); out["summary"]["passed"] += 1
    def FAIL(msg): logger(f"[FAIL] {msg}"); out["summary"]["failed"] += 1
    def INFO(msg): logger(f"[INFO] {msg}")

    # 0) Force LEARNING routing
    try:
        cp.set_db_paths(mode="learning", quiet=False)
    except Exception:
        pass
    bets = cp.bets_db()
    auto = cp.autoscalp_db()
    mode = (cp.os.environ.get("AUTOSCALP_MODE") or "learning").lower()
    if mode == "learning":
        PASS(f"DB routing — mode={mode} BETS_DB={bets} AUTO_DB={auto}")
    else:
        FAIL(f"DB routing — expected 'learning', got '{mode}' (BETS_DB={bets} AUTO_DB={auto})")

    # open DBs
    bdb = sqlite3.connect(bets, timeout=8);  bdb.row_factory = sqlite3.Row
    adb = sqlite3.connect(auto, timeout=8);  adb.row_factory = sqlite3.Row

    try:
        # Detect "quiet window" from schedule (no race for several hours)
        quiet_threshold_hours = 6.0
        try:
            row = bdb.execute(
                "SELECT off_at_utc FROM markets_schedule "
                "WHERE datetime(off_at_utc) >= datetime('now','utc') "
                "ORDER BY datetime(off_at_utc) ASC LIMIT 1"
            ).fetchone()
            next_off_h = None
            if row and row[0]:
                off = _dt.strptime(str(row[0])[:19], "%Y-%m-%d %H:%M:%S")
                next_off_h = max((off - _dt.utcnow()).total_seconds() / 3600.0, 0.0)
            quiet = (next_off_h is None) or (next_off_h >= quiet_threshold_hours)
        except Exception:
            quiet = True  # if we cannot read schedule, be conservative
        INFO(f"Window — {'quiet' if quiet else 'active'}"
             + (f" (next off in ~{int(next_off_h)}h)" if not quiet and next_off_h is not None else ""))

        # 1) Anchors (OC0) coverage
        try:
            if not _table_exists(bdb, "inbound_bets_min"):
                FAIL("Anchors (OC0) — inbound_bets_min missing")
            else:
                total = int(bdb.execute("SELECT COUNT(*) FROM inbound_bets_min").fetchone()[0] or 0)
                # try common anchor columns on BETS_DB.inbound_bets_min then fall back to *oc_cache*
                anchor_col = next((c for c in ("oc0","anchor_odd","anchor_odds","anchor")
                                   if _has_col(bdb, "inbound_bets_min", c)), None)
                if anchor_col:
                    have = int(bdb.execute(f"SELECT COUNT(*) FROM inbound_bets_min WHERE {anchor_col} IS NOT NULL").fetchone()[0] or 0)
                else:
                    have = 0
                    # fallback: inbound_oc_cache oc0/anchor_odd in BETS, else AUTO
                    for db in (bdb, adb):
                        if _table_exists(db, "inbound_oc_cache"):
                            cols = [r[1] for r in db.execute("PRAGMA table_info(inbound_oc_cache)")]
                            oc0_col = "oc0" if "oc0" in cols else ("anchor_odd" if "anchor_odd" in cols else None)
                            if oc0_col:
                                have = int(db.execute(f"SELECT COUNT(*) FROM inbound_oc_cache WHERE {oc0_col} IS NOT NULL").fetchone()[0] or 0)
                                break
                cov = (100.0 * have / total) if total else 0.0
                if total > 0 and cov >= 80.0:
                    PASS(f"Anchors (OC0) — rows={have}/{total} (≈{cov:.0f}% coverage)")
                else:
                    FAIL(f"Anchors (OC0) — rows={have}/{total} (≈{cov:.0f}% coverage)")
        except Exception as e:
            FAIL(f"Anchors (OC0) — error: {e}")

        # 2) OC1 flowing — recent minutes or quiet fallback (24h)
        try:
            if not _table_exists(adb, "inbound_oc_cache") or not _has_col(adb, "inbound_oc_cache", "oc1"):
                FAIL("OC1 flowing — AUTO_DB.inbound_oc_cache.oc1 missing")
            else:
                window = f"-{minutes_recent} minutes" if not quiet else "-24 hours"
                n = int(adb.execute(
                    "SELECT COUNT(*) FROM inbound_oc_cache "
                    "WHERE oc1 IS NOT NULL AND datetime(COALESCE(last_sync_ts,'')) >= datetime('now', ?, 'utc')",
                    (window,)
                ).fetchone()[0] or 0)
                if n > 0:
                    PASS(f"OC1 flowing — rows={n} in window {window}")
                else:
                    FAIL(f"OC1 flowing — rows=0 in window {window}")
        except Exception as e:
            FAIL(f"OC1 flowing — error: {e}")

        # 3) Optional API probe (only if a probe module exists)
        try:
            probe_fn = None
            try:
                from engines.live.api_probe import probe_api_health as probe_fn  # type: ignore
            except Exception:
                try:
                    from engines.live.api import ping as probe_fn  # type: ignore
                except Exception:
                    probe_fn = None
            if probe_fn:
                ok, msg = False, "uninitialized"
                try:
                    ok, msg = probe_fn(timeout=3)
                except TypeError:
                    ok, msg = probe_fn(), "ok"  # fallback signature
                if ok:
                    PASS(f"API probe — {msg}")
                else:
                    FAIL(f"API probe — {msg}")
            else:
                INFO("API probe — not configured (skipping)")
        except Exception as e:
            INFO(f"API probe — error: {e} (skipping)")

        # 4) Learning loop thread present? (kick if not)
        try:
            alive = any(t.name == "LearningLoop" and t.is_alive() for t in threading.enumerate())
            if alive:
                PASS("Learning loop — running")
            else:
                INFO("Learning loop — not detected, attempting start…")
                try:
                    from engines.decision_engine.orchestrator import start_learning_loop
                    rid = f"LEARN-{_dt.utcnow().strftime('%Y%m%d-%H%M%S')}"
                    threading.Thread(
                        target=lambda: start_learning_loop(run_id=rid, hz=2, logger=logger),
                        name="LearningLoop",
                        daemon=True
                    ).start()
                    time.sleep(0.25)
                    alive2 = any(t.name == "LearningLoop" and t.is_alive() for t in threading.enumerate())
                    if alive2:
                        PASS("Learning loop — started")
                    else:
                        FAIL("Learning loop — failed to start")
                except Exception as e:
                    FAIL(f"Learning loop — start error: {e}")
        except Exception as e:
            FAIL(f"Learning loop — check failed: {e}")

        # 5) Context + Policy — must be callable now (quiet-hour tolerant)
        try:
            from engines.mastery.context_builder import build_context
            import engines.mastery.mastery_policy as mp
            ctx, meta = build_context(source="LEARNING")
            mid = meta.get("marketId") or ctx.get("marketId")
            sid = meta.get("selectionId") or ctx.get("selectionId")
            if not (mid and sid):
                if quiet:
                    INFO("Context — no market/selection yet (quiet hours)")
                else:
                    FAIL("Context — missing marketId/selectionId")
            else:
                PASS("Context — market/selection present")
                try:
                    plan = mp.propose_trade(ctx)
                    if isinstance(plan, dict) and ("enter" in plan or "why" in plan):
                        why = plan.get("why","")
                        if plan.get("enter"):
                            PASS(f"Policy — ENTER permitted (why='{why[:60]}')")
                        else:
                            INFO(f"Policy — NO-TRADE (why='{why[:60]}')")
                    else:
                        FAIL("Policy — invalid response")
                except Exception as e:
                    FAIL(f"Policy — error: {e}")
        except Exception as e:
            FAIL(f"Context — build error: {e}")

        # 6) Decisions recently (quiet-hour tolerant)
        try:
            if not _table_exists(adb, "decisions"):
                FAIL("Decisions — table missing (AUTO_DB)")
            else:
                col = "decided_at" if _has_col(adb,"decisions","decided_at") else ("opened_at" if _has_col(adb,"decisions","opened_at") else None)
                if not col:
                    FAIL("Decisions — no timestamp column")
                else:
                    dn = int(adb.execute(
                        f"SELECT COUNT(*) FROM decisions WHERE datetime(COALESCE({col},'')) >= datetime('now', ?, 'utc')",
                        (f"-{minutes_recent} minutes",)
                    ).fetchone()[0] or 0)
                    if dn > 0:
                        PASS(f"Decisions — rows={dn} in last {minutes_recent}m")
                    else:
                        if quiet:
                            INFO(f"Decisions — 0 in last {minutes_recent}m (quiet hours)")
                        else:
                            FAIL(f"Decisions — 0 in last {minutes_recent}m")
        except Exception as e:
            FAIL(f"Decisions — error: {e}")

        # 7) Orders sanity (AUTO_DB)
        try:
            if not _table_exists(adb, "orders"):
                FAIL("Orders — table missing (AUTO_DB)")
            else:
                rows = int(adb.execute("SELECT COUNT(*) FROM orders").fetchone()[0] or 0)
                open_ = int(adb.execute("SELECT COUNT(*) FROM orders WHERE (closed_at IS NULL OR closed_at='')").fetchone()[0] or 0)
                PASS(f"Orders — rows={rows}, open={open_}")
        except Exception as e:
            FAIL(f"Orders — error: {e}")

        # 8) Stories/Chapters (optional)
        try:
            if _table_exists(bdb, "stories") and _table_exists(bdb, "chapters"):
                s = int(bdb.execute("SELECT COUNT(*) FROM stories").fetchone()[0] or 0)
                c = int(bdb.execute("SELECT COUNT(*) FROM chapters").fetchone()[0] or 0)
                PASS(f"Stories/Chapters — stories={s}, chapters={c}")
            else:
                INFO("Stories/Chapters — tables not present (optional)")
        except Exception as e:
            INFO(f"Stories/Chapters — check error: {e}")

        # 9) oc_series informational
        try:
            present = _table_exists(bdb, "oc_series")
            INFO(f"Known issue — oc_series present={present}; not required.")
        except Exception:
            pass

    finally:
        try: bdb.close()
        except Exception: pass
        try: adb.close()
        except Exception: pass

    return out
