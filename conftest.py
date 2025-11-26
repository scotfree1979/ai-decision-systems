# conftest.py — defines CLI options & fixtures for pytest BEFORE parsing
import pytest

def pytest_addoption(parser):
    parser.addoption("--db", action="store", default=None, help="Path to autoscalp_gui.db")
    parser.addoption("--date", action="store", default=None, help="YYYY-MM-DD (UTC day; default: yesterday in UTC)")
    parser.addoption("--cap", action="store", default="3", help="CAP value")

@pytest.fixture(scope="session")
def db_path(pytestconfig):
    val = pytestconfig.getoption("--db")
    if not val:
        pytest.skip("Provide --db to run DB tests, e.g. --db=data/autoscalp_gui.db")
    return val

@pytest.fixture(scope="session")
def target_date(pytestconfig):
    return pytestconfig.getoption("--date") or None

@pytest.fixture(scope="session")
def cap_value(pytestconfig):
    try:
        return int(pytestconfig.getoption("--cap") or "3")
    except Exception:
        return 3
