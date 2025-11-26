# engines/pnl_engine.py
from .canonical_pnl import calc_pnl_context  # <- your new aggregator

def calc_digest_pnl(day=None):
    """Return unified totals for digest & dashboard."""
    ctx = calc_pnl_context(day)
    total = round(sum(ctx.values()), 2)
    per_letter = {}
    for (letter, *rest), pnl in ctx.items():
        per_letter[letter] = per_letter.get(letter, 0.0) + pnl
    return total, per_letter
