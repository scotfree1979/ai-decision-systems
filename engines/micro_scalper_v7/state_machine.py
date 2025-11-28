# /engines/micro_scalper_v7/state_machine.py

from enum import Enum

class MSCGlobalState(Enum):
    IDLE        = 0
    PRE_OFF     = 1
    IN_PLAY     = 2
    RESET       = 3


class RiskSubState(Enum):
    IDLE            = 0
    ATTACHED        = 1
    WITH_PARENT     = 2
    AGAINST_PARENT  = 3
    APPROACH_CSL    = 4
    STOP_CLEANUP    = 5
    DETACHED        = 6


class ExploratorySubState(Enum):
    IDLE        = 0
    EVALUATE    = 1
    ENTRY       = 2
    MONITOR     = 3
    EXIT        = 4


class InPlaySubState(Enum):
    IDLE            = 0
    ANALYSE         = 1
    EVALUATE_LAY    = 2
    ENTRY           = 3
    MONITOR         = 4
    EXIT            = 5
