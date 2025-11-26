from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import time
import importlib
import sys

# Modules inside the current repo
MODULES_TO_WATCH = ["bot", "api", "risk"]

class ReloadHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if event.src_path.endswith(".py"):
            print(f"🔄 File changed: {event.src_path}")
            for mod in MODULES_TO_WATCH:
                if mod in sys.modules:
                    try:
                        importlib.reload(sys.modules[mod])
                        print(f"✅ Reloaded: {mod}")
                    except Exception as e:
                        print(f"❌ Reload failed for {mod}: {e}")

def start_reload_watcher():
    observer = Observer()
    observer.schedule(ReloadHandler(), path='.', recursive=True)
    observer.start()
    return observer
