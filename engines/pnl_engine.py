# engines/pnl_engine.py
import os, json, datetime
from engines.canonical_pnl import calc_pnl_context  # the canonical aggregator you built

def calc_digest_pnl(day=None):
    """
    Returns unified total and per-letter P&L.
    """
    ctx = calc_pnl_context(day or 'yesterday')
    total = round(sum(ctx.values()), 2)
    per_letter = {}
    for (letter, *rest), pnl in ctx.items():
        per_letter[letter] = per_letter.get(letter, 0.0) + pnl
    return total, per_letter
