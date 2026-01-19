# placement_queues.py
# SINGLETON QUEUES — DO NOT DUPLICATE

import queue
import threading
import time
from typing import Tuple

# ------------------------------------------------------------------
# Diagnostics (OBSERVABILITY ONLY)
# ------------------------------------------------------------------

PLACEMENT_QUEUE_METRICS = {
    "input_seen": 0,
    "exec_enqueued": 0,
    "last_report_ts": 0.0,
}


# ------------------------------------------------------------------
# Queues
# ------------------------------------------------------------------

PLACEMENT_INPUT_QUEUE: "queue.Queue[Tuple[str, dict, dict]]" = queue.Queue()
PLACEMENT_EXEC_QUEUE:  "queue.Queue[Tuple[str, dict, dict]]" = queue.Queue()

ENGINE_PRIORITY = {
    "MSC_RISK": 0,
    "LEGACY": 1,
    "MSC_INPLAY": 2,
    "MSC_EXPLORATORY": 3,
}

_QUEUE_THREAD: threading.Thread | None = None


def _placement_queue_loop(poll_interval: float = 0.1):
    """
    Drain INPUT queue and feed EXEC queue in deterministic order.
    """
    buffer: list[tuple[str, dict, dict]] = []

    REPORT_EVERY = 5.0  # seconds

    while True:
        try:
            # 1) Drain INPUT (non-blocking)
            while True:
                try:
                    item = PLACEMENT_INPUT_QUEUE.get_nowait()
                    buffer.append(item)
                    PLACEMENT_QUEUE_METRICS["input_seen"] += 1
                except queue.Empty:
                    break

            if not buffer:
                time.sleep(poll_interval)
                continue

            # 2) Stable sort by engine priority
            buffer.sort(
                key=lambda x: ENGINE_PRIORITY.get(
                    x[1].get("engine"), 99
                )
            )

            # 3) Feed EXEC queue
            for item in buffer:
                PLACEMENT_EXEC_QUEUE.put(item)
                PLACEMENT_QUEUE_METRICS["exec_enqueued"] += 1

            buffer.clear()

            # --------------------------------------------------
            # 📊 PERIODIC QUEUE REPORT (OBSERVABILITY ONLY)
            # --------------------------------------------------
            now = time.time()
            if now - PLACEMENT_QUEUE_METRICS["last_report_ts"] >= REPORT_EVERY:
                PLACEMENT_QUEUE_METRICS["last_report_ts"] = now

                try:
                    print(
                        "[PLACEMENT][QUEUES] "
                        f"INPUT={PLACEMENT_INPUT_QUEUE.qsize()} "
                        f"EXEC={PLACEMENT_EXEC_QUEUE.qsize()} "
                        f"seen={PLACEMENT_QUEUE_METRICS['input_seen']} "
                        f"enq={PLACEMENT_QUEUE_METRICS['exec_enqueued']}"
                    )
                    print(
                        "[PLACEMENT][QUEUE_ID]",
                        id(PLACEMENT_EXEC_QUEUE)
                    )

                except Exception:
                    pass

        except Exception as e:
            print(f"[PLACEMENT][QUEUE_LOOP][ERR] {e}")
            time.sleep(0.5)


def start_placement_queue_loop():
    """
    Idempotent startup for placement queue loop.
    Must be called by orchestrator.
    """
    global _QUEUE_THREAD

    if _QUEUE_THREAD and _QUEUE_THREAD.is_alive():
        return

    t = threading.Thread(
        target=_placement_queue_loop,
        name="PlacementQueueLoop",
        daemon=True,
    )
    t.start()

    _QUEUE_THREAD = t
    print("[PLACEMENT] queue loop started")
