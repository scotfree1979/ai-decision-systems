import json
from engines.config_paths import connect_db

def test_thresholds_event_writable():
    with connect_db(ro=False) as conn:
        conn.execute("UPDATE mastery_state SET thresholds_json=?, updated_at=datetime('now') WHERE id=1",
                     (json.dumps({"steam_strength":0.65}),))
        conn.execute("INSERT INTO mastery_events(event_type,details_json,delta_progress,source) VALUES (?,?,20,'TEST')",
                     ("learn_thresholds", json.dumps({"steam_strength":0.65})))
        conn.commit()
        ev = conn.execute("SELECT event_type, delta_progress FROM mastery_events ORDER BY id DESC LIMIT 1").fetchone()
        assert ev[0] == "learn_thresholds"
        assert ev[1] == 20
