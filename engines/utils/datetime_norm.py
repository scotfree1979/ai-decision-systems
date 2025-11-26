# engines/utils/datetime_norm.py  (new)
from datetime import datetime, timezone

def to_iso_utc(s: str | None) -> str:
    if not s:
        # never fabricate local time; let callers decide the default
        return ""
    t = s.strip()
    if "T" not in t:
        t = t.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(t.replace("Z","+00:00"))
    except ValueError:
        # last-ditch: if all we got is YYYY-MM-DD, make it 00:00Z
        if len(t) == 10 and t.count("-") == 2:
            dt = datetime.fromisoformat(t+"T00:00:00+00:00")
        else:
            raise
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=timezone.utc).isoformat(timespec="seconds").replace("+00:00","Z")
