# bias/model.py
from dataclasses import dataclass
from typing import Literal

BiasDir = Literal["L2B", "B2L", "FLAT"]

@dataclass
class BiasOut:
    value: float          # [-1.0, +1.0]  >0 favors L2B (shorten), <0 favors B2L (drift)
    dir: BiasDir          # "L2B" | "B2L" | "FLAT"
    conf: float           # [0.0, 1.0]
    why: str = ""         # human-readable rationale

    def as_plan_fields(self) -> dict:
        return {
            "bias": self.value,
            "bias_dir": self.dir,
            "bias_conf": self.conf,
        }
