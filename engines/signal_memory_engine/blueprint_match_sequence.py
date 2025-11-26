# 📘 blueprint_match_sequence.py
# Central matching logic that evaluates rolling OC chapter alignment against known blueprint stories

from signal_memory_engine.controller import load_yesterday_blueprint_patterns as get_known_blueprint_patterns


def match_blueprint_sequence(snapshot):
    """
    Takes a snapshot and compares recent OC transitions against known blueprints.
    Returns match_type (exploratory, partial, rolling, full_story), match_depth, matched key, and confidence.
    """
    runner_transitions = snapshot.get("oc_snapshots", {})
    oc_order = [f"OC{i}" for i in range(1, 7)]  # Only consider OC1 to OC6 for now

    sequence_history = []
    for i in range(len(oc_order) - 1):
        prev, curr = runner_transitions.get(oc_order[i]), runner_transitions.get(oc_order[i + 1])
        if prev is not None and curr is not None:
            move = "drift" if curr > prev else "steam" if curr < prev else "flat"
            label = f"{move}→{oc_order[i]}–{oc_order[i+1]}"
            sequence_history.append(label)

    if not sequence_history:
        return {"match_type": "exploratory", "match_depth": 0, "story_confidence": 0.0}

    known_blueprints = get_known_blueprint_patterns()

    best_match = None
    best_depth = 0
    best_story_key = None

    for pattern_key, pattern in known_blueprints.items():
        blueprint_seq = pattern.get("sequence", [])

        for i in range(len(blueprint_seq) - len(sequence_history) + 1):
            segment = blueprint_seq[i:i + len(sequence_history)]
            matches = [1 for a, b in zip(segment, sequence_history) if a == b]
            depth = sum(matches)

            if depth > best_depth:
                best_depth = depth
                best_match = segment
                best_story_key = pattern_key

    match_type = "exploratory"
    if best_depth >= 1:
        match_type = "partial"
    if best_depth >= 3:
        match_type = "rolling"
    if best_depth == 6:
        match_type = "full_story"

    return {
        "match_type": match_type,
        "match_depth": best_depth,
        "story_confidence": round(best_depth / 6, 2),
        "matched_story": best_story_key or "",
        "matched_chapters": best_match or []
    }
