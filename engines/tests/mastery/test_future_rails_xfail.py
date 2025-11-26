import pytest

@pytest.mark.xfail(reason="kill-switch/cooldown/session caps not implemented yet")
def test_kill_switch_on_sigma_flip():
    assert False, "pending implementation"

@pytest.mark.xfail(reason="posteriors update from outcomes not implemented yet")
def test_posteriors_update_from_trade_outcomes():
    assert False, "pending implementation"
