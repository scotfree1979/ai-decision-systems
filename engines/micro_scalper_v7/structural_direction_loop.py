import threading
import time
from datetime import datetime, timezone
from collections import defaultdict

from engines.market_monitor.monitor import get_market_state
from engines.config_paths import open_auto_db, open_bets_db

_LOOP_THREAD = None
_ACTIVE = False

# persistent in-memory anchors
_ANCHORS = {}          # (mid, sid) -> anchor_px
_LAST_DIRECTION = {}   # (mid, sid) -> direction


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _load_anchor_from_bets(mid, sid):
    con = open_bets_db(rw=False)
    try:
        row = con.execute("""
            SELECT anchor_odd
            FROM bets
            WHERE marketId=?
              AND selectionId=?
            LIMIT 1
        """, (mid, sid)).fetchone()
        if row and row[0]:
            return float(row[0])
    finally:
        con.close()
    return None


def _persist_row(mid, sid, anchor, px, delta, band, fav_rank, direction):
    con = open_auto_db(rw=True)
    try:
        con.execute("""
            INSERT INTO structural_graph(
                ts, marketId, selectionId,
                anchor_odd, current_px,
                delta, band, fav_rank, direction
            )
            VALUES(?,?,?,?,?,?,?,?,?)
        """, (
            _now(), mid, sid,
            anchor, px,
            delta, band, fav_rank, direction
        ))
        con.commit()
    finally:
        con.close()


def _structural_cycle():

    drift_rank = []
    steam_rank = []

    # iterate all monitored markets
    from engines.market_monitor.monitor import _STATE

    for mid, state in _STATE.items():
        runners = state.get("runners", {})

        # compute rank ordering
        sorted_runners = sorted(
            runners.items(),
            key=lambda x: (x[1].get("px") is None, x[1].get("px", 9999))
        )

        for rank, (sid, data) in enumerate(sorted_runners):

            px = data.get("px")
            if px is None:
                continue

            key = (mid, sid)

            anchor = _ANCHORS.get(key)

            if anchor is None:
                # load from DB if exists
                anchor = _load_anchor_from_bets(mid, sid)
                if anchor is None:
                    anchor = float(px)
                _ANCHORS[key] = anchor

            delta = float(px) - float(anchor)

            # raw direction
            if delta > 0:
                direction = "LAY->BACK"
                drift_rank.append((abs(delta), mid, sid))
            elif delta < 0:
                direction = "BACK->LAY"
                steam_rank.append((abs(delta), mid, sid))
            else:
                direction = None

            # boundary logic
            band = data.get("band")
            if band == "IGNORED":
                direction = None

            # only flip if boundary crossed
            last = _LAST_DIRECTION.get(key)
            if last and direction and last != direction:
                # require structural confirmation via rank shift
                if rank == 0:
                    # favourite shift confirms flip
                    pass
                else:
                    direction = None

            _LAST_DIRECTION[key] = direction

            _persist_row(
                mid, sid,
                anchor, px,
                delta, band,
                rank + 1,
                direction
            )

    # print top 5 each
    drift_rank.sort(reverse=True)
    steam_rank.sort(reverse=True)

    print("\n===== STRUCTURAL DIRECTION LOOP =====")

    print("\nTOP DRIFT (LAY->BACK)")
    for _, mid, sid in drift_rank[:5]:
        print(mid, sid)

    print("\nTOP STEAM (BACK->LAY)")
    for _, mid, sid in steam_rank[:5]:
        print(mid, sid)

    print("======================================\n")


def _loop(interval_s=5):

    global _ACTIVE

    while _ACTIVE:
        try:
            _structural_cycle()
        except Exception as e:
            print("[STRUCTURAL LOOP ERROR]", e)

        time.sleep(interval_s)


def start_structural_direction_loop(interval_s=5):

    global _LOOP_THREAD, _ACTIVE

    if _LOOP_THREAD and _LOOP_THREAD.is_alive():
        return

    _ACTIVE = True

    t = threading.Thread(
        target=_loop,
        args=(interval_s,),
        daemon=True,
        name="StructuralDirectionLoop"
    )
    t.start()

    _LOOP_THREAD = t

    print("[STRUCTURAL] direction loop started")