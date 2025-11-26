from engines.config_paths import connect_db

def test_mastery_state_exists():
    with connect_db(ro=True) as conn:
        row = conn.execute("SELECT id, progress FROM mastery_state WHERE id=1").fetchone()
        assert row is not None
        assert row[0] == 1
        assert 0 <= row[1] <= 100
