# ======================================================================
# engines/bus/engine_registry.py
# Canonical Engine Registry for BUS
# ======================================================================

from engines.micro_scalper_v7.risk_engine import RiskEngine
from engines.micro_scalper_v7.exploratory_engine import ExploratoryEngine
from engines.micro_scalper_v7.inplay_engine import InPlayEngine
from engines.micro_scalper_v7.unified_engine import UnifiedEngine
from engines.micro_scalper_v7.msc_blueprint_engine import BlueprintEngine
from engines.micro_scalper_v7.msc_context_engine import ContextEngine
from engines.micro_scalper_v7.msc_structure_engine import StructureEngine
from engines.micro_scalper_v7.msc_meta_engine import MetaEngine

ENGINE_REGISTRY = {
    "MSC_EXPLORATORY": ExploratoryEngine(),
    "MSC_INPLAY":      InPlayEngine(),
    "MSC_RISK":        RiskEngine(),
    "MSC_UNIFIED":     UnifiedEngine(),
    "MSC_BLUEPRINT":   BlueprintEngine(),
    "MSC_CONTEXT":     ContextEngine(),
    "MSC_STRUCTURE":   StructureEngine(),
    "MSC_META":        MetaEngine(),

}
