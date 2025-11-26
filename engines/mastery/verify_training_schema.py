#!/usr/bin/env python3
"""
verify_training_schema.py — schema-to-DB validation utility
─────────────────────────────────────────────────────────────
Scans mastery_training_schema_v7.json and checks each metric
for table and column validity in data/autoscalp_gui.db.

Usage:
    python3 -m engines.mastery.verify_training_schema
"""
import os, sys, sqlite3, json, re
from engines.config_paths import autoscalp_db

def main():
    db = autoscalp_db()
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    with open("data/configs/mastery_training_schema_v7.json", "r", encoding="utf-8") as f:
        schema = json.load(f)

    # Gather DB metadata
    tables = {r[0]: set() for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in list(tables):
        try:
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
            tables[t] = set(cols)
        except Exception:
            continue

    def extract_cols(expr):
        return re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr)

    print(f"\n[verify] Checking schema metrics against {db}\n")
    report = []
    for bucket in schema.get("buckets", []):
        for q in bucket.get("questions", []):
            src = q.get("source")
            metric = q.get("metric")
            cols = extract_cols(metric)
            tblcols = tables.get(src)
            if tblcols is None:
                status = f"❌ missing table: {src}"
            else:
                missing = [c for c in cols if c not in tblcols]
                if missing:
                    status = f"⚠️ missing cols: {', '.join(missing)}"
                else:
                    status = "✅ ok"
            report.append((bucket["bucket"], q["id"], src, metric, status))

    # Print summary
    for b, qid, src, metric, status in report:
        print(f"[{b:<25}] {qid:<6} {src:<25} → {status}")

    con.close()
    print("\n[verify] ✅ complete")

if __name__ == "__main__":
    main()
