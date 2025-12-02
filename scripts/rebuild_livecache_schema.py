#!/usr/bin/env python3
import sqlite3, os, sys

# --------------------------------------------------------------
# ABSOLUTE PROJECT ROOT (no guessing)
# --------------------------------------------------------------
ROOT = os.path.dirname(os.path.abspath(__file__))        # /Users/.../analytics_beta_dev
DATA = os.path.join(ROOT, "data")                        # /data
LIVE = os.path.join(DATA, "livecache")                   # /data/livecache

# --------------------------------------------------------------
# LOCAL DBs
# --------------------------------------------------------------
LOCAL_AUTO = os.path.join(DATA, "autoscalp_gui.db")
LOCAL_BETS = os.path.join(DATA, "bets.db")
LOCAL_SETT = os.path.join(DATA, "settlements.db")
LOCAL_MAST = os.path.join(DATA, "mastery_v7.db")

# --------------------------------------------------------------
# LIVECACHE DBs
# --------------------------------------------------------------
CLOUD_AUTO = os.path.join(LIVE, "autoscalp_livecache.db")
CLOUD_BETS = os.path.join(LIVE, "bets_livecache.db")
CLOUD_SETT = os.path.join(LIVE, "settlements_livecache.db")
CLOUD_MAST = os.path.join(LIVE, "mastery_livecache.db")

PAIRINGS = [
    (LOCAL_AUTO, CLOUD_AUTO, "AUTO"),
    (LOCAL_BETS, CLOUD_BETS, "BETS"),
    (LOCAL_SETT, CLOUD_SETT, "SETTLE"),
    (LOCAL_MAST, CLOUD_MAST, "MASTERY"),
]

# Make sure livecache exists
os.makedirs(LIVE, exist_ok=True)

def schema_of(path):
    if not os.path.exists(path):
        print(f"[WARN] Missing DB: {path}")
        return {}

    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row

    tables = {}
    for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        t = r["name"]
        cols = con.execute(f"PRAGMA table_info('{t}')").fetchall()
        tables[t] = [c["name"] for c in cols]

    con.close()
    return tables

def rebuild(local_path, live_path, label):
    print(f"\n[{label}] Rebuilding schema (EMPTY target)…")
    os.makedirs(os.path.dirname(live_path), exist_ok=True)

    # recreate the file fresh (wipe all data)
    try:
        if os.path.exists(live_path):
            os.remove(live_path)
        open(live_path, "a").close()
    except Exception as e:
        print(f"[{label}] ERROR wiping {live_path}: {e}")
        return

    lt = schema_of(local_path)
    if not lt:
        print(f"[{label}] SKIP — cannot read local schema ({local_path})")
        return

    conC = sqlite3.connect(live_path)
    conC.row_factory = sqlite3.Row

    # build schema only (NO DATA)
    for table, lcols in lt.items():

        # skip system tables
        if table.lower().startswith("sqlite_"):
            print(f"  [SKIP] internal table {table}")
            continue

        # create empty table
        print(f"  [+] CREATE TABLE {table}")
        coldefs = ", ".join(f'"{c}" TEXT' for c in lcols)

        try:
            conC.execute(f'CREATE TABLE "{table}" ({coldefs})')
        except sqlite3.OperationalError as e:
            print(f"    [WARN] {e}")
            continue

        conC.commit()

    conC.close()
    print(f"[{label}] DONE — schema created, NO ROWS copied.")


# --------------------------------------------------------------
# ENTRY
# --------------------------------------------------------------
if __name__ == "__main__":
    print(f"[ROOT] {ROOT}")
    print(f"[DATA] {DATA}")
    print(f"[LIVE] {LIVE}")

    for lp, cp, label in PAIRINGS:
        rebuild(lp, cp, label)

    print("\n✔ LiveCache schema rebuild complete.\n")
