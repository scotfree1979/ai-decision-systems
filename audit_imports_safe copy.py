#!/usr/bin/env python3
import sys, importlib, time, traceback

LOG = "/tmp/autoscalp_import_log.csv"

with open(LOG, "w") as f:
    f.write("ts,module,file\n")

_real_import = importlib.import_module

def audit_import(name, package=None):
    t = time.time()
    try:
        mod = _real_import(name, package)
        try:
            path = getattr(mod, "__file__", "(builtin)")
        except Exception:
            path = "(unknown)"
        with open(LOG, "a") as f:
            f.write(f"{t},{name},{path}\n")
        return mod
    except Exception:
        traceback.print_exc()
        raise

importlib.import_module = audit_import

print(f"[SAFE IMPORT AUDIT] active → {LOG}")
print("Now run: python3 -m engines.gui.GUI")
