from collections import defaultdict, Counter


def match_partial_segment(segment_db, segment_key):
    return segment_db.get(segment_key, [])


def predict_next_segment(segment_db, segment_key):
    matches = match_partial_segment(segment_db, segment_key)
    if not matches:
        return None

    next_counts = Counter()
    total_conf = defaultdict(float)

    for m in matches:
        if m["next_segment"]:
            next_counts[m["next_segment"]] += 1
            total_conf[m["next_segment"]] += m["meta"].get("avg_confidence", 0.0)

    if not next_counts:
        return None

    best_next = next_counts.most_common(1)[0][0]
    avg_conf = round(total_conf[best_next] / next_counts[best_next], 3)
    direction = "lay_to_back" if "drift" in best_next else "back_to_lay"

    return {
        "next_segment": best_next,
        "confidence": avg_conf,
        "direction": direction,
        "segment_key": segment_key
    }
