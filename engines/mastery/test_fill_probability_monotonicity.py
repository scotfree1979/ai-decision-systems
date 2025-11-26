from engines.mastery.microstructure import compute_p_fill

def test_p_fill_monotone_depth():
    ctx_low  = {"depth_total": 50, "stake": 2.0, "matched_per_min": 50, "sigma": 0.8}
    ctx_high = {"depth_total": 500, "stake": 2.0, "matched_per_min": 50, "sigma": 0.8}
    post = {"p_fill": {"alpha": 30.0, "beta": 20.0}}
    assert compute_p_fill(ctx_high, post) > compute_p_fill(ctx_low, post)

def test_p_fill_sigma_penalty():
    ctx_a = {"depth_total": 300, "stake": 2.0, "matched_per_min": 120, "sigma": 0.3}
    ctx_b = {"depth_total": 300, "stake": 2.0, "matched_per_min": 120, "sigma": 1.5}
    post = {"p_fill": {"alpha": 30.0, "beta": 20.0}}
    assert compute_p_fill(ctx_a, post) > compute_p_fill(ctx_b, post)
