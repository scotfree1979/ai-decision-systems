from __future__ import annotations
from engines.config_paths import connect_db
from engines.mastery.priors import seed_simple_prior
from engines.mastery.policy_lookup import load_posteriors

def test_priors_insert_and_load_posteriors():
    bin_key = "5-7f|FLAT|30-10|mid|fav"
    seed_simple_prior(bin_key)
    with connect_db(ro=True) as conn:
        post = load_posteriors(conn, bin_key)
    assert post["p1"]["alpha"] > 0 and post["p1"]["beta"] > 0
    assert "costs" in post and "stops" in post and "sizing" in post
