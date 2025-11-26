#!/usr/bin/env python3
"""
www.py — AutoScalp Web Dashboard Launcher (safe-restart)
────────────────────────────────────────────────────────────
Ensures any previous Streamlit dashboard on :8501 is closed,
then restarts a clean instance and tunnels it through ngrok.
"""

import os, sys, time, subprocess, socket, signal
from pyngrok import ngrok

STREAMLIT_PORT = 8501
APP_PATH = os.path.join(os.path.dirname(__file__), "web_dashboard_core.py")

# ✅ Hook: expose GhostTrader (engines/pages) to Streamlit
ENGINES_PAGES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "engines", "pages"))
os.environ["STREAMLIT_PAGES_DIRS"] = ENGINES_PAGES
print(f"[www] 🔗 Linked GhostTrader pages from: {ENGINES_PAGES}")


# ─────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────
def _kill_port(port: int):
    """Kill any process currently bound to the given TCP port."""
    try:
        # macOS / Linux
        cmd = f"lsof -ti tcp:{port}"
        pids = subprocess.check_output(cmd, shell=True).decode().strip().splitlines()
        for pid in pids:
            if pid:
                os.kill(int(pid), signal.SIGKILL)
                print(f"[www] 💀 Killed existing process on port {port} (PID={pid})")
    except subprocess.CalledProcessError:
        pass  # no process bound

def _wait_for_port(port: int, timeout: int = 10):
    """Wait until the given port starts accepting connections."""
    start = time.time()
    while time.time() - start < timeout:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False

# ─────────────────────────────────────────────
# Main logic
# ─────────────────────────────────────────────
def start_streamlit():
    """Start Streamlit cleanly after killing any old instance."""
    _kill_port(STREAMLIT_PORT)
    print(f"[www] 🚀 Launching Streamlit dashboard on port {STREAMLIT_PORT}…")
    subprocess.Popen(
        ["streamlit", "run", APP_PATH, "--server.port", str(STREAMLIT_PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if _wait_for_port(STREAMLIT_PORT):
        print(f"[www] ✅ Streamlit is live on http://localhost:{STREAMLIT_PORT}")
    else:
        print(f"[www] ⚠️ Streamlit did not start on port {STREAMLIT_PORT}")
    time.sleep(2)

def start_ngrok():
    """Expose Streamlit externally via ngrok."""
    print("[www] 🌐 Starting ngrok tunnel…")
    public_url = ngrok.connect(STREAMLIT_PORT, "http").public_url
    print(f"\n✅ Web Dashboard LIVE!")
    print(f"   🌍  {public_url}")
    print("   📱  Accessible on any device.\n")
    return public_url

if __name__ == "__main__":
    try:
        start_streamlit()
        start_ngrok()
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n🛑 Stopping Streamlit + ngrok…")
        ngrok.kill()
        sys.exit(0)
