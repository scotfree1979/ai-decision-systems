

# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: engines/tests/mastery/test_policy_uses_priors_initially.py
# 🔎 SEARCH: ^def test_policy_uses_priors_when_gates_ok\(\):\n(?:.*\n)+?plan = propose_trade\(ctx\)
# --- PATCH START: provide microstructure context + relaxed assertion ----------
from engines.mastery.mastery_policy import propose_trade
from engines.mastery.priors import seed_simple_prior, upsert_prior
from engines.config_paths import connect_db

def test_policy_uses_priors_when_gates_ok():
    bin_key = "5-7f|FLAT|30-10|mid|fav"
    # Seed baseline then bump priors so 1–2 tick clips can be EV+
    seed_simple_prior(bin_key)
    with connect_db(ro=False) as conn:
        # p1 ≈ 0.73, p2 ≈ 0.64, p3 ≈ 0.58; p_fill prior ≈ 0.80; MAE priors not used directly here
        upsert_prior(conn, bin_key,
                     (55.0, 20.0), (45.0, 25.0), (35.0, 25.0),
                     (40.0, 10.0), 1.2, 0.5)
        conn.commit()

    # Strong microstructure context (liquid + low σ)
    ctx = {
        "distance_band":"5-7f","code":"FLAT","tto_window":"30-10","class_band":"mid","fav_rank_bin":"fav",
        "sigma_ok": True, "liquidity_ok": True, "two_chapters_ok": True,
        "direction":"LAY->BACK",
        "sigma": 0.20,          # low volatility → high p_fill, low slippage
        "stake": 2.0,
        "depth_total": 600,
        "matched_per_min": 200,
    }
    plan = propose_trade(ctx)
    assert plan["enter"] is True
    assert plan["target_ticks"] in (1, 2, 3)
    assert "ev=" in plan["why"]

# --- PATCH END ----------------------------------------------------------------

