"""Trend Analyzer package: authoritative pre-plan direction service.

Exports:
- direction_for: primary entry point returning DirectionDecision
- load_config: helper to override defaults
"""
from .config import load_config, DEFAULT_CONFIG
from .direction_service import direction_for
from .types import DirectionDecision, MarketTick, MarketView, AnalyzerConfig

__all__ = [
    "direction_for",
    "load_config",
    "DEFAULT_CONFIG",
    "DirectionDecision",
    "MarketTick",
    "MarketView",
    "AnalyzerConfig",
]
