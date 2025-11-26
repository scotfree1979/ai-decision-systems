# === PATCH START ===
# 📍 TARGET: engines/odds/features.py
# 🔎 SEARCH: (file does not exist)
# ⛏️ ACTION: create file
from __future__ import annotations
from typing import Deque, Dict, Tuple, List
from collections import deque
import math, time

# Ring buffers live in odds_service; we only compute here.

def slope_ppm(samples: List[Tuple[float,float]]) -> float:
    """
    Simple least-squares slope per minute on (ts, ltp) pairs. ts in seconds.
    """
    n = len(samples)
    if n < 3:
        return 0.0
    # Normalize time to minutes relative to first sample
    t0 = samples[0][0]
    xs = [(ts - t0)/60.0 for ts,_ in samples]
    ys = [px for _,px in samples]
    xbar = sum(xs)/n
    ybar = sum(ys)/n
    num = sum((xs[i]-xbar)*(ys[i]-ybar) for i in range(n))
    den = sum((xs[i]-xbar)**2 for i in range(n)) or 1.0
    return num/den

def tick_velocity(samples: List[Tuple[float,float]], window_s: float, up_only: bool=True) -> int:
    """
    Count of up (or down if up_only=False) ticks in the last window_s.
    """
    if not samples:
        return 0
    cutoff = samples[-1][0] - window_s
    vals = [px for (ts,px) in samples if ts >= cutoff]
    if len(vals) < 2:
        return 0
    cnt = 0
    for i in range(1,len(vals)):
        if up_only and vals[i] > vals[i-1]:
            cnt += 1
        if not up_only and vals[i] < vals[i-1]:
            cnt += 1
    return cnt

def ranks_by_ltp(odds_map: Dict[str,float]) -> Dict[str,int]:
    """
    1 = favourite (lowest odds). ties stable-sorted by selectionId.
    """
    sids = sorted(odds_map.items(), key=lambda kv: (float(kv[1]), str(kv[0])))
    return {sid: i+1 for i,(sid,_) in enumerate(sids)}

def market_breadth(m_now: Dict[str,float], m_then: Dict[str,float]) -> float:
    """
    Fraction of runners moving in the same direction as the max mover.
    +1 if drifting, -1 if steaming. Returns 0..1 for agreement fraction.
    """
    if not m_now or not m_then:
        return 0.0
    # compute delta
    deltas = {}
    for sid, nowp in m_now.items():
        thenp = m_then.get(sid, nowp)
        deltas[sid] = float(nowp) - float(thenp)
    if not deltas:
        return 0.0
    # pick dominant sign by absolute move
    sid_star = max(deltas.items(), key=lambda kv: abs(kv[1]))[0]
    dominate_sign = 1.0 if deltas[sid_star] > 0.0 else -1.0
    agree = sum(1 for v in deltas.values() if (v>0 and dominate_sign>0) or (v<0 and dominate_sign<0))
    return float(agree) / max(1,len(deltas))

# --- Blueprint detectors (lightweight v1) ------------------------------------

def detect_steam_dominant(runner_feats: Dict[str,Dict], ranks_now: Dict[str,int], breadth: float) -> Dict[str,Tuple[float,str]]:
    """
    Returns per-runner score & reason for STEAM_DOMINANT on the mover (score 0..1).
    Strong negative slope & rank<=2 and market breadth drifting against the field.
    """
    out={}
    for sid, feat in runner_feats.items():
        s = float(feat.get("slope_ppm",0.0))
        rv1 = int(feat.get("tick_vel_1s_up",0))
        rv3 = int(feat.get("tick_vel_3s_up",0))
        rank = int(ranks_now.get(sid, 99))
        # steamer = negative slope (prices falling)
        if s < -0.025 and rank <= 2:
            # breadth drifting when many others go the other way (breadth near 1 ⇒ many agree with max mover).
            score = min(1.0, (abs(s)/0.06) * 0.6 + (1.0 - min(1.0, breadth))*0.4)
            if score >= 0.5:
                out[sid]=(score, f"sppm={s:.3f} rank={rank} breadth={breadth:.2f}")
    return out

def detect_broad_drift(runner_feats: Dict[str,Dict], ranks_now: Dict[str,int], breadth: float) -> Dict[str,Tuple[float,str]]:
    """
    Many runners gently drifting; prefer LAY->BACK on non-favs.
    """
    out={}
    # heuristic: breadth high indicates many moving same way; check avg slope
    avg_slope = sum(float(f.get("slope_ppm",0.0)) for f in runner_feats.values())/max(1,len(runner_feats))
    if avg_slope > 0.01 and breadth > 0.6:
        for sid, feat in runner_feats.items():
            rank = int(ranks_now.get(sid, 99))
            s = float(feat.get("slope_ppm",0.0))
            if s > 0 and rank > 1:
                score = min(1.0, 0.5 + min(0.5, (avg_slope/0.04)))
                out[sid]=(score, f"avg_slope={avg_slope:.3f} breadth={breadth:.2f}")
    return out

def detect_oscillation(runner_feats: Dict[str,Dict], ranks_now: Dict[str,int]) -> Dict[str,Tuple[float,str]]:
    """
    Ping-pong inside short prices: up and down ticks present, small net slope.
    """
    out={}
    for sid, feat in runner_feats.items():
        s = float(feat.get("slope_ppm",0.0))
        vup = int(feat.get("tick_vel_3s_up",0))
        # we don't have explicit down velocity; approximate by low vup & small |slope|
        if abs(s) < 0.01 and vup>=2 and ranks_now.get(sid,99)<=2:
            out[sid]=(0.6, f"oscillation rank<=2 vup3={vup}")
    return out
# === PATCH END ===
