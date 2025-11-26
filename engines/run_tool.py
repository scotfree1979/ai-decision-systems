# 📁 run_tool.py
# ✅ Fully restored to original structure with reload + scalper module support

import sys
import os
import time
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from reload_watcher import start_reload_watcher
from config_paths import DB_PATH, ensure_db_readable
import scalper_module  # ✅ Ensure module is imported

if __name__ == "__main__":
    print("🚀 Running tool with hot reload...")
    observer = start_reload_watcher()

    try:
        scalper_module.start_scalper_session()
    except Exception as e:
        print(f"❌ Tool crashed: {e}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("🛑 Exiting...")
        observer.stop()
        observer.join()
