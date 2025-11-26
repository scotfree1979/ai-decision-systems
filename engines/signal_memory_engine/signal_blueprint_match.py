import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engines.blueprint_match_sequence import match_blueprint_sequence


def detect_blueprint_match(snapshot):
    """
    Evaluates the snapshot against known blueprints using chapter-level match logic.
    Returns a dict with match_type, match_depth, and story confidence.
    """
    match_result = match_blueprint_sequence(snapshot)

    # Attach blueprint logic to the signal
    if match_result["match_type"] in ["partial", "rolling", "full_story"]:
        return {
            "blueprint_key": match_result.get("matched_story"),
            "match_type": match_result["match_type"],
            "match_depth": match_result["match_depth"],
            "confidence": match_result["story_confidence"],
            "matched_chapters": match_result.get("matched_chapters", [])
        }

    return None  # fallback to exploratory logic elsewhere

def predict_next_segment(self, segment_key):
    """
    Predicts the most likely next OC segment key based on current sequence and known blueprints.
    """
    if not segment_key or not isinstance(segment_key, str):
        return None

    matching = [k for k in self.KNOWN_BLUEPRINT_PATTERNS if k.startswith(segment_key)]
    if not matching:
        return None

    next_parts = set()
    for m in matching:
        parts = m.split("→")
        seg_parts = segment_key.split("→")
        if len(parts) > len(seg_parts):
            next_parts.add(parts[len(seg_parts)])

    if not next_parts:
        return None

    return max(next_parts, key=lambda x: len(x))  # simple heuristic: return the longest next segment


