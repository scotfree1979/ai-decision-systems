# engines/decision_engine/decide_once/decide_once.py
from __future__ import annotations
from typing import Optional
import importlib, os, time, sys

def decide_once(run_id: str, source_override: str | None = None, logger=None) -> Optional[int]:
    """
    Unified DecideOnce entrypoint for LIVE mode.

    - Always reloads engines.decision_engine.decide_once.lanes so GUI never uses a stale copy.
    - Prints trace guard (file path + modified time) for verification.
    - Delegates fully to run_all(), which now handles scope + Mastery internally.
    """
    source = (source_override or "LIVE").upper()

    try:
        # --- Reload lanes directly by full module path --------------------------
        import engines.decision_engine.decide_once.lanes as lanes
        importlib.reload(lanes)
        _run_all = lanes.run_all

        lanes_file = getattr(lanes, "__file__", None)
        mtime = None
        if lanes_file and os.path.exists(lanes_file):
            mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(lanes_file)))

        trace_line = f"[DECIDE] wrapper → lanes.run_all (reloaded, mode={source})"
        if lanes_file:
            trace_line += f"\n[TRACE] using {lanes_file}"
            if mtime:
                trace_line += f" (last modified {mtime})"
        print(trace_line)

    except Exception as e:
        msg = f"[DECIDE] lanes reload failed: {e}"
        if logger:
            logger(msg)
        else:
            print(msg)
        return None

    try:
        # --- Call run_all directly (guaranteed fresh) ----------------------------
        return _run_all(run_id, source=source, logger=logger)
    except Exception as e:
        msg = f"[DECIDE] run_all raised: {type(e).__name__}: {e}"
        if logger:
            logger(msg)
        else:
            print(msg)
        return None
