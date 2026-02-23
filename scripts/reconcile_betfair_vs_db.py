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
# PARENT ↔ CHILD INTEGRITY (DB-ONLY, SCHEMA-PURE)
# -------------------------------------------------------------------

def parent_child_integrity_report(db: pd.DataFrame):
    """
    DB-authoritative parent ↔ child integrity report.

    Schema truth only:
    - role        → PARENT / CHILD
    - hedge_of    → linkage
    - no odds
    - no Betfair
    - no inference
    """

    print("\n================ PARENT ↔ CHILD INTEGRITY =================\n")

    parents = db[db["role"] == "PARENT"]
    children = db[db["role"] == "CHILD"]

    print(f"Total parents (today):  {len(parents)}")
    print(f"Total children (today): {len(children)}\n")

    # --------------------------------------------------
    # Parents with NO children
    # --------------------------------------------------
    parents_no_child = parents[
        ~parents["id"].isin(children["hedge_of"].dropna())
    ]

    print("=== PARENTS WITH NO CHILD ===")
    print(f"count: {len(parents_no_child)}")

    if len(parents_no_child):
        print(
            parents_no_child[
                [
                    "engine",
                    "marketId",
                    "selectionId",
                    "entry_status",
                    "exit_status",
                    "opened_at",
                ]
            ]
            .sort_values("opened_at")
            .head(25)
            .to_string(index=False)
        )
    else:
        print("None 🎉")

    print()

    # --------------------------------------------------
    # Parents with MULTIPLE children
    # --------------------------------------------------
    multi_child = (
        children
        .dropna(subset=["hedge_of"])
        .groupby("hedge_of")
        .size()
        .reset_index(name="child_count")
    )

    multi_child = multi_child[multi_child["child_count"] > 1]

    print("=== PARENTS WITH MULTIPLE CHILDREN ===")
    print(f"count: {len(multi_child)}")

    if len(multi_child):
        print(
            multi_child
            .merge(
                parents,
                left_on="hedge_of",
                right_on="id",
                how="left",
            )[
                [
                    "engine",
                    "marketId",
                    "selectionId",
                    "child_count",
                    "opened_at",
                ]
            ]
            .sort_values("child_count", ascending=False)
            .head(25)
            .to_string(index=False)
        )
    else:
        print("None 🎉")

    print()

    # --------------------------------------------------
    # Orphaned children (CHILD rows with no parent)
    # --------------------------------------------------
    orphan_children = children[
        children["hedge_of"].notna()
        & ~children["hedge_of"].isin(parents["id"])
    ]

    print("=== ORPHANED CHILDREN (SHOULD BE ZERO) ===")
    print(f"count: {len(orphan_children)}")

    if len(orphan_children):
        print(
            orphan_children[
                [
                    "engine",
                    "marketId",
                    "selectionId",
                    "hedge_of",
                    "entry_status",
                    "exit_status",
                    "opened_at",
                ]
            ]
            .sort_values("opened_at")
            .head(25)
            .to_string(index=False)
        )
    else:
        print("None 🎉")

    print()

    # --------------------------------------------------
    # Engine-level summary
    # --------------------------------------------------
    parent_ids_with_child = set(children["hedge_of"].dropna())

    engine_summary = (
        parents
        .assign(has_child=parents["id"].isin(parent_ids_with_child))
        .groupby(["engine", "has_child"])
        .size()
        .unstack(fill_value=0)
        .rename(columns={
            False: "parents_without_child",
            True:  "parents_with_child",
        })
    )

    print("=== ENGINE SUMMARY (PARENT ↔ CHILD) ===")
    print(engine_summary.to_string())

    print("\n===========================================================\n")


# -------------------------------------------------------------------
# MAIN (REWRITTEN – SCHEMA-PURE)
# -------------------------------------------------------------------

def main():
    print("\n=== Daily Betfair ↔ AutoScalp Reconciliation ===\n")

    bf = load_betfair_csvs()
    bf_by_id = bf.set_index("bet_id")
    db = load_orders_db()

    # ------------------------------------------------
    # DB-only integrity (authoritative)
    # ------------------------------------------------
    parent_child_integrity_report(db)

    # ------------------------------------------------
    # Build execution cycles (structure only)
    # ------------------------------------------------
    print("\n================ CYCLE TABLE ====================")

    cycles = build_cycle_table(db)

    print(f"Total parent cycles: {len(cycles)}")
    print(cycles.groupby(["engine", "cycle_complete"]).size())

    # ------------------------------------------------
    # Attach Betfair odds (by bet_id)
    # ------------------------------------------------
    cycles["parent_bf_odds"] = cycles["parent_bet_id"].map(
        lambda x: bf_by_id.loc[x]["odds"] if x in bf_by_id.index else None
    )

    cycles["child_bf_odds"] = cycles["child_bet_id"].map(
        lambda x: bf_by_id.loc[x]["odds"] if x in bf_by_id.index else None
    )

    # ------------------------------------------------
    # Join DB rows for parent and child (schema-pure)
    # ------------------------------------------------
    parents_db = db[
        ["id", "role", "side", "entry_odds"]
    ].rename(columns={
        "id": "parent_id",
        "side": "parent_side",
        "entry_odds": "parent_entry_odds",
    })

    children_db = db[
        ["id", "role", "side", "entry_odds"]
    ].rename(columns={
        "id": "child_id",
        "side": "child_entry_side",
        "entry_odds": "child_entry_odds",
    })

    truth = (
        cycles
        .merge(parents_db, on="parent_id", how="left")
        .merge(children_db, on="child_id", how="left")
    )

    # ------------------------------------------------
    # Presence on Betfair
    # ------------------------------------------------
    truth["parent_seen_bf"] = truth["parent_bet_id"].isin(bf_by_id.index)
    truth["child_seen_bf"]  = truth["child_bet_id"].isin(bf_by_id.index)

    # ------------------------------------------------
    # Direction check (schema-pure)
    # ------------------------------------------------
    def direction_ok(row):
        try:
            ps = row["parent_side"]
            cs = row["child_entry_side"]
            po = float(row["parent_entry_odds"])
            co = float(row["child_entry_odds"])

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
        if not row["parent_seen_bf"]:
            return "DB_ONLY_PARENT"
        if pd.isna(row["child_id"]):
            return "NO_CHILD_DB"
        if not row["child_seen_bf"]:
            return "CHILD_NOT_SENT"
        if row["direction_ok"] is False:
            return "DIRECTION_VIOLATION"
        return "OK"

    truth["cycle_status"] = truth.apply(classify, axis=1)

    # ------------------------------------------------
    # Summary
    # ------------------------------------------------
    print("\n=== CYCLE STATUS SUMMARY ===")
    print(truth.groupby(["engine", "cycle_status"]).size())



    # ------------------------------------------------
    # PRICE INTEGRITY VIEW (schema-pure)
    # ------------------------------------------------
    print("\n================ PRICE INTEGRITY (DB ↔ BETFAIR) =================")

    price_view = truth[
        [
            "engine",
            "marketId",
            "selectionId",
            "parent_id",
            "child_id",
            "parent_side",
            "child_entry_side",
            "parent_entry_odds",
            "parent_bf_odds",
            "child_entry_odds",
            "child_bf_odds",
            "cycle_status",
        ]
    ].assign(
        parent_odds_diff=lambda d: (d["parent_entry_odds"] - d["parent_bf_odds"]).round(3),
        child_odds_diff=lambda d: (d["child_entry_odds"] - d["child_bf_odds"]).round(3),
    )

    print(price_view.head(50).to_string(index=False))

    # ------------------------------------------------
    # RISK CYCLE REALITY CHECK (schema-pure)
    # ------------------------------------------------
    print("\n================ RISK CYCLE REALITY =================")

    shadow_cycles = db[
        (db["role"] == "PARENT")
        & (db["entry_status"] == "MATCHED")
        & (db["engine"].isin(["LEGACY", "MSC_EXPLORATORY"]))
    ][["engine", "marketId", "selectionId", "entry_odds"]]

    anchors = (
        shadow_cycles
        .groupby(["marketId", "selectionId"])
        .agg(
            cycles=("entry_odds", "count"),
            distinct_anchor_px=("entry_odds", pd.Series.nunique),
        )
        .reset_index()
    )

    price_moves = (
        db.groupby(["marketId", "selectionId"])
        .agg(
            min_px=("entry_odds", "min"),
            max_px=("entry_odds", "max"),
        )
        .assign(price_range=lambda d: (d["max_px"] - d["min_px"]).round(3))
        .reset_index()
    )

    risk_fires = (
        db[
            (db["role"] == "PARENT")
            & (db["entry_status"] == "MATCHED")
            & (db["engine"] == "MSC_RISK")
        ]
        .groupby(["marketId", "selectionId"])
        .size()
        .reset_index(name="risk_parents")
    )

    risk_view = (
        anchors
        .merge(price_moves, on=["marketId", "selectionId"], how="left")
        .merge(risk_fires, on=["marketId", "selectionId"], how="left")
        .fillna({"risk_parents": 0})
        .assign(missing_risk=lambda d: d["cycles"] - d["risk_parents"])
        .sort_values("missing_risk", ascending=False)
    )

    print(risk_view.head(50).to_string(index=False))

    # ------------------------------------------------
    # EXIT STATUS COUNTS
    # ------------------------------------------------
    print("\n================ EXIT STATUS SUMMARY =================")

    print(
        db["exit_status"]
        .fillna("OPEN")
        .value_counts()
        .rename_axis("exit_status")
        .reset_index(name="count")
        .to_string(index=False)
    )

    print("\n===============================================================")

    # ===============================================================
    # 🔍 SR1 EXTENDED DIAGNOSTICS BLOCK (READ-ONLY)
    # ===============================================================

    print("\n================ EXTENDED SR1 DIAGNOSTICS =================\n")

    # ---------------------------------------------------------------
    # 1️⃣ PARENTS BY ENGINE / STATUS
    # ---------------------------------------------------------------
    print("\n=== TODAY PARENTS BY ENGINE / ENTRY+EXIT STATUS ===")

    q1 = """
    SELECT engine, entry_status, exit_status, COUNT(*)
    FROM orders
    WHERE role='PARENT'
      AND date(opened_at)=date('now','utc')
    GROUP BY engine, entry_status, exit_status
    ORDER BY engine;
    """
    for r in sqlite3.connect(DB_PATH).execute(q1):
        print(r)


    # ---------------------------------------------------------------
    # 2️⃣ PARENT ↔ CHILD EXISTENCE RATE
    # ---------------------------------------------------------------
    print("\n=== PARENTS vs CHILD EXISTENCE (ENGINE LEVEL) ===")

    q2 = """
    SELECT
        p.engine,
        COUNT(*) AS parents,
        SUM(CASE WHEN c.id IS NULL THEN 1 ELSE 0 END) AS no_child
    FROM orders p
    LEFT JOIN orders c
        ON c.hedge_of = p.id
    WHERE p.role='PARENT'
      AND date(p.opened_at)=date('now','utc')
    GROUP BY p.engine;
    """
    for r in sqlite3.connect(DB_PATH).execute(q2):
        print(r)


    # ---------------------------------------------------------------
    # 3️⃣ MATCHED REQUIRED_EXPOSURE BY ENGINE
    # ---------------------------------------------------------------
    print("\n=== MATCHED REQUIRED_EXPOSURE BY ENGINE ===")

    q3 = """
    SELECT
        engine,
        SUM(required_exposure),
        COUNT(*)
    FROM orders
    WHERE role='PARENT'
      AND entry_status='MATCHED'
      AND date(opened_at)=date('now','utc')
    GROUP BY engine;
    """
    for r in sqlite3.connect(DB_PATH).execute(q3):
        print(r)


    # ---------------------------------------------------------------
    # 4️⃣ FAILED PARENTS STILL HOLDING EXPOSURE
    # ---------------------------------------------------------------
    print("\n=== FAILED PARENTS EXPOSURE (SHOULD BE ZERO) ===")

    q4 = """
    SELECT
        engine,
        SUM(required_exposure),
        COUNT(*)
    FROM orders
    WHERE role='PARENT'
      AND exit_status='FAILED'
      AND date(opened_at)=date('now','utc')
    GROUP BY engine;
    """
    for r in sqlite3.connect(DB_PATH).execute(q4):
        print(r)


    # ---------------------------------------------------------------
    # 5️⃣ MATCHED SIDE DISTRIBUTION (STEAM vs DRIFT)
    # ---------------------------------------------------------------
    print("\n=== MATCHED PARENTS SIDE DISTRIBUTION ===")

    q5 = """
    SELECT
        side,
        COUNT(*)
    FROM orders
    WHERE role='PARENT'
      AND entry_status='MATCHED'
      AND date(opened_at)=date('now','utc')
    GROUP BY side;
    """
    for r in sqlite3.connect(DB_PATH).execute(q5):
        print(r)


    # ---------------------------------------------------------------
    # 6️⃣ SCHEMA CHECK – v7_oc_drift_unfolded
    # ---------------------------------------------------------------
    print("\n=== v7_oc_drift_unfolded SCHEMA ===")

    q6 = "PRAGMA table_info(v7_oc_drift_unfolded)"
    for r in sqlite3.connect(DB_PATH).execute(q6):
        print(r)


    # ---------------------------------------------------------------
    # 7️⃣ NEXT QUALIFYING MARKETS (>=7 RUNNERS)
    # ---------------------------------------------------------------
    print("\n=== NEXT 10 QUALIFYING MARKETS (>=7 RUNNERS) ===")

    try:
        con_bets = sqlite3.connect("data/bets.db")
        q7 = """
        SELECT marketId, marketStartTime, COUNT(*)
        FROM bets
        WHERE date(marketStartTime)=date('now','utc')
        GROUP BY marketId
        HAVING COUNT(*) >= 7
        ORDER BY marketStartTime
        LIMIT 10;
        """
        for r in con_bets.execute(q7):
            print(r)
        con_bets.close()
    except Exception as e:
        print("bets.db check failed:", e)

    print("\n===============================================================")


if __name__ == "__main__":
    main()
