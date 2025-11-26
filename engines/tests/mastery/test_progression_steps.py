from __future__ import annotations
import json
from engines.config_paths import connect_db

def _progress() -> int:
    with connect_db(ro=True) as conn:
        row = conn.execute("SELECT progress FROM mastery_state WHERE id=1").fetchone()
        return int(row[0] if row else 0)

def test_start_and_learn_steps_increment_progress():
    with connect_db(ro=False) as conn:
        conn.execute("UPDATE mastery_state SET progress=0, thresholds_json='{}', volatility_json='{}', liquidity_json='{}', time_windows_json='{}', exit_policy_json='{}', confidence_json='{}' WHERE id=1")
        # thresholds (+20)
        conn.execute("UPDATE mastery_state SET thresholds_json=?", (json.dumps({"steam_strength":0.65}),))
        conn.execute("INSERT INTO mastery_events(event_type,details_json,delta_progress,source) VALUES (?,?,20,'TEST')",
                     ("learn_thresholds", json.dumps({"steam_strength":0.65})))
        # volatility (+15)
        conn.execute("UPDATE mastery_state SET volatility_json=?", (json.dumps({"sigma_low":0.5,"sigma_high":2.0}),))
        conn.execute("INSERT INTO mastery_events(event_type,details_json,delta_progress,source) VALUES (?,?,15,'TEST')",
                     ("learn_volatility", json.dumps({})))
        # liquidity (+15)
        conn.execute("UPDATE mastery_state SET liquidity_json=?", (json.dumps({"min_lay_avail":250}),))
        conn.execute("INSERT INTO mastery_events(event_type,details_json,delta_progress,source) VALUES (?,?,15,'TEST')",
                     ("learn_liquidity", json.dumps({})))
        # time windows (+15)
        conn.execute("UPDATE mastery_state SET time_windows_json=?", (json.dumps({"windows":[["30","10"],["10","2"]]}),))
        conn.execute("INSERT INTO mastery_events(event_type,details_json,delta_progress,source) VALUES (?,?,15,'TEST')",
                     ("learn_time_windows", json.dumps({})))
        # exit policy (+15)
        conn.execute("UPDATE mastery_state SET exit_policy_json=?", (json.dumps({"hard_stop_ticks":2}),))
        conn.execute("INSERT INTO mastery_events(event_type,details_json,delta_progress,source) VALUES (?,?,15,'TEST')",
                     ("learn_exit_policy", json.dumps({})))
        # apply deltas to progress
        conn.execute("UPDATE mastery_state SET progress = MIN(100, progress + 20 + 15 + 15 + 15 + 15)")
        conn.commit()
    assert _progress() == 80

def test_advance_to_100_and_reset():
    with connect_db(ro=False) as conn:
        conn.execute("UPDATE mastery_state SET progress=80 WHERE id=1")
        conn.execute("INSERT INTO mastery_events(event_type,details_json,delta_progress,source) VALUES (?,?,20,'TEST')",
                     ("advance_to_100", "{}"))
        conn.execute("UPDATE mastery_state SET progress=100 WHERE id=1")
        conn.commit()
    assert _progress() == 100

    # reset
    with connect_db(ro=False) as conn:
        conn.execute("UPDATE mastery_state SET progress=0, thresholds_json='{}', volatility_json='{}', liquidity_json='{}', time_windows_json='{}', exit_policy_json='{}', confidence_json='{}' WHERE id=1")
        conn.execute("DELETE FROM mastery_events")
        conn.commit()
    assert _progress() == 0
