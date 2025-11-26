# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/green_up.py
# 🔎 SEARCH (delete whole file)
# ⛏️ ACTION: replace with this clean version
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import logging
from datetime import datetime, timezone
from engines.live.live_router import (
    _place, _orders_conn, _orders_insert_child_queued,
    _orders_update_child_placed, _orders_update_child_matched,
    _orders_update_hedge_matched, _orders_probe,
    _round_odds, _calc_hedge_stake, _ref
)

log = logging.getLogger("green_up")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def calculate_hedge_stake(init_odds: float, init_stake: float, curr_odds: float) -> float:
    return round((init_odds * init_stake) / max(0.01, curr_odds), 2)

def green_up(app_key: str, session: str,
             market_id: str,
             stakes: dict,
             current_odds: dict,
             fire_ice: dict | None = None,
             profit_target=(10, 20),
             loss_caps=(10, 20, 30, 40)) -> dict:
    """
    stakes: {sid: {'stake': float, 'odds': float}}
    current_odds: {sid: float}
    fire_ice: optional {'fire': sid, 'ice': sid}
    """
    outcomes, total_pnl = {}, 0.0

    # adjust loss caps for fire/ice
    adj_losses = {}
    if fire_ice:
        fire, ice = str(fire_ice.get("fire")), str(fire_ice.get("ice"))
        remaining = iter([20, 30])
        for sid in list(stakes.keys())[:4]:
            if sid == fire: adj_losses[sid] = 10
            elif sid == ice: adj_losses[sid] = 40
            else: adj_losses[sid] = next(remaining)
    else:
        for sid, cap in zip(list(stakes.keys())[:4], loss_caps):
            adj_losses[sid] = cap

    for sid, data in stakes.items():
        init_stake, init_odds = float(data["stake"]), float(data["odds"])
        curr = current_odds.get(sid)
        if not curr: continue
        hedge = calculate_hedge_stake(init_odds, init_stake, curr)
        profit = round(((init_odds - curr) * init_stake) / curr, 2)
        outcomes[sid] = {"hedge": hedge, "profit": profit, "odds_now": curr}
        total_pnl += profit

    log.info(f"[GREENUP] market={market_id} agg_pnl={total_pnl:.2f}")

    if profit_target[0] <= total_pnl <= profit_target[1]:
        log.info("🟢 Target met → hedging evenly")
        for sid, out in outcomes.items():
            _execute(app_key, session, market_id, sid, out["hedge"], out["odds_now"], "BACK")
    else:
        log.warning("🧊 Target not met → fallback caps")
        for sid, out in outcomes.items():
            if not out: continue
            cap = adj_losses.get(sid, 20)
            safe = min(out["hedge"], round(cap / out["odds_now"], 2))
            _execute(app_key, session, market_id, sid, safe, out["odds_now"], "BACK")

    return outcomes

def _execute(app_key, session, mid, sid, stake, odds, side):
    stake = float(stake); odds = _round_odds(float(odds))
    ref = _ref("GREENUP")
    try:
        con = _orders_conn()
        cid = _orders_insert_child_queued(parent_cor=ref, market_id=mid,
                                          selection_id=sid, side=side,
                                          odds=odds, stake=stake, source="GREENUP")
        bf_id, detail = _place(app_key, session, mid, sid, side, odds, stake, ref)
        if bf_id and cid:
            _orders_update_child_placed(cid, bf_id)
            _orders_update_child_matched(ref, ref, side, odds, stake)
            _orders_update_hedge_matched(cor=ref, exit_side=side, exit_odds=odds, exit_stake=stake)
            _orders_probe(ref, note="greenup", bet_id=bf_id)
            log.info(f"✅ sid={sid} hedge={stake} @ {odds}")
        else:
            log.error(f"❌ fail sid={sid} hedge={stake} @ {odds} detail={detail}")
    except Exception as e:
        log.error(f"🛑 exception {mid}/{sid}: {e}")
    finally:
        try: con.close()
        except Exception: pass
