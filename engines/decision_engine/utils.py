def fnum(x, default=0.0):
    try:
        y = float(x)
        if y != y:  # NaN
            return float(default)
        return y
    except Exception:
        return float(default)
def f_or(v, d=0.0):
    try:
        if v is None: return d
        x = float(v)
        if x != x: return d  # NaN
        return x
    except Exception:
        return d

def i_or(v, d=0):
    try:
        if v is None: return d
        return int(v)
    except Exception:
        try: return int(float(v))
        except Exception: return d
def f_or(v, d=0.0):
    try:
        if v is None: return d
        x = float(v)
        if x != x: return d  # NaN
        return x
    except Exception:
        return d

def i_or(v, d=0):
    try:
        if v is None: return d
        return int(v)
    except Exception:
        try: return int(float(v))
        except Exception: return d
