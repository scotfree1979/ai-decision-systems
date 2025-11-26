#!/usr/bin/env python3
"""
Quick import self-test. Goal: fail fast on packaging/path issues.
Usage:
  python3 -m engines.import_smoke
"""
import sys, os, importlib, traceback

def check(label, fn):
    try:
        fn()
        print(f"[PASS] {label}")
    except Exception as e:
        print(f"[FAIL] {label}: {e}")
        traceback.print_exc()
        return False
    return True

def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)

    ok = True
    ok &= check("import engines", lambda: importlib.import_module("engines"))
    ok &= check("import engines.autoscalp_gui", lambda: importlib.import_module("engines.autoscalp_gui"))
    # Signal memory engine may be a directory or package under engines/
    ok &= check("import engines.signal_memory_engine", lambda: importlib.import_module("engines.signal_memory_engine"))

    # Optional: probe a few common submodules if present
    for mod in [
        "engines.signal_memory_engine.controller",
        "engines.signal_memory_engine.ram_reader",
        "engines.signal_memory_engine.ram_writer",
    ]:
        ok &= check(f"import {mod}", lambda m=mod: importlib.import_module(m))

    print("\nRESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
