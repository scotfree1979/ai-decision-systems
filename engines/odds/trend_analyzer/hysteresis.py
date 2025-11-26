from __future__ import annotations
from dataclasses import dataclass
from .types import VolRegime

@dataclass
class HystState:
    state: str = "NEUTRAL"  # NEUTRAL, HOLD_BACK, HOLD_LAY
    hold_ms_left: int = 0

class Hysteresis:
    def __init__(self, hold_ms: dict[VolRegime,int], delta_tau_flip: dict[VolRegime,float]) -> None:
        self.hold_ms_cfg = hold_ms
        self.delta_flip = delta_tau_flip
        self.states: dict[tuple[str,int], HystState] = {}

    def update(self, key: tuple[str,int], regime: VolRegime, back_conf: float, lay_conf: float, dt_ms: int) -> tuple[str, float]:
        st = self.states.get(key, HystState())
        # decrement hold timer
        st.hold_ms_left = max(st.hold_ms_left - max(dt_ms,0), 0)
        # decide desired side
        desired = "BACK" if back_conf >= lay_conf else "LAY"
        diff = abs(back_conf - lay_conf)
        # if in hold and no strong reason to flip, keep
        if st.state.startswith("HOLD"):
            if st.hold_ms_left > 0 or diff < self.delta_flip[regime]:
                side_now = "BACK" if st.state == "HOLD_BACK" else "LAY"
                self.states[key] = st
                return side_now, 0.0
        # adopt desired, start new hold
        st.state = "HOLD_BACK" if desired == "BACK" else "HOLD_LAY"
        st.hold_ms_left = self.hold_ms_cfg[regime]
        self.states[key] = st
        return desired, diff

    def snapshot(self, key: tuple[str,int]) -> HystState:
        return self.states.get(key, HystState())
