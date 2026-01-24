# engines/shadow_confidence.py

from collections import defaultdict

_shadow_conf = defaultdict(lambda: {"DRIFT": 0, "STEAM": 0})

def record(marketId, selectionId, engine, direction):
    key = (marketId, selectionId, engine)
    _shadow_conf[key][direction] += 1
    return _shadow_conf[key][direction]

def get(marketId, selectionId, engine):
    return _shadow_conf.get((marketId, selectionId, engine), {"DRIFT": 0, "STEAM": 0})
