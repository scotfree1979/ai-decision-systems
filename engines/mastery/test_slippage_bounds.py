from engines.mastery.microstructure import expected_slippage_ticks
def test_slippage_bounds_and_direction():
    assert expected_slippage_ticks(0.95, 0.5) < expected_slippage_ticks(0.60, 0.5)
    assert expected_slippage_ticks(0.60, 0.2) < expected_slippage_ticks(0.60, 1.2)
    assert expected_slippage_ticks(0.5, 1.0) >= 0.0
