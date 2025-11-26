from engines.mastery.mastery_policy import propose_trade
from engines.mastery.priors import seed_simple_prior

def test_pretrade_guards_block_as_expected():
    seed_simple_prior("5-7f|FLAT|30-10|mid|fav")
    bad = {
        "distance_band":"5-7f","code":"FLAT","tto_window":"30-10","class_band":"mid","fav_rank_bin":"fav",
        "sigma_ok": False, "liquidity_ok": True, "two_chapters_ok": True,
        "sigma": 2.0, "stake": 2.0, "depth_total": 50, "matched_per_min": 10,
        "direction":"LAY->BACK"
    }
    plan = propose_trade(bad)
    assert plan["enter"] is False
    assert "sigma" in plan["why"]
