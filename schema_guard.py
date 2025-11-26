# engines/schema_guard.py
"""
DeathAutoHealReturnNone
-----------------------
All legacy schema guard hooks disabled.
Every function returns None. No DB changes ever occur.
"""

def auto_heal(*args, **kwargs):
    return None

def sync_views(*args, **kwargs):
    return None

def ensure_compat(*args, **kwargs):
    return None

def heal_all(*args, **kwargs):
    return None

def check(*args, **kwargs):
    return None

def repair(*args, **kwargs):
    return None
