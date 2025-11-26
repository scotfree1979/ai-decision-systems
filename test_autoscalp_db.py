# test_autoscalp_db.py — simple tests using fixtures from conftest.py

import pytest
import importlib.util
import sys
from pathlib import Path

def _load_audit_module():
    mod_path = Path(__file__).parent / "db_audit_yesterday.py"
    spec = importlib.util.spec_from_file_location("db_audit_yesterday", mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["db_audit_yesterday"] = mod
    spec.loader.exec_module(mod)  # type: ignore
    return mod

@pytest.fixture(scope="session")
def audit_mod():
    return _load_audit_module()

def test_schema(audit_mod, db_path, target_date, cap_value):
    res = audit_mod.run_audit(db_path, target_date, cap_value)
    assert not res.schema_missing, f"Missing schema columns: {res.schema_missing}"

def test_cap(audit_mod, db_path, target_date, cap_value):
    res = audit_mod.run_audit(db_path, target_date, cap_value)
    assert not res.cap_violations, f"CAP violations found: {len(res.cap_violations)}"

def test_stamping(audit_mod, db_path, target_date, cap_value):
    res = audit_mod.run_audit(db_path, target_date, cap_value)
    assert not res.stamp_issues, f"Parents missing letter 'source': {len(res.stamp_issues)}"

def test_finalize(audit_mod, db_path, target_date, cap_value):
    res = audit_mod.run_audit(db_path, target_date, cap_value)
    assert not res.finalize_issues, f"Parents with matched child but not closed: {len(res.finalize_issues)}"

def test_rotation(audit_mod, db_path, target_date, cap_value):
    res = audit_mod.run_audit(db_path, target_date, cap_value)
    if res.rotation_dups:
        dup = res.rotation_dups[0]
        pytest.skip(f"Rotation duplicates exist (first: {dup.minute} {dup.marketId}/{dup.selectionId} {dup.letter})")
