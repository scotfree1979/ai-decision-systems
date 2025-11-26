import sqlite3
from datetime import datetime
from config_paths import DB_PATH


def match_blueprint_partial(
    direction_prefix,
    current_oc,
    oc_snapshots,
    tick_pattern,
    unmatched_history,
    market_id,
    selection_id,
    blueprint_patterns
):
    matches = []
    now = datetime.utcnow().isoformat()

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        for col in ["report_type", "status", "oc_band", "customer_order_ref"]:
            try:
                cursor.execute(f"ALTER TABLE playbooks ADD COLUMN {col} TEXT")
            except:
                pass

        for pattern_key in blueprint_patterns:
            try:
                parts = pattern_key.split("→")
                if len(parts) != 4:
                    continue

                bp_direction, oc_range, bp_pattern, action = parts
                if bp_direction != direction_prefix:
                    continue
                if bp_pattern != tick_pattern:
                    continue

                start_oc = int(oc_range.split("-")[0].replace("OC", ""))
                end_oc = int(oc_range.split("-")[1].replace("OC", ""))

                oc_band = f"OC{start_oc}-OC{end_oc}"

                if not (start_oc <= current_oc <= end_oc):
                    status = "outside_oc_range"
                elif current_oc >= end_oc:
                    status = "no_time_remaining"
                else:
                    available_ocs = [f"OC{i}" for i in range(start_oc, current_oc + 1) if f"OC{i}" in oc_snapshots]
                    missing_ocs = [f"OC{i}" for i in range(start_oc, current_oc + 1) if f"OC{i}" not in oc_snapshots]

                    if f"OC{current_oc}" not in oc_snapshots:
                        status = "missing_current_oc"
                    else:
                        recent_patterns = [x.get("tick_pattern") for x in unmatched_history[-4:]]
                        match_strength = sum(1 for p in recent_patterns if p == tick_pattern)
                        scalp_ticks = 3 if match_strength == 4 else 2 if match_strength >= 2 else 1

                        completion = len(available_ocs) / (end_oc - start_oc + 1)
                        confidence = round(0.6 + (0.4 * completion), 2)

                        status = "ready_to_fire"
                        matches.append({
                            "pattern_key": pattern_key,
                            "confidence": confidence,
                            "expected_action": action,
                            "end_oc": end_oc,
                            "scalp_ticks": scalp_ticks,
                            "oc_band": oc_band
                        })

                        cursor.execute("""
                            INSERT INTO playbooks (
                                marketId, selectionId, pattern_key, blueprint_match,
                                scalp_direction, tick_pattern, confidence, scalp_ticks,
                                result, opened_at, report_type, status, oc_band
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            market_id, selection_id, pattern_key, pattern_key,
                            action, tick_pattern, confidence, scalp_ticks,
                            None, now, "partial_report", status, oc_band
                        ))

                cursor.execute("""
                    INSERT INTO playbooks (
                        marketId, selectionId, pattern_key, blueprint_match,
                        scalp_direction, tick_pattern, confidence, scalp_ticks,
                        result, opened_at, report_type, status, oc_band
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    market_id, selection_id, pattern_key, None,
                    action, tick_pattern, 0.0, 0,
                    None, now, "partial_report", status, oc_band
                ))

            except Exception as e:
                continue

        conn.commit()

    if not matches:
        return None
    matches.sort(key=lambda x: x["confidence"], reverse=True)
    return matches

def attempt_partial_blueprint_match(self, market_id, selection_id):
    key = (market_id, selection_id)
    runner = self.get_static_snapshot().get(key)
    if not runner:
        return

    snapshot = runner.get("snapshot", {})
    oc_snaps = snapshot.get("oc_snapshots", {})
    if not oc_snaps or "OC0" not in oc_snaps or len(oc_snaps) < 2:
        return

    latest_oc = sorted(oc_snaps.keys(), key=lambda x: int(x.replace("OC", "")))[-1]
    current_oc = int(latest_oc.replace("OC", ""))
    direction = snapshot.get("direction_bias", "flat")
    tick_pattern = snapshot.get("tick_pattern", "flat")

    matches = self.match_partial_segment(
        direction_prefix=direction,
        current_oc=current_oc,
        oc_snapshots=oc_snaps,
        tick_pattern=tick_pattern,
        unmatched_history=self.unmatched_signals.get(key, []),
        market_id=market_id,
        selection_id=selection_id,
        blueprint_patterns=self.KNOWN_BLUEPRINT_PATTERNS
    )

    if not matches:
        return

    match = matches[0]
    signal = {
        "marketId": market_id,
        "selectionId": selection_id,
        "odds": snapshot.get("tick_trail", [])[-1] if snapshot.get("tick_trail") else snapshot.get("anchor_odd", 0.0),
        "range_low": snapshot.get("range_low"),
        "range_high": snapshot.get("range_high"),
        "tick_pattern": tick_pattern,
        "volatility": snapshot.get("volatility", 0.0),
        "position_ratio": snapshot.get("position_ratio", 0.5),
        "minutes_to_post": snapshot.get("minutes_to_post", 999),
        "confidence": match["confidence"],
        "blueprint_match": match["pattern_key"],
        "pattern_key": match["pattern_key"],
        "scalp_direction": match["expected_action"],
        "signal_type": "partial_blueprint",
        "scalp_ticks": match["scalp_ticks"],
        "customerOrderRef": f"{match['pattern_key']}_{uuid.uuid4().hex[:6]}",
        "forced": False
    }

    print(f"🤠 [PARTIAL MATCH] {selection_id} → {match['pattern_key']} | OC Band: {match['oc_band']} | Confidence: {match['confidence']}")
    self.track_unmatched_signal(signal)

