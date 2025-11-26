#!/usr/bin/env python3
import os, ast, sys, importlib

# ============================
# FIX: force repo root import
# ============================
REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir)
)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

ENGINES_DIR = os.path.join(REPO_ROOT, "engines")


# Real writer roots — correct paths
WRITER_ROOTS = [
    "engines.odds.odds_service",
    "engines.indicators.market_data",
    "engines.risk.budget_manager",
    "engines.live.overwatcher",
    "engines.mastery.feedback_scheduler",
    "engines.market_monitor.monitor",
    "engines.decision_engine.orchestrator",
]

WRITE_PATTERNS = [
    "INSERT ",
    "UPDATE ",
    "DELETE ",
    "REPLACE ",
    "enqueue_write",
    "open_auto_db",
    "open_bets_db",
    "open_settlements_db",
]

EXCLUDE_PREFIXES = (
    "gui.",
    "engines.tests",
    "engines.pages",
)

def module_file(modname):
    """Return the full path to module source within the repo."""
    rel = modname.replace(".", "/") + ".py"
    local_path = os.path.join(REPO_ROOT, rel)
    if os.path.exists(local_path):
        return local_path
    return None

def load_source(modname):
    path = module_file(modname)
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf8", errors="ignore") as f:
            return f.read()
    except:
        return ""

def get_imports(src):
    try:
        tree = ast.parse(src)
    except:
        return []

    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("engines"):
                mods.append(node.module)
        if isinstance(node, ast.Import):
            for n in node.names:
                if n.name.startswith("engines"):
                    mods.append(n.name)
    return mods

def is_writer(src):
    up = src.upper()
    return any(p in up for p in WRITE_PATTERNS)

def walk_tree():
    visited = {}
    queue = WRITER_ROOTS[:]

    while queue:
        mod = queue.pop(0)
        if mod in visited:
            continue
        if mod.startswith(EXCLUDE_PREFIXES):
            continue

        src = load_source(mod)
        if not src:
            continue

        reason = None
        if is_writer(src):
            reason = "DB-WRITER"
        elif mod in WRITER_ROOTS:
            reason = "ROOT-WRITER"

        if reason:
            visited[mod] = reason
            children = get_imports(src)
            for child in children:
                if not child.startswith(EXCLUDE_PREFIXES):
                    queue.append(child)

    return visited

if __name__ == "__main__":
    writers = walk_tree()

    print("\n=== WRITER TREE (reloadable) ===")
    for m, r in sorted(writers.items()):
        print(f"{m:50s}  ← {r}")

    print("\n\n=== STEP-4 RELOAD BLOCK ===")
    print("        # === AUTO-GENERATED DAL RELOAD BLOCK ===")
    print("        import importlib\n")
    for m in sorted(writers.keys()):
        var = "_mod_" + m.replace(".", "_")
        print(f"        import {m} as {var}")
        print(f"        importlib.reload({var})\n")
    print("        print('[LIVE DAL] reload complete')")
    print("        # === END AUTO-GENERATED RELOAD BLOCK ===")
