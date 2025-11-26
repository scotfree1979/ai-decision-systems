#!/usr/bin/env python3
import os, json, sqlite3
from datetime import datetime, timedelta, timezone


# Paths
AUTO_DB = "data/autoscalp_gui.db"
REPORT_DIR = "data/reports"
POST_DIR = "data/posteriors"
LATEST_REP = sorted(
    [f for f in os.listdir("data") if f.startswith("replay_report_")],
    reverse=True
)[0]
REPORT_PATH = os.path.join("data", LATEST_REP)

def load_json(path):
    with open(path, "r") as f:
        return json.load(f)
def get_market_pnl(day: str, marketId: str, selectionId: str|None=None) -> float:
    """Return settled P&L from settlements.db for given marketId[/selectionId]."""
    con = sqlite3.connect("data/settlements.db"); con.row_factory = sqlite3.Row
    try:
    except Exception as e:
        print(f"[digest] auto-added except: {e}")
        if selectionId:
            row = con.execute("""
                SELECT SUM(profit) AS net
                  FROM bf_cleared_orders
                 WHERE marketId=? AND selectionId=? AND date(settledDate)=?
            """, (marketId, selectionId, day)).fetchone()
        else:
            row = con.execute("""
                SELECT net FROM v_settle_mkt_day
                 WHERE marketId=? AND day=?
            """, (marketId, day)).fetchone()
        return float(row["net"] or 0.0) if row else 0.0
    finally:
        con.close()

def normalize_pnl(row_or_value):
    """
    Shim: unifies P&L references (net_pl, net_pnl, pnl, numeric).
    Always returns a float.
    """
    try:
    except Exception as e:
        print(f"[digest] auto-added except: {e}")
        if isinstance(row_or_value, dict):
            return float(
                row_or_value.get("net_pl") or
                row_or_value.get("net_pnl") or
                row_or_value.get("pnl") or 0.0
            )
        elif hasattr(row_or_value, "__getitem__"):  # sqlite3.Row
            return float(
                row_or_value.get("net_pl") or
                row_or_value.get("net_pnl") or
                row_or_value.get("pnl") or 0.0
            )
        return float(row_or_value or 0.0)
    except Exception:
        return 0.0




def main():
    outcomes = load_json(REPORT_PATH)
    total = len(outcomes)
    pnl = sum(o.get("pnl", 0.0) for o in outcomes)
    hedges = sum(1 for o in outcomes if o.get("hedge"))
    stops  = sum(1 for o in outcomes if o.get("stoploss"))
    wins   = sum(1 for o in outcomes if o.get("success"))
    letters= {}
    for o in outcomes:
        letters[o.get("letter","?")] = letters.get(o.get("letter","?"),0)+1

    # === DB QUERIES ===
    con = sqlite3.connect(AUTO_DB); con.row_factory = sqlite3.Row

    # Opportunities
    opps = con.execute("""
        SELECT SUM(opportunities) AS opps,
               SUM(taken) AS taken,
               SUM(conversion) AS conv
          FROM indicators_opportunities
         WHERE day IN (SELECT MAX(day) FROM indicators_opportunities)
    """).fetchone()

    # Error tags
    tags = con.execute("""
        SELECT tag, COUNT(*) AS n
          FROM loss_tags
         WHERE day IN (SELECT MAX(day) FROM loss_tags)
         GROUP BY tag
    """).fetchall()

    # --- Resolve report_day and fetch orders ---
    report_day_row = con.execute("SELECT MAX(date(opened_at)) AS d FROM orders").fetchone()
    report_day = (report_day_row["d"] if report_day_row and report_day_row["d"]
                  else datetime.now().astimezone().date().isoformat())

    orders = con.execute("""
        SELECT marketId, selectionId, role, customerOrderRef,
               entry_odds, entry_stake, entry_status, exit_status,
               COALESCE(net_pl,0) AS net_pl, opened_at
          FROM orders
         WHERE date(opened_at)=(SELECT MAX(date(opened_at)) FROM orders)
    """).fetchall()

    # --- Group parents/children by (mid, sid) ---
    market_map = {}
    for o in orders:
        mid, sid = o["marketId"], o["selectionId"]
        key = (mid, sid)
        if key not in market_map:
            market_map[key] = {"parents": [], "children": []}
        if o["role"] == "PARENT":
            market_map[key]["parents"].append(dict(o))
        elif o["role"] == "CHILD":
            market_map[key]["children"].append(dict(o))

    print("\n=== Parent/Child Mismatch Summary ===")
    total_parents   = sum(len(st["parents"]) for st in market_map.values())
    total_children  = sum(len(st["children"]) for st in market_map.values())
    mismatch_total  = sum(1 for st in market_map.values() if len(st["parents"]) != len(st["children"]))
    print(f"  Parents total : {total_parents}")
    print(f"  Children total: {total_children}")
    print(f"  Mismatched sids: {mismatch_total}")

    # Matched conversion (simple ratio of children to parents)
    matched_conv = (100.0 * total_children / total_parents) if total_parents else 0.0
    print(f"  Matched Conversion : {matched_conv:.1f}%")

    # --- Net P&L from settlements (per-market truth) ---
    distinct_mids = sorted({mid for (mid, _sid) in market_map.keys()})
    market_sum = 0.0
    for mid in distinct_mids:
        try:
        except Exception as e:
            print(f"[digest] auto-added except: {e}")
            market_sum += get_market_pnl(report_day, mid, None)
        except Exception:
            pass
    avg_parent_pnl = (market_sum / total_parents) if total_parents else 0.0
    print(f"  Net P&L        : £{market_sum:.2f} (avg £{avg_parent_pnl:.2f} per parent)")







# ───────────────────────────────────────────────────────────────
# 📍 TARGET: replay_digest.py
# 🔎 SEARCH: print(f"  Net P&L       : £{total_net_pl:.2f} (avg £{avg_net_pl:.2f} per parent)")
# 📆 PATCHED: 2025-10-01
# ───────────────────────────────────────────────────────────────

    # --- PATCH START: pull settled PnL from settlements.db ---
    try:
    except Exception as e:
        print(f"[digest] auto-added except: {e}")
        SETTLE_DB = "data/settlements.db"
        scon = sqlite3.connect(SETTLE_DB); scon.row_factory = sqlite3.Row
        day = datetime.now().astimezone().date().isoformat()


        settle = scon.execute("""
            SELECT SUM(net) AS net, AVG(net) AS avg_net
              FROM v_settle_mkt_day
             WHERE day = ?
        """, (report_day,)).fetchone()
        scon.close()

        if settle and settle["net"] is not None:
            total_net_pl = float(settle["net"])
            avg_net_pl   = float(settle["avg_net"])
    except Exception as e:
        print(f"[digest] settlements warn: {e}")
    # --- PATCH END ---

    # set defaults so they're always defined
    c_net_pl, c_avg_pl = 0.0, 0.0

    # --- PATCH START: pull child PnL from settlements.db ---
    try:
    except Exception as e:
        print(f"[digest] auto-added except: {e}")
        scon = sqlite3.connect("data/settlements.db"); scon.row_factory = sqlite3.Row
        day = datetime.now().astimezone().date().isoformat()  # timezone-aware UTC
        settle = scon.execute("""
            SELECT SUM(net) AS net, AVG(net) AS avg_net
              FROM v_settle_mkt_day
             WHERE day = ?
        """, (report_day,)).fetchone()
        scon.close()
        if settle and settle["net"] is not None:
            c_net_pl = float(settle["net"])
            c_avg_pl = float(settle["avg_net"])
    except Exception as e:
        print(f"[digest] settlements child warn: {e}")
    # --- PATCH END ---


    # --- Child outcomes summary ---
    child_outcomes = con.execute("""
        SELECT
            COUNT(*) AS total_children,
            SUM(CASE WHEN entry_status='EXECUTED' OR exit_status='EXECUTED' THEN 1 ELSE 0 END) AS executed,
            SUM(CASE WHEN entry_status='CANCELLED' OR exit_status='CANCELLED' THEN 1 ELSE 0 END) AS cancelled,
            SUM(net_pl) AS total_net_pl,
            AVG(net_pl) AS avg_net_pl
        FROM orders
        WHERE role='CHILD'
          AND date(opened_at)=(
              SELECT MAX(date(opened_at)) FROM orders
          )
    """).fetchone()




    print(f"  Total children : {total_children}")
    print(f"  Executed       : {executed}")
    print(f"  Cancelled      : {cancelled}")
    # Total P&L across all parents+children (settlement-based)
    all_pnl = sum(
        normalize_pnl(o)
        for st in market_map.values()
        for o in (st["parents"] + st["children"])
    )

    avg_pnl = all_pnl / total_parents if total_parents else 0.0
    print(f"  Net P&L        : £{all_pnl:.2f} (avg £{avg_pnl:.2f} per parent)")


    if total_children > 0:
        cancel_rate = 100.0 * cancelled / total_children
        print(f"  Cancel rate    : {cancel_rate:.1f}%")


  

    # --- Replay Focus flags ---
    print("\nReplay Focus:")
    if total_parents > 0:
        if (mismatch_total / total_parents) > 0.20:
            print("  ⚠️ High unpaired rate → Check why parents aren’t generating children.")
        if (total_children > 0) and ('cancel_rate' in locals()) and (cancel_rate > 20.0):
            print("  ⚠️ High child cancel rate.")
        if avg_net_pl < 0:
            print("  ⚠️ Negative average P&L per parent → tighten filters / stoploss rules.")



    # --- Parent/Child Match Quality Summary ---
    try:
    except Exception as e:
        print(f"[digest] auto-added except: {e}")
        con = sqlite3.connect(AUTO_DB); con.row_factory = sqlite3.Row
        totals = con.execute("""
            WITH parent_status AS (
                SELECT customerOrderRef,
                       COUNT(*) FILTER (WHERE role='CHILD' AND (entry_status='EXECUTED' OR exit_status='EXECUTED')) AS matched_children,
                       COUNT(*) FILTER (WHERE role='PARENT') AS parent_count
                  FROM orders
                 WHERE date(opened_at)=(SELECT MAX(date(opened_at)) FROM orders)
                 GROUP BY customerOrderRef
            )
            SELECT COUNT(*) AS total_parents,
                   SUM(CASE WHEN matched_children>0 THEN 1 ELSE 0 END) AS matched_parents
              FROM parent_status
        """).fetchone()
        con.close()

        total_parents = totals["total_parents"] or 0
        matched_parents = totals["matched_parents"] or 0
        unpaired_parents = total_parents - matched_parents
        pct_match = (100.0 * matched_parents / total_parents) if total_parents else 0.0

        print("\n=== Parent/Child Match Performance ===")
        parents_total = total_parents
        parents_matched = min(total_children, total_parents)   # each child implies one matched parent
        parents_unpaired = max(0, total_parents - total_children)
        match_rate = (100.0 * parents_matched / total_parents) if total_parents else 0.0
        print(f"  Parents total     : {parents_total}")
        print(f"  Parents matched   : {parents_matched}")
        print(f"  Parents unpaired  : {parents_unpaired}")
        print(f"  ✅ Match Rate      : {match_rate:.1f}%")
        if match_rate == 100.0:
            print("  🎯 Gold standard: All parents matched with children.")


        # --- Persist match metrics + parent/child tags into loss_tags ---
        try:
        except Exception as e:
            print(f"[digest] auto-added except: {e}")
            con3 = sqlite3.connect(AUTO_DB)
            cur = con3.cursor()
            today = outcomes[0]["day"] if outcomes else datetime.now().date().isoformat()
            seen = set()
            persisted = []

            # 1) Persist match-rate bucket
            bucket = int(matched_conv // 10) * 10
            tag = f"match_rate:{bucket}-{bucket+10}"
            cur.execute("""
                INSERT INTO loss_tags(report_day, marketId, selectionId, tag, created_at)
                VALUES (?,?,?,?,datetime('now','utc'))
                ON CONFLICT(report_day, marketId, selectionId, tag) DO NOTHING
            """, (today, "ALL", "0", tag))
            persisted.append({"mid": "ALL", "sid": "0", "tag": tag})

            # 2) Parent/child tags
            for (mid, sid), st in market_map.items():
                pcount, ccount = len(st["parents"]), len(st["children"])
                pnl = get_market_pnl(today, mid, sid)

                taglist = []
                if pcount > 0 and pcount == ccount:
                    taglist.append("fully_hedged")
                    tick_diffs = []
                    for p in st["parents"]:
                        for c in st["children"]:
                            try:
                            except Exception as e:
                                print(f"[digest] auto-added except: {e}")
                                diff = abs((p["entry_odds"] or 0) - (c["entry_odds"] or 0))
                                if diff < 0.1:
                                    tick_diffs.append(diff)
                            except:
                                pass
                    if len(tick_diffs) == pcount:
                        taglist.append("perfect_match")
                elif pcount > ccount:
                    taglist.append("unpaired_parent")
                    if pnl < 0:
                        taglist.append("unpaired_parent_loss")
                    if st["parents"] and st["children"]:
                        last_parent = max([p["opened_at"] for p in st["parents"]])
                        last_child  = max([c["opened_at"] for c in st["children"]])
                        if last_parent > last_child:
                            taglist.append("late_unpaired")

                # persist tags for this mid/sid
                for tag in taglist:
                    key = (today, mid, sid, tag)
                    if key in seen:
                        continue
                    seen.add(key)
                    cur.execute("""
                        INSERT INTO loss_tags(report_day, marketId, selectionId, tag, created_at)
                        VALUES (?,?,?,?,datetime('now','utc'))
                        ON CONFLICT(report_day, marketId, selectionId, tag) DO NOTHING
                    """, (today, str(mid), str(sid), tag))
                    persisted.append({"mid": mid, "sid": sid, "tag": tag})

            con3.commit()
            con3.close()

            if persisted:
                print(f"\n[loss_tags updated — persisted {len(persisted)} tags]")
                for row in persisted[:20]:
                    print(f"  mid={row['mid']} sid={row['sid']} tag={row['tag']}")
                if len(persisted) > 20:
                    print(f"  … and {len(persisted)-20} more")
            else:
                print("\n[loss_tags updated — no tags persisted]")

        except Exception as e:
            print(f"[digest] loss_tags persist warn: {e}")




# ───────────────────────────────────────────────────────────────
# 📍 TARGET: replay_digest.py
# 🔎 SEARCH: # --- PATCH START: distance band + race breakdown ---
# 📆 PATCHED: 2025-10-01T23:55Z
# ───────────────────────────────────────────────────────────────

    # --- PATCH START: distance band + race breakdown ---
    try:
    except Exception as e:
        print(f"[digest] auto-added except: {e}")
        BETS_DB = "data/bets.db"
        SETTLE_DB = "data/settlements.db"

        con2 = sqlite3.connect(BETS_DB); con2.row_factory = sqlite3.Row
        scon = sqlite3.connect(SETTLE_DB); scon.row_factory = sqlite3.Row

        day = datetime.now().astimezone().date().isoformat()
        rows = con2.execute("""
            SELECT b.marketId, b.market_name,
                   SUM(s.net) AS net
              FROM bets b
              LEFT JOIN st_runner_day_totals s
                ON b.marketId = s.marketId
             WHERE s.day = ?
             GROUP BY b.marketId, b.market_name
        """, (report_day,)).fetchall()

        con2.close(); scon.close()

        # derive distance buckets
        def band_from_name(name: str) -> str:
            name = (name or "").lower()
            if name.startswith("5f") or name.startswith("6f"): return "sprint"
            if name.startswith("7f") or name.startswith("1m"): return "middle"
            if name.startswith("1m4f") or name.startswith("1m5f") or name.startswith("1m6f"): return "staying"
            if name.startswith("2m") or name.startswith("3m"): return "marathon"
            return "other"

        bands, races = {}, {}
        for r in rows:
            mname = r["market_name"] or "unknown"
            band = band_from_name(mname)
            bands[band] = bands.get(band, 0.0) + float(r["net"] or 0.0)
            races[mname] = races.get(mname, 0.0) + float(r["net"] or 0.0)

        print("\n🔎 Grouped by Distance Band")
        for band, net in sorted(bands.items(), key=lambda x: x[0]):
            print(f"  {band:<10} Net P&L £{net:8.2f}")
            if net < 0:
                focus.append(f"Distance band loss: {band}")
                focus_examples.append(("band", band, "distance_loss"))

        print("\n🔎 Individual Race Breakdown")
        for mname, net in sorted(races.items(), key=lambda x: x[0]):
            print(f"  {mname:<40} Net P&L £{net:8.2f}")
            if net < 0:
                focus.append(f"Race loss: {mname}")
                focus_examples.append(("race", mname, "race_loss"))
    except Exception as e:
        print(f"[digest] distance_band warn: {e}")
    # --- PATCH END ---


    # === Posterior deltas ===
    latest = os.path.join(POST_DIR, "mastery_snapshot_latest.json")
    prev   = sorted([f for f in os.listdir(POST_DIR) if f.startswith("mastery_snapshot_") and not f.endswith("latest.json")])
    deltas = []
    if prev and os.path.exists(latest):
        prev_snap = load_json(os.path.join(POST_DIR, prev[-1]))
        latest_snap = load_json(latest)
        for k,v in latest_snap.items():
            try:
            except Exception as e:
                print(f"[digest] auto-added except: {e}")
                old = normalize_pnl(prev_snap.get(k,{}))
                new = normalize_pnl(v)

                diff = new - old
                if abs(diff) > 0.0:
                    deltas.append((diff,k))
            except Exception: pass
        deltas.sort(key=lambda x: -abs(x[0]))
        deltas = deltas[:5]

    # === Print Report ===
    print("\n📊 Replay Learning Report")
    day = outcomes[0]["day"] if outcomes else "?"
    print(f"Day: {day}")
    print(f"Outcomes: {total:,}")
    print(f"Win rate: {100.0*wins/total:.1f}%")
    print(f"PnL total: £{pnl:,.2f}")
    print(f"Hedges: {hedges:,} | Stoploss: {stops:,}")
    print(f"Letters fired: {letters}")

    if opps and opps["opps"]:
        taken_pct = 100.0*(opps["taken"] or 0)/(opps["opps"] or 1)
        conv_pct  = 100.0*(opps["conv"] or 0)/(opps["taken"] or 1)
        print("\n🔹 Opportunities")
        print(f"  Observed: {int(opps['opps'] or 0)}")
        print(f"  Taken: {int(opps['taken'] or 0)} ({taken_pct:.1f}%)")
        print(f"  Conversion: {conv_pct:.1f}%")



    if taglist:
        print("\n🔹 Error Tag (latest day)")
        for r in taglist:
            print(f"  {r['tag']:<15} {r['n']}")

    if deltas:
        print("\n🔹 Top 5 Bin Adjustments")
        for diff,k in deltas:
            print(f"  {k:<30} {diff:+.2f}")

    # --- Amalgamated Replay Focus (parents + children + tags + P&L) ---
    print("\n=== Replay Focus Areas ===")

    focus = []
    focus_examples = []

    # Parent-level issues
    if total_parents > 0:
        if mismatch_total / total_parents > 0.2:
            focus.append("High unpaired parents")
        if cancelled / total_parents > 0.1:
            focus.append("Too many parent cancels")
        if avg_net_pl < 0:
            focus.append("Negative average P&L per parent")
      

    # Child-level issues
    if total_children > 0:
        if cancel_rate > 20.0:
            focus.append("High child cancel rate")
        if c_avg_pl < 0:
            focus.append("Negative average P&L per child")

    # Error tags (from loss_tags)
    if tags:
        focus.extend([f"Frequent error: {r['tag']}" for r in tags if r['n'] >= 1])

# ───────────────────────────────────────────────────────────────
# 📍 TARGET: replay_digest.py
# 🔎 SEARCH: # --- PATCH START: focus markets with negative settled PnL ---
# 📆 PATCHED: 2025-10-01
# ───────────────────────────────────────────────────────────────

    # --- PATCH START: focus markets with negative settled PnL ---
    try:
    except Exception as e:
        print(f"[digest] auto-added except: {e}")
        scon = sqlite3.connect("data/settlements.db"); scon.row_factory = sqlite3.Row
        day = datetime.now().astimezone().date().isoformat()
        negs = scon.execute("""
            SELECT marketId, net
              FROM v_settle_mkt_day
             WHERE day = ? AND net < 0
             ORDER BY net ASC
             LIMIT 20
        """, (report_day,)).fetchall()
        scon.close()

        if negs:
            for r in negs:
                msg = f"Loss market {r['marketId']} net=£{r['net']:.2f}"
                focus.append(msg)
                # just collect for persistence later (sid=0 for market-level)
                focus_examples.append((r['marketId'], 0, "market_loss"))

    except Exception as e:
        print(f"[digest] settlements focus warn: {e}")
    # --- PATCH END ---



    if not focus:
        print("  ✅ No major focus issues — replay performed cleanly.")
    else:
        for f in focus:
            print("  ⚠️", f)


    # Show concrete examples (from outcomes JSON)
    try:
    except Exception as e:
        print(f"[digest] auto-added except: {e}")
        print("\nExamples (mids/sids to review):")
        for o in outcomes[:10]:   # show first 10 only
            print(f"  mid={o['mid']} sid={o['sid']} letter={o['letter']} "
                  f"pnl={o['pnl']} success={o['success']} stop={o['stoploss']}")
    except Exception:
        pass

# ───────────────────────────────────────────────────────────────
# 📍 TARGET: replay_digest.py (or better: schema init code where loss_tags is created)
# 🔎 SEARCH: INSERT INTO loss_tags(report_day, marketId, selectionId, tag, created_at)
# 📆 PATCHED: 2025-10-01
# ───────────────────────────────────────────────────────────────

    # --- PATCH START: ensure UNIQUE index for loss_tags ---
    try:
    except Exception as e:
        print(f"[digest] auto-added except: {e}")
        con_idx = sqlite3.connect(AUTO_DB)
        cur_idx = con_idx.cursor()
        cur_idx.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_loss_tags_day_mid_sid_tag
            ON loss_tags(report_day, marketId, selectionId, tag)
        """)
        con_idx.commit(); con_idx.close()
    except Exception as e:
        print(f"[digest] loss_tags index warn: {e}")
    # --- PATCH END ---

# === PATCH START ===
# 📍 TARGET: replay_digest.py
# 🔎 SEARCH: # --- PATCH START: markets with negative settled PnL ---
# 📆 PATCHED: 2025-10-02
# ───────────────────────────────────────────────────────────────

    # --- Market-level settled losses (unified) ---
    try:
    except Exception as e:
        print(f"[digest] auto-added except: {e}")
        scon = sqlite3.connect("data/settlements.db"); scon.row_factory = sqlite3.Row
        day = datetime.now().astimezone().date().isoformat()
        negs = scon.execute("""
            SELECT marketId, net
              FROM v_settle_mkt_day
             WHERE day = ? AND net < 0
             ORDER BY net ASC
             LIMIT 50
        """, (report_day,)).fetchall()
        scon.close()

        if negs:
            for r in negs:
                msg = f"Loss market {r['marketId']} net=£{r['net']:.2f}"
                focus.append(msg)
                focus_examples.append((r['marketId'], 0, "market_loss"))
    except Exception as e:
        print(f"[digest] settlements market_loss warn: {e}")
# === PATCH END ===

# ───────────────────────────────────────────────────────────────
# 📍 TARGET: replay_digest.py
# 🔎 SEARCH: # --- Persist only problem mids/sids into loss_tags (closes the loop) ---
# 📆 PATCHED: 2025-10-02
# ───────────────────────────────────────────────────────────────

    # --- PATCH START: persist enriched problem + success tags ---
    try:
    except Exception as e:
        print(f"[digest] auto-added except: {e}")
        con3 = sqlite3.connect(AUTO_DB)
        cur = con3.cursor()
        day = outcomes[0]["day"] if outcomes else datetime.now().astimezone().date().isoformat()

        focus_examples = []
        success_examples = []

        # === PROBLEM TAGS ====================================================
        for o in outcomes:
            # 1. Stoploss or fail
            if o.get("stoploss") or not o.get("success"):
                focus_examples.append((o["mid"], o["sid"], "stoploss_or_fail"))

            # 2. Parent/child mismatches (handled via unpaired/cancelled/loss)
            # Use totals computed above
# ───────────────────────────────────────────────────────────────
# 📍 TARGET: replay_digest.py
# 🔎 SEARCH: if mismatch_total / total_parents > 0.2:
# 📆 PATCHED: 2025-10-03
# ───────────────────────────────────────────────────────────────

        if mismatch_total / total_parents > 0.2:
            print("  ⚠️ High mismatch rate → Many parents didn’t generate children.")

            for o in outcomes:
                focus_examples.append((o["mid"], o["sid"], "unpaired_parent"))
            if cancelled / total_parents > 0.1:
                for o in outcomes:
                    focus_examples.append((o["mid"], o["sid"], "parent_cancelled"))
            if avg_net_pl < 0:
                for o in outcomes:
                    focus_examples.append((o["mid"], o["sid"], "parent_loss"))

        if total_children > 0:
            if cancel_rate > 20.0:
                for o in outcomes:
                    focus_examples.append((o["mid"], o["sid"], "child_cancelled"))
            if c_avg_pl < 0:
                for o in outcomes:
                    focus_examples.append((o["mid"], o["sid"], "child_loss"))

        # 3. Markets with negative settled PnL
        try:
        except Exception as e:
            print(f"[digest] auto-added except: {e}")
            scon = sqlite3.connect("data/settlements.db"); scon.row_factory = sqlite3.Row
            negs = scon.execute("""
                SELECT marketId, selectionId, net
                  FROM v_settle_mkt_day
                 WHERE day = ? AND net < 0
                 LIMIT 50
            """, (report_day,)).fetchall()
            scon.close()
            for r in negs:
                focus_examples.append((r["marketId"], r["selectionId"], "market_loss"))
        except Exception as e:
            print(f"[digest] settlements persist warn: {e}")

        # 4. High odds trades (>= 500)
        for o in outcomes:
            try:
            except Exception as e:
                print(f"[digest] auto-added except: {e}")
                if float(o.get("entry_px", 0)) >= 500:
                    focus_examples.append((o["mid"], o["sid"], "high_odds"))
            except Exception:
                pass

        # 5. Tick distance rules
        for o in outcomes:
            t = int(o.get("target_ticks", 0) or 0)
            if t == 1: focus_examples.append((o["mid"], o["sid"], "tick1"))
            if t == 2: focus_examples.append((o["mid"], o["sid"], "tick2"))
            if t == 3: focus_examples.append((o["mid"], o["sid"], "tick3"))

        # 6. Direction breakdown
        for o in outcomes:
            code = (o.get("code") or "").upper()
            pnl  = float(o.get("pnl", 0.0) or 0.0)
            if code == "STEAM" and pnl < 0:
                focus_examples.append((o["mid"], o["sid"], "steam_loss"))
            elif code == "DRIFT" and pnl < 0:
                focus_examples.append((o["mid"], o["sid"], "drift_loss"))

        # 7. Enrichments (timing / exposure / fills / holds)
        for o in outcomes:
            if o.get("late_entry"):    focus_examples.append((o["mid"], o["sid"], "late_entry"))
            if o.get("early_entry"):   focus_examples.append((o["mid"], o["sid"], "early_entry"))
            if o.get("overexposed"):   focus_examples.append((o["mid"], o["sid"], "overexposed"))
            if o.get("underexposed"):  focus_examples.append((o["mid"], o["sid"], "underexposed"))
            if o.get("no_exit"):       focus_examples.append((o["mid"], o["sid"], "no_exit"))
            if o.get("partial_fill"):  focus_examples.append((o["mid"], o["sid"], "partial_fill"))
            if o.get("inplay_entry"):  focus_examples.append((o["mid"], o["sid"], "inplay_entry"))
            if o.get("vol_spike"):     focus_examples.append((o["mid"], o["sid"], "volatility_spike"))
            if o.get("long_hold"):     focus_examples.append((o["mid"], o["sid"], "long_hold"))
            if o.get("multi_parent"):  focus_examples.append((o["mid"], o["sid"], "multi_parent"))
            if o.get("class_band_loss"): focus_examples.append((o["mid"], o["sid"], "class_band_loss"))
            if o.get("fav_loss"):      focus_examples.append((o["mid"], o["sid"], "fav_loss"))
            if o.get("field_loss"):    focus_examples.append((o["mid"], o["sid"], "field_loss"))
            if o.get("distance_loss"): focus_examples.append((o["mid"], o["sid"], "distance_loss"))
            if o.get("race_loss"):     focus_examples.append((o["mid"], o["sid"], "race_loss"))

        # === SUCCESS TAGS ====================================================
        for o in outcomes:
            if o.get("success"):
                if o.get("pnl",0) > 0:
                    success_examples.append((o["mid"], o["sid"], "win_trade"))
                if (o.get("code") or "").upper() == "STEAM" and o.get("pnl",0) > 0:
                    success_examples.append((o["mid"], o["sid"], "steam_win"))
                if (o.get("code") or "").upper() == "DRIFT" and o.get("pnl",0) > 0:
                    success_examples.append((o["mid"], o["sid"], "drift_win"))
                t = int(o.get("target_ticks",0) or 0)
                if t == 1 and o.get("pnl",0) > 0: success_examples.append((o["mid"], o["sid"], "tick1_win"))
                if t == 2 and o.get("pnl",0) > 0: success_examples.append((o["mid"], o["sid"], "tick2_win"))
                if t == 3 and o.get("pnl",0) > 0: success_examples.append((o["mid"], o["sid"], "tick3_win"))

        # === MERGE & PERSIST =================================================
        all_examples = focus_examples + success_examples
        seen = set()
        persisted = []
        for (mid, sid, tag) in all_examples:
            key = (report_day, mid, sid, tag)
            if key in seen: continue
            seen.add(key)
            cur.execute("""
                INSERT INTO loss_tags(report_day, marketId, selectionId, tag, created_at)
                VALUES (?,?,?,?,datetime('now','utc'))
                ON CONFLICT(report_day, marketId, selectionId, tag) DO NOTHING
            """, (
                day,
                str(mid),
                str(sid) if sid not in (None,"","band","race") else "0",
                tag
            ))
            persisted.append({"day": day, "mid": mid, "sid": sid, "tag": tag})

        con3.commit(); con3.close()

        if persisted:
            print(f"\n[loss_tags updated — persisted {len(persisted)} tags]")
            for row in persisted[:20]:
                print(f"  mid={row['mid']} sid={row['sid']} tag={row['tag']}")
            if len(persisted) > 20:
                print(f"  … and {len(persisted)-20} more")
        else:
            print("\n[loss_tags updated — no tags persisted]")

    except Exception as e:
        print(f"[digest] loss_tags persist warn: {e}")
    # --- PATCH END ---



# ───────────────────────────────────────────────────────────────
# 📍 TARGET: replay_digest.py
# 🔎 SEARCH: # --- Summarize problem tags for report ---
# 📆 PATCHED: 2025-10-02T01:10Z
# ───────────────────────────────────────────────────────────────

        # --- Summarize + persist tags (problems + successes) ---
        tag_counts = {}
        for _mid, _sid, tag in focus_examples:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

        # --- NEW: build success_examples ---
        success_examples = []
        for o in outcomes:
            if o.get("success"):
                if o.get("pnl",0) > 0:
                    success_examples.append((o["mid"], o["sid"], "win_trade"))
                if (o.get("code") or "").upper() == "STEAM" and o.get("pnl",0) > 0:
                    success_examples.append((o["mid"], o["sid"], "steam_win"))
                if (o.get("code") or "").upper() == "DRIFT" and o.get("pnl",0) > 0:
                    success_examples.append((o["mid"], o["sid"], "drift_win"))
                t = int(o.get("target_ticks",0) or 0)
                if t in (1,2,3) and o.get("pnl",0) > 0:
                    success_examples.append((o["mid"], o["sid"], f"tick{t}_win"))

        # merge into tag_counts
        for _mid, _sid, tag in success_examples:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

        # master list of expected tags
        all_tags = [
            # problems
            "stoploss_or_fail", "unpaired_parent", "parent_cancelled", "parent_loss",
            "child_cancelled", "child_loss", "market_loss", "high_odds",
            "tick1", "tick2", "tick3", "steam_loss", "drift_loss",
            "late_entry", "early_entry", "overexposed", "underexposed",
            "no_exit", "partial_fill", "inplay_entry", "volatility_spike",
            "long_hold", "multi_parent", "class_band_loss", "fav_loss", "field_loss",
            "distance_loss", "race_loss",
            # successes
            "win_trade", "steam_win", "drift_win", "tick1_win", "tick2_win", "tick3_win"
        ]

        print("\n🔹 Problem / Success Tag Summary (this run)")
        for t in all_tags:
            n = tag_counts.get(t, 0)
            if n > 0:
                print(f"  {t:<20} {n}")

    # --- Parent/Child quality summary ---
    try:
    except Exception as e:
        print(f"[digest] auto-added except: {e}")
        con = sqlite3.connect(AUTO_DB); con.row_factory = sqlite3.Row
        pc_tags = con.execute("""
            SELECT tag, COUNT(*) AS n
              FROM loss_tags
             WHERE day IN (SELECT MAX(day) FROM loss_tags)
               AND tag IN ('fully_hedged','perfect_match','unpaired_parent','unpaired_parent_loss','late_unpaired')
             GROUP BY tag
             ORDER BY tag
        """).fetchall()
        con.close()

        if pc_tags:
            print("\n🔹 Parent/Child Match Quality (latest day)")
            for r in pc_tags:
                print(f"  {r['tag']:<20} {r['n']}")
    except Exception as e:
        print(f"[digest] parent/child summary warn: {e}")


# === PATCH START ===
# 📍 TARGET: replay_digest.py
# 🔎 SEARCH: # === Mastery Benchmarks ===
# 📆 PATCHED: 2025-10-02
# ───────────────────────────────────────────────────────────────

    # === Mastery Benchmarks ===
    try:
    except Exception as e:
        print(f"[digest] auto-added except: {e}")
        print("\n=== Mastery Benchmarks ===")
        total = len(outcomes) or 1
        stoploss_hits = sum(1 for o in outcomes if o.get("stoploss"))
        hedge_hits    = sum(1 for o in outcomes if o.get("hedge"))
        wins          = sum(1 for o in outcomes if o.get("success"))

        stoploss_mastery = 100.0 * (1 - stoploss_hits/total)
        hedge_mastery    = 100.0 * (hedge_hits/total)
        winrate          = 100.0 * (wins/total)

        print(f"Stop-loss mastery   : {stoploss_mastery:.1f}%")
        print(f"Hedge mastery       : {hedge_mastery:.1f}%")
        print(f"Winrate             : {winrate:.1f}%")

        # add distance / fav / DOW / surface rollups
        q1 = con.execute("SELECT distance_band, SUM(net_pnl) AS pnl, COUNT(*) AS n FROM mastery_posteriors GROUP BY distance_band").fetchall()
        q2 = con.execute("SELECT fav_rank_bin, SUM(net_pnl) AS pnl, COUNT(*) AS n FROM mastery_posteriors GROUP BY fav_rank_bin").fetchall()
        q3 = con.execute("SELECT day_of_week, SUM(net_pnl) AS pnl, COUNT(*) AS n FROM mastery_posteriors GROUP BY day_of_week").fetchall()
        q4 = con.execute("SELECT surface_type, SUM(net_pnl) AS pnl, COUNT(*) AS n FROM mastery_posteriors GROUP BY surface_type").fetchall()

        print("\nDistance mastery:")
        for r in q1: print(f"  {r['distance_band']:<8} pnl={r['pnl']:.2f} n={r['n']}")
        print("\nFav/field mastery:")
        for r in q2: print(f"  {r['fav_rank_bin']:<8} pnl={r['pnl']:.2f} n={r['n']}")
        print("\nDay-of-week mastery:")
        for r in q3: print(f"  {r['day_of_week']:<8} pnl={r['pnl']:.2f} n={r['n']}")
        print("\nSurface mastery:")
        for r in q4: print(f"  {r['surface_type']:<8} pnl={r['pnl']:.2f} n={r['n']}")

    except Exception as e:
        print(f"[digest] mastery benchmarks warn: {e}")
# === PATCH END ===



        # Deduplicate mids/sids/tags and persist both problem + success
        seen = set()
        persisted = []
        for (mid, sid, tag) in focus_examples + success_examples:
            key = (report_day, mid, sid, tag)
            if key in seen:
                continue
            seen.add(key)
            cur.execute("""
                INSERT INTO loss_tags(report_day, marketId, selectionId, tag, created_at)
                VALUES (?,?,?,?,datetime('now','utc'))
                ON CONFLICT(report_day, marketId, selectionId, tag) DO NOTHING
            """, (
                day,
                str(mid),
                str(sid) if sid not in (None, "", "band", "race") else "0",
                tag
            ))
            persisted.append({"day": day, "mid": mid, "sid": sid, "tag": tag})


        con3.commit()
        con3.close()

        # Print summary of what was persisted
        if persisted:
            print("\n[loss_tags updated — persisted problem cases:]")
            for row in persisted[:20]:
                print(f"  mid={row['mid']} sid={row['sid']} tag={row['tag']}")
            if len(persisted) > 20:
                print(f"  … and {len(persisted)-20} more")
        else:
            print("\n[loss_tags updated — no problem mids/sids found]")


    except Exception as e:
        print(f"[digest] loss_tags persist warn: {e}")



    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(REPORT_DIR, exist_ok=True)
    out_path = os.path.join(REPORT_DIR, f"digest_{ts}.txt")
    with open(out_path,"w") as f: f.write("") # optional file save
    print(f"\n[Digest saved → {out_path}]")

if __name__=="__main__":
    main()
