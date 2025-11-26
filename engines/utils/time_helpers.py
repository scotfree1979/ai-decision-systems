from datetime import datetime, timedelta
import os

SIMULATED_DURATION_MIN = 5
SIMULATED_TOTAL_MINUTES = 100
TEST_CLOCK_PATH = "./.test_start_time.txt"
TEST_START_TIME = None
TEST_MODE = os.environ.get("TEST_MODE") == "1"

def load_or_set_test_start_time():
    global TEST_START_TIME
    if not TEST_MODE:
        return
    try:
        if os.path.exists(TEST_CLOCK_PATH):
            with open(TEST_CLOCK_PATH, "r") as f:
                TEST_START_TIME = datetime.fromisoformat(f.read().strip())
        else:
            TEST_START_TIME = datetime.utcnow()
            with open(TEST_CLOCK_PATH, "w") as f:
                f.write(TEST_START_TIME.isoformat())
    except:
        TEST_START_TIME = datetime.utcnow()

    return TEST_START_TIME

if TEST_MODE:
    load_or_set_test_start_time()

def get_simulated_now():
    if not TEST_MODE:
        return datetime.utcnow()

    elapsed_real = (datetime.utcnow() - TEST_START_TIME).total_seconds()
    simulated_minutes = (elapsed_real / (SIMULATED_DURATION_MIN * 60)) * SIMULATED_TOTAL_MINUTES
    return (TEST_START_TIME + timedelta(minutes=simulated_minutes))
