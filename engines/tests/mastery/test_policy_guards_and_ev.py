from __future__ import annotations
from engines.mastery.mastery_policy import propose_trade
from engines.mastery.priors import seed_simple_prior, upsert_prior
from engines.config_paths import connect_db

def test_pretrade_guard_blocks_when_sigma_bad():
    seed_simple_prior("5-7f|FLAT|30-10|mid|fav")
    plan = propose_trade({
        "distance_band":"5-7f","code":"FLAT","tto_window":"30-10","class_band":"mid","fav_rank_bin":"fav",
        "sigma_ok": False, "liquidity_ok": True, "two_chapters_ok": True,
    })
    assert plan["enter"] is False and "sigma" in plan["why"]

def test_ev_positive_clip_selected_when_context_strong():
    bin_key = "5-7f|FLAT|30-10|mid|fav"
    seed_simple_prior(bin_key)
    with connect_db(ro=False) as conn:
        upsert_prior(conn, bin_key,
                     (55.0, 20.0), (45.0, 25.0), (35.0, 25.0),
                     (40.0, 10.0), 1.2, 0.5)
        conn.commit()
    plan = propose_trade({
        "distance_band":"5-7f","code":"FLAT","tto_window":"30-10","class_band":"mid","fav_rank_bin":"fav",
        "sigma_ok": True, "liquidity_ok": True, "two_chapters_ok": True,
        "sigma": 0.25, "stake": 2.0, "depth_total": 700, "matched_per_min": 220,
        "direction":"LAY->BACK"
    })
    assert plan["enter"] is True and plan["target_ticks"] in (1,2,3)
