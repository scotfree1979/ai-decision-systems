# ======================================================================
# engines/bus/engine_registry.py
# Canonical Engine Registry for BUS
# ======================================================================

from engines.micro_scalper_v7.risk_engine import RiskEngine
from engines.micro_scalper_v7.exploratory_engine import ExploratoryEngine
from engines.micro_scalper_v7.inplay_engine import InPlayEngine



ENGINE_REGISTRY = {
    "MSC_EXPLORATORY": ExploratoryEngine(),
    "MSC_INPLAY":      InPlayEngine(),
    "MSC_RISK":        RiskEngine(),

}
