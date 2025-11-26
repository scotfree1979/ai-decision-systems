"""
Blueprint Predictor Runtime
- Loads known blueprints from JSON (builder output).
- Tracks observed OCs per runner.
- Matches prefix chapters to candidate blueprints.
- Scores candidates with priors, OC evidence, ensemble feedback.
"""

from __future__ import annotations
import json, os
from collections import defaultdict, Counter
from typing import Dict, Any, List, Optional

# Config knobs (can move into blueprint/config.py if preferred)
ENTRY_CONF_THRESHOLD = 0.35
STAKE_CURVE = [
    (0.40, 0.25),
    (0.55, 0.40),
    (0.70, 0.60),
    (0.85, 0.80),
    (1.01, 1.00),
]

# ---------------------------------------------------------------------
# Loader: blueprint signals JSON -> candidate index
# ---------------------------------------------------------------------

def _load_known_blueprints(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def build_index(bp_json: Dict[str, Any]) -> Dict[str, List[List[str]]]:
    """
    Build index: pattern_key -> list of chapter sequences.
    Example: "drift→OC10-OC20→flat→lay-to-back"
      -> ["drift", "OC10-OC20", "flat", "lay-to-back"]
    """
    out = {}
    for key in bp_json.keys():
        if not key:
            continue
        chapters = key.split("→")
        out[key] = chapters
    return out

# ---------------------------------------------------------------------
# Runtime state (per runner)
# ---------------------------------------------------------------------

_runner_state: Dict[tuple[str,int], Dict[str,Any]] = {}
_bp_index: Dict[str,List[str]] = {}

def init(path: str):
    """Load blueprints from JSON once per day."""
    global _bp_index
    bp_json = _load_known_blueprints(path)
    _bp_index = build_index(bp_json)

def observe_oc(mid: str, sid: int, oc_label: str, move: str):
    """Update observed sequence for (market, runner)."""
    key = (mid, sid)
    st = _runner_state.setdefault(key, {"chapters":[]})
    st["chapters"].append((oc_label, move))

def score_candidates(mid: str, sid: int,
                     ensemble_score: float = 0.5,
                     oc_evidence_score: float = 0.5,
                     prior_weight: float = 0.40,
                     oc_weight: float = 0.25,
                     ensemble_weight: float = 0.15) -> Dict[str,Any]:
    """
    Match observed chapters to blueprint prefixes.
    Return {side, confidence, stake, expected_next, candidates}
    """
    key = (mid, sid)
    st = _runner_state.get(key, {})
    chapters = st.get("chapters", [])
    if len(chapters) < 2:
        return {"decision": None, "why":"<2 chapters"}

    # Flatten observed sequence to ["drift","OC10-OC20","flat",...]
    observed = []
    for lab, move in chapters:
        observed.append(move)     # drift/steam/flat
        observed.append(lab)      # "OC10-OC20"
    # cut to prefix length
    prefix = observed[:len(observed)]

    candidates = []
    for pk, chs in _bp_index.items():
        if chs[:len(prefix)] == prefix[:len(chs[:len(prefix)])]:
            candidates.append(pk)

    if not candidates:
        return {"decision": None, "why":"no_candidates"}

    # Count next chapters across candidates
    next_chap = []
    for pk in candidates:
        chs = _bp_index[pk]
        if len(chs) > len(prefix):
            next_chap.append(chs[len(prefix)])
    dist = Counter(next_chap)
    expected_next = dist.most_common(1)[0][0] if dist else None

    # Confidence (blended)
    conf = (prior_weight*0.5 +
            oc_weight*oc_evidence_score +
            ensemble_weight*ensemble_score)

    # Stake curve
    s_factor = 0.25
    for th, f in STAKE_CURVE:
        if conf < th:
            s_factor = f
            break
    stake_mult = s_factor

    # Side (lay-first)
    side = "LAY"

    return {
        "decision": True,
        "side": side,
        "confidence": round(conf,3),
        "stake_mult": stake_mult,
        "expected_next": expected_next,
        "candidates": candidates[:5]  # top few for logging
    }
