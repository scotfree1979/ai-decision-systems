# engines/odds/odds_service_state.py
from __future__ import annotations
from typing import Dict, List
from engines.market_monitor import monitor

class _OddServiceState:
    """
    Minimal state layer expected by BUS.tick():
        • refresh(mids)  → update internal cache
        • state_for(mid) → return {
                                "active_sids": [...],
                                "passive_sids": [...],
                             }
    All runner classification (band, fav) comes from market_monitor.
    """

    def __init__(self):
        self._cache: Dict[str, Dict[str, List[str]]] = {}

    # ------------------------------------------------------------
    # BUS calls this every tick
    # ------------------------------------------------------------
    def refresh(self, mids: List[str]) -> None:
        """
        Ensure monitor.refresh() has current state,
        then convert monitor state → BUS runner map.
        """
        try:
            # Ensure monitor has fresh snapshot for these markets
            monitor.ensure_for_markets(mids)
            monitor.refresh(mids)
        except Exception as e:
            print(f"[ODD_SERVICE_STATE] monitor refresh warn: {e}")

        # Build ACTIVE/PASSIVE sets per market
        new_cache: Dict[str, Dict[str, List[str]]] = {}

        for mid in mids:
            st = monitor.get_market_state(mid) or {}
            runners = st.get("runners") or {}

            active = []
            passive = []

            for sid, info in runners.items():
                band = (info.get("band") or "").upper()
                if band == "ACTIVE":
                    active.append(sid)
                elif band == "PASSIVE":
                    passive.append(sid)

            new_cache[mid] = {
                "active_sids": active,
                "passive_sids": passive,
            }

        self._cache = new_cache

    # ------------------------------------------------------------
    def state_for(self, mid: str) -> Dict[str, List[str]]:
        """Return cached ACTIVE/PASSIVE lists for this market."""
        return self._cache.get(str(mid), {})
        

# Global singleton — BUS imports this
ODD_SERVICE_STATE = _OddServiceState()
