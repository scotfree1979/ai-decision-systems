# 📘 blueprint_engine_story_builder.py (Final Version)
# Builds OC0–OC20 story using direct column access to 'bets'
# Falls back to anchor_odd + odds_check_1–6 if OC0–OC6 missing

import json
from database_hijack_monitor import enqueue_read


def get_story_chapters(market_id, selection_id):
    story = []
    try:
        # Read OC0–OC20, anchor, odds_check_1–6 from bets
        query = """
            SELECT anchor_odd, odds_check_1, odds_check_2, odds_check_3,
                   odds_check_4, odds_check_5, odds_check_6,
                   oc0, oc1, oc2, oc3, oc4, oc5, oc6,
                   oc7, oc8, oc9, oc10, oc11, oc12, oc13,
                   oc14, oc15, oc16, oc17, oc18, oc19, oc20
            FROM bets
            WHERE marketId = ? AND selectionId = ?
            ORDER BY placed_at DESC LIMIT 1
        """
        row = enqueue_read(query, [market_id, selection_id])
        if not row or not row[0]:
            return []

        values = row[0]

        # Index map
        anchor_odd = values[0]
        odds_checks = values[1:7]  # odds_check_1–6
        oc_values = values[7:]     # oc0–oc20

        # Build OC0–OC6 with fallback
        for i in range(0, 7):
            label = f"OC{i}"
            oc_val = oc_values[i] if i < len(oc_values) else None
            fallback_val = anchor_odd if i == 0 else odds_checks[i - 1] if i - 1 < len(odds_checks) else None
            odds = oc_val if oc_val is not None else fallback_val

            if odds is not None:
                story.append({"label": label, "odds": float(odds)})
            else:
                break

        # Continue OC7–OC20 without fallback
        for i in range(7, 21):
            label = f"OC{i}"
            oc_index = i  # 0-based index in oc_values
            if oc_index < len(oc_values):
                odds = oc_values[oc_index]
                if odds is not None:
                    story.append({"label": label, "odds": float(odds)})
                else:
                    break

    except Exception as e:
        print(f"❌ Failed to load OC story for {market_id}-{selection_id}: {e}")
    return story
