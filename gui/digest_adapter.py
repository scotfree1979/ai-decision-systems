#!/usr/bin/env python3
# Digest Adapter — Canonical realised P&L bridge for dashboard

import json
import ast
import os
import datetime
from collections import defaultdict

DIGEST_PATH = "data/reports/digest_context_latest.json"

# ---------------------------------------------------------------------
def _load_digest_rows():
    """
    Returns list of rows:
      {
        letter, venue, country, fav_rank, mto_band,
        pnl, ts (optional)
      }
    """
    if not os.path.exists(DIGEST_PATH):
        return []

    with open(DIGEST_PATH) as f:
        raw = json.load(f)

    rows = []
    for k, v in raw.items():
        try:
            key = ast.literal_eval(k)
            if len(key) != 5:
                continue

            letter, venue, country, fav_rank, mto = key

            rows.append({
                "letter": letter,
                "venue": venue,
                "country": country,
                "fav_rank": fav_rank,
                "mto_band": mto,
                "pnl": float(v or 0.0),
            })
        except Exception:
            continue

    return rows


# ---------------------------------------------------------------------
def digest_pnl_total(days: int | None = None) -> float:
    """
    Total realised P&L.
    days=None ⇒ full history
    """
    rows = _load_digest_rows()
    return round(sum(r["pnl"] for r in rows), 2)


# ---------------------------------------------------------------------
def digest_by_letter(days: int | None = None):
    """
    Returns:
      { letter: { pnl, trades } }
    """
    rows = _load_digest_rows()
    out = defaultdict(lambda: {"pnl": 0.0, "trades": 0})

    for r in rows:
        out[r["letter"]]["pnl"] += r["pnl"]
        out[r["letter"]]["trades"] += 1

    return {
        k: {
            "pnl": round(v["pnl"], 2),
            "trades": v["trades"]
        }
        for k, v in out.items()
    }


# ---------------------------------------------------------------------
def digest_by_engine(engine_letter_map: dict[str, list[str]]):
    """
    Map digest letters → engines.

    engine_letter_map example:
      {
        "LEGACY": ["A","B","F","G","L","P","R","S","X"],
        "MSC_RISK": ["M"],
      }

    Returns:
      {
        engine: {
          realised_pnl,
          trades
        }
      }
    """
    by_letter = digest_by_letter()

    out = {}
    for engine, letters in engine_letter_map.items():
        pnl = 0.0
        trades = 0
        for L in letters:
            if L in by_letter:
                pnl += by_letter[L]["pnl"]
                trades += by_letter[L]["trades"]

        out[engine] = {
            "realised_pnl": round(pnl, 2),
            "trades": trades,
        }

    return out
