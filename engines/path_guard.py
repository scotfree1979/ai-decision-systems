# engines/path_guard.py
import os

def ensure_parent(path: str) -> None:
    """
    Ensure the directory that will contain `path` exists.
    Safe to call repeatedly.
    """
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)
