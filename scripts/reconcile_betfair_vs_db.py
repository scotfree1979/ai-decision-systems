#!/usr/bin/env python3
"""
Daily Betfair ↔ AutoScalp reconciliation

READ-ONLY diagnostic tool.
Safe to run during or after trading.
"""

import sqlite3
import pandas as pd
import re
from pathlib import Path
from datetime import datetime, timezone
import tkinter as tk
from tkinter import filedialog


# -------------------------------------------------------------------
# REGEX — canonical Betfair Bet ID extractor
# -------------------------------------------------------------------

BET_ID_RE = re.compile(r"Betfair Bet ID\s+([0-9:]+)")


# -------------------------------------------------------------------
# CONFIG
# -------------------------------------------------------------------

DB_PATH = "data/autoscalp_gui.db"
TODAY_UTC = datetime.now(timezone.utc).strftime("%Y-%m-%d")


# -------------------------------------------------------------------
# HELPERS
# -------------------------------------------------------------------

def pick_csv(title: str) -> Path | None:
    root = tk.Tk()
    root.withdraw()
    file = filedialog.askopenfilename(
        title=title,
        filetypes=[("CSV files", "*.csv")],
    )
    return Path(file) if file else None


def parse_odds(x):
    if pd.isna(x):
        return None
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)", str(x))
    return float(m.group(1)) if m else None


def extract_bet_id(desc: str) -> str | None:
    """
    Extract Betfair Bet ID from Description and NORMALISE it.
    '1:416865971570' -> '416865971570'
    """
    if not isinstance(desc, str):
        return None

    m = BET_ID_RE.search(desc)
    if not m:
        return None

    raw = m.group(1)
    return raw.split(":", 1)[1] if ":" in raw else raw


# -------------------------------------------------------------------
# BETFAIR LOADER
# -------------------------------------------------------------------

def load_betfair_csvs() -> pd.DataFrame:
    dfs = []

    while True:
        path = pick_csv("Select Betfair CSV export")
        if not path:
            break

        df = pd.read_csv(path)
        df["__source_file"] = path.name
        dfs.append(df)

        more = input("Add another Betfair file? [y/N]: ").strip().lower()
        if more != "y":
            break

    if not dfs:
        raise RuntimeError("No Betfair files selected")

    bf = pd.concat(dfs, ignore_index=True)

    bf = bf.rename(columns={
        "Odds": "odds_raw",
        "Stake (£)": "stake",
        "Type": "side",
        "Description": "description",
        "Profit/Loss": "pnl",
        "Status": "status",
    })

    bf["side"] = bf["side"].astype(str).str.upper()
    bf["stake"] = pd.to_numeric(bf["stake"], errors="coerce")
    bf["odds"] = bf["odds_raw"].apply(parse_odds)
    bf["bf_state"] = bf["status"].astype(str).str.upper()

    bf["bet_id"] = bf["description"].apply(extract_bet_id)

    bf = bf.dropna(subset=["stake", "odds", "side", "bet_id"])

    return bf


# -------------------------------------------------------------------
# DB LOADER (FIXED: includes hedge_of)
# -------------------------------------------------------------------

def load_orders_db() -> pd.DataFrame:
    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        """
        SELECT
            id,
            hedge_of,
            customerOrderRef,
            bf_bet_id,
            entry_bet_id,
            exit_bet_id,
            child_bf_bet_id,
            marketId,
            selectionId,
            side,
            entry_odds,
            entry_stake,
            entry_status,
            exit_status,
            realized_pnl,
            engine,
            role,
            opened_at,
            closed_at
        FROM orders
        WHERE date(COALESCE(opened_at, closed_at)) = date('now','utc')
        """,
        con
    )
    con.close()

    df["bet_id"] = (
        df["entry_bet_id"]
        .fillna(df["exit_bet_id"])
        .fillna(df["bf_bet_id"])
        .fillna(df["child_bf_bet_id"])
    )

    df["bet_id"] = df["bet_id"].astype(str)

    return df


# -------------------------------------------------------------------
# CYCLE TABLE
# -------------------------------------------------------------------

def build_cycle_table(db: pd.DataFrame) -> pd.DataFrame:
    """
    Build parent → child execution cycles from DB truth.
    One row per parent.
    """

    parents = db[
        (db["role"] == "PARENT") &
        (db["engine"].notna()) &
        (db["entry_status"] == "MATCHED")
    ].copy()

    children = db[db["role"] == "CHILD"].copy()

    children_by_parent = (
        children
        .sort_values("opened_at")
        .groupby("hedge_of", as_index=False)
        .first()
    )

    cycles = parents.merge(
        children_by_parent,
        how="left",
        left_on="id",
        right_on="hedge_of",
        suffixes=("_parent", "_child")
    )

    cycles["parent_time"] = pd.to_datetime(cycles["opened_at_parent"], errors="coerce")
    cycles["child_time"]  = pd.to_datetime(cycles["opened_at_child"], errors="coerce")

    cycles["cycle_complete"] = cycles["id_child"].notna()

    cycles["delay_seconds"] = (
        cycles["child_time"] - cycles["parent_time"]
    ).dt.total_seconds()

    return cycles[
        [
            "engine_parent",
            "marketId_parent",
            "selectionId_parent",
            "id_parent",
            "bet_id_parent",
            "parent_time",
            "id_child",
            "bet_id_child",
            "child_time",
            "cycle_complete",
            "delay_seconds",
        ]
    ].rename(columns={
        "engine_parent": "engine",
        "marketId_parent": "marketId",
        "selectionId_parent": "selectionId",
        "id_parent": "parent_id",
        "bet_id_parent": "parent_bet_id",
        "id_child": "child_id",
        "bet_id_child": "child_bet_id",
    })


# -------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------

def main():
    print("\n=== Daily Betfair ↔ AutoScalp Reconciliation ===\n")

    bf = load_betfair_csvs()
    db = load_orders_db()

    print("\n================ CYCLE TABLE ====================")

    cycles = build_cycle_table(db)

    print(f"Total parent cycles: {len(cycles)}")
    print(cycles.groupby(["engine", "cycle_complete"]).size())

    print("\n=== INCOMPLETE CYCLES (no child) ===")
    print(
        cycles[~cycles["cycle_complete"]][
            ["engine", "marketId", "selectionId", "parent_time"]
        ].head(20)
    )

    print("\n=== DELAY STATS (seconds) ===")
    print(
        cycles[cycles["cycle_complete"]]
        .groupby("engine")["delay_seconds"]
        .describe()
    )

    # ================================================================
    # PARENT ↔ CHILD ↔ BETFAIR TRUTH TABLE
    # ================================================================

    print("\n================ EXECUTION TRUTH TABLE =================")

    # ------------------------------------------------
    # Build Betfair lookup by bet_id
    # ------------------------------------------------
    bf_by_id = bf.set_index("bet_id")

    def bf_exists(bet_id):
        if not isinstance(bet_id, str):
            return False
        return bet_id in bf_by_id.index

    # ------------------------------------------------
    # Enrich cycles with DB + Betfair truth
    # ------------------------------------------------
    truth = cycles.merge(
        db[
            [
                "id",
                "side",
                "entry_odds",
     
                "role",
            ]
        ],
        left_on="parent_id",
        right_on="id",
        how="left",
    ).rename(columns={
        "side": "parent_side",
        "entry_odds": "parent_odds",
    })

    truth = truth.merge(
        db[
            [
                "id",
                "side",
                "entry_odds",
            ]
        ],
        left_on="child_id",
        right_on="id",
        how="left",
        suffixes=("", "_child"),
    ).rename(columns={
        "side_child": "child_side",
        "entry_odds_child": "child_odds",
    })

    truth["bf_parent"] = truth["parent_bet_id"].apply(bf_exists)
    truth["bf_child"]  = truth["child_bet_id"].apply(bf_exists)
  

    # ------------------------------------------------
    # Direction rule check
    # ------------------------------------------------
    def direction_ok(row):
        try:
            ps = row["parent_side"]
            cs = row["child_side"]
            po = float(row["parent_odds"])
            co = float(row["child_odds"])

            if ps == "LAY" and cs == "BACK":
                return co > po
            if ps == "BACK" and cs == "LAY":
                return co < po
            return None
        except Exception:
            return None

    truth["direction_ok"] = truth.apply(direction_ok, axis=1)

    # ------------------------------------------------
    # Cycle classification
    # ------------------------------------------------
    def classify(row):
        if not row["bf_parent"]:
            return "DB_ONLY_PARENT"
        if pd.isna(row["child_id"]):
            return "NO_CHILD_DB"
        if not row["bf_child"]:
            return "CHILD_NOT_SENT"
        if row["direction_ok"] is False:
            return "DIRECTION_VIOLATION"
        return "OK"

    truth["cycle_status"] = truth.apply(classify, axis=1)

    problems = truth[truth["cycle_status"] != "OK"]

    # ------------------------------------------------
    # Summary
    # ------------------------------------------------
    print("\n=== CYCLE STATUS SUMMARY ===")
    print(truth.groupby(["engine", "cycle_status"]).size())

    # ------------------------------------------------
    # Detailed problem rows (Betfair-truth based)
    # ------------------------------------------------


    print("\n=== PROBLEM CYCLES (DB ↔ BETFAIR DIVERGENCE) ===")

    if problems.empty:
        print("None 🎉")
    else:
        rows = []

        for _, r in problems.iterrows():
            pbid = r["parent_bet_id"]
            cbid = r["child_bet_id"]

            # Betfair truth
            pbf = bf_by_id.loc[pbid] if pbid in bf_by_id.index else None
            cbf = bf_by_id.loc[cbid] if cbid in bf_by_id.index else None

            rows.append({
                "engine": r["engine"],
                "marketId": r["marketId"],
                "selectionId": r["selectionId"],

                "parent_bet_id": pbid,
                "parent_side_bf": pbf["side"] if pbf is not None else None,
                "parent_odds_bf": pbf["odds"] if pbf is not None else None,
                "parent_seen_bf": pbf is not None,

                "child_bet_id": cbid,
                "child_side_bf": cbf["side"] if cbf is not None else None,
                "child_odds_bf": cbf["odds"] if cbf is not None else None,
                "child_seen_bf": cbf is not None,

                "cycle_status": r["cycle_status"],
            })

        prob_df = pd.DataFrame(rows)

        print(prob_df.head(50))


    print("\n================================================")


if __name__ == "__main__":
    main()
