#!/usr/bin/env python3
import sqlite3, os, shutil

# --- Adjust these paths if needed ---
BASE = os.path.dirname(os.path.dirname(__file__))  # project root
DATA = os.path.join(BASE, "data")

LOCAL_DB  = os.path.join(DATA, "autoscalp_gui.db")
LOCAL_BET = os.path.join(DATA, "bets.db")
LOCAL_SET = os.path.join(DATA, "settlements.db")
LOCAL_MAS = os.path.join(DATA, "mastery_v7.db")

LIVE = os.path.join(DATA, "livecache")
CLOUD_AUTO = os.path.join(LIVE, "autoscalp_livecache.db")
CLOUD_BETS = os.path.join(LIVE, "bets_livecache.db")
CLOUD_SETT = os.path.join(LIVE, "settlements_livecache.db")
CLOUD_MAST = os.path.join(LIVE, "mastery_livecache.db")

PAIRINGS = [
    (LOCAL_DB,  CLOUD_AUTO, "AUTO"),
    (LOCAL_BET, CLOUD_BETS, "BETS"),
    (LOCAL_SET, CLOUD_SETT, "SETTLE"),
    (LOCAL_MAS, CLOUD_MAST, "MASTERY"),
]

def schema_of(dbpath):
    con = sqlite3.connect(dbpath)
    con.row_factory = sqlite3.Row
    tables = {}
    for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        t = r["name"]
        cols = con.execute(f"PRAGMA table_info('{t}')").fetchall()
        tables[t] = [c["name"] for c in cols]
    con.close()
    return tables

def rebuild(local_path, live_path, label):
    print(f"\n[{label}] Checking schema…")

    lt = schema_of(local_path)
    ct = schema_of(live_path)

    conL = sqlite3.connect(local_path)
    conC = sqlite3.connect(live_path)
    conC.row_factory = sqlite3.Row

    for table, lcols in lt.items():
        # create missing table
        if table not in ct:
            print(f"  [+] create table {table}")
            coldefs = ", ".join(f'"{c}" TEXT' for c in lcols)
            conC.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({coldefs})')
            conC.commit()
            ct[table] = lcols

        # add missing columns
        missing = set(lcols) - set(ct.get(table, []))
        for col in missing:
            print(f"  [+] add column {table}.{col}")
            conC.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" TEXT')
            conC.commit()

        # copy rows only if table empty
        cur = conC.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()
        if cur["n"] == 0:
            print(f"  [+] initial copy for {table}")
            rows = conL.execute(f'SELECT * FROM "{table}"').fetchall()
            if rows:
                cols = lcols
                collist = ",".join(f'"{c}"' for c in cols)
                ph = ",".join("?" for _ in cols)
                ins = f'INSERT INTO "{table}"({collist}) VALUES({ph})'
                for r in rows:
                    conC.execute(ins, [r[c] for c in cols])
                conC.commit()

    conL.close()
    conC.close()
    print(f"[{label}] OK\n")

def main():
    for lp, cp, label in PAIRINGS:
        rebuild(lp, cp, label)

if __name__ == "__main__":
    main()
