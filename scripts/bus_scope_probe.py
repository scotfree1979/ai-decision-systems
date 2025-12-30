#!/usr/bin/env python3
"""
BUS SCOPE INTEGRITY PROBE
------------------------
Standalone, read-only.
No placement, no engines, no router.
"""

from engines.decision_engine.decide_once.scope import build_and_maintain_scope
from engines.market_monitor.monitor import get_market_state
from engines.config_paths import open_auto_db
from datetime import datetime, timezone
import sqlite3

NOW = datetime.now(timezone.utc)

def is_market_finished(mid: str) -> bool:
    con = open_auto_db(rw=False)
    con.row_factory = sqlite3.Row
    try:
        r = con.execute("""
            SELECT off_at_utc
            FROM markets_schedule
            WHERE marketId=?
            LIMIT 1
        """, (mid,)).fetchone()
        if not r:
            return True
        off = datetime.fromisoformat(str(r["off_at_utc"]).replace("Z","+00:00"))
        # 15 min in-play grace
        return (NOW - off).total_seconds() > (15 * 60)
    finally:
        con.close()

def main():
    scope = build_and_maintain_scope()
    markets = scope.get("markets_dict", [])

    allowed = []
    ignored = []

    for m in markets:
        mid = m["marketId"]

        if is_market_finished(mid):
            ignored.append((mid, "finished"))
            continue

        st = get_market_state(mid) or {}
        runners = st.get("runners", {}) or {}

        for sid, r in runners.items():
            band = r.get("band")
            if band in ("ACTIVE", "PASSIVE"):
                allowed.append((mid, sid, band))
            else:
                ignored.append((mid, sid, band))

    print("\n=== BUS EXECUTION SET ===")
    for a in allowed:
        print("ALLOW", a)

    print("\n=== BUS IGNORED SET ===")
    for i in ignored:
        print("IGNORE", i)

    print(f"\nSUMMARY: allow={len(allowed)} ignore={len(ignored)}")

if __name__ == "__main__":
    main()
