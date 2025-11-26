#!/usr/bin/env python3
"""
web_dashboard.py — Streamlit dashboard with TunnelRoot auto-exposure
====================================================================
Launches a secure ngrok tunnel automatically so you can access the
dashboard from your phone or any external device.
"""

import os, subprocess, threading, time, webbrowser

def launch_tunnel(port=8501):
    """Launch ngrok tunnel for the given port and print public URL."""
    try:
        # start ngrok in background
        proc = subprocess.Popen(
            ["ngrok", "http", str(port), "--log=stdout"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        url = None
        for line in iter(proc.stdout.readline, ""):
            if "url=" in line and "https://" in line:
                url = line.split("url=")[-1].strip()
                break
        if url:
            print(f"\n🌐 Tunnel active → {url}\n")
            webbrowser.open(url)
        else:
            print("[tunnel] no public URL detected — check ngrok installation.")
    except Exception as e:
        print(f"[tunnel] failed: {e}")

def launch_dashboard():
    """Launch Streamlit dashboard and auto-tunnel."""
    port = 8501
    thread = threading.Thread(target=launch_tunnel, args=(port,), daemon=True)
    thread.start()
    os.system(f"streamlit run gui/web_dashboard_core.py --server.port {port}")

if __name__ == "__main__":
    print("[TunnelRoot] starting Streamlit + ngrok tunnel…")
    launch_dashboard()
