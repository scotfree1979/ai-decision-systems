# SAFE IMPORT AUDIT (Python 3.13–compatible)
# Does NOT override loaders / exec_module / create_module

import sys
import time
import threading

LOG_PATH = "/tmp/autoscalp_import_log.csv"
_current_step = "BOOT"
_lock = threading.Lock()

# Initialise log file
with open(LOG_PATH, "w", encoding="utf-8") as f:
    f.write("ts,step,module,file\n")

def set_step(step: str):
    global _current_step
    _current_step = step

# meta_path hook (observer-only)
class AuditImporter:
    def find_spec(self, fullname, path, target=None):
        # Only log AFTER module imports successfully
        def _post_import_hook(name, module):
            try:
                with _lock:
                    with open(LOG_PATH, "a", encoding="utf-8") as f:
                        f.write(f"{time.time()},{_current_step},{name},{getattr(module, '__file__', '?')}\n")
            except Exception:
                pass

        # Register a lazy hook AFTER module loads
        if fullname not in sys.modules:
            sys.meta_path.append(self._make_loader(fullname, _post_import_hook))

        return None  # allow normal import process

    def _make_loader(self, fullname, hook):
        class Loader:
            def create_module(self, spec):
                return None  # let Python create it normally

            def exec_module(self, module):
                # After normal load occurs, call hook
                hook(fullname, module)

        return Loader()

# Install observer only once
if not any(isinstance(h, AuditImporter) for h in sys.meta_path):
    sys.meta_path.insert(0, AuditImporter())

print(f"[SAFE IMPORT AUDIT] active → {LOG_PATH}")
