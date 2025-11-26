from engines.mastery.mastery_policy import propose_trade
from engines.mastery.priors import seed_simple_prior

def test_policy_selects_positive_ev_clip():
    # Seed a bin and propose a trade in a liquid, low-sigma setup
    bin_key = "5-7f|FLAT|30-10|mid|fav"
    seed_simple_prior(bin_key)
    ctx = {
        "distance_band":"5-7f","code":"FLAT","tto_window":"30-10","class_band":"mid","fav_rank_bin":"fav",
        "sigma_ok": True, "liquidity_ok": True, "two_chapters_ok": True,
        "sigma": 0.6, "stake": 2.0, "depth_total": 600, "matched_per_min": 200,
        "direction":"LAY->BACK"
    }
    plan = propose_trade(ctx)
    assert plan["enter"] is True
    assert plan["target_ticks"] in (1,2,3)
    assert "ev=" in plan["why"]
