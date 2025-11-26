from datetime import datetime, timezone

def now_utc():
    """Return timezone-aware UTC now()."""
    return datetime.now(timezone.utc)

def calculate_minutes_to_post(start_time_str: str) -> float:
    """
    Convert marketStartTime (ISO string) to minutes until off.
    Returns a positive/negative float (minutes), 1-dp resolution.
    """
    try:
        start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
        minutes_to_post = (start_time - now_utc()).total_seconds() / 60.0
        return round(minutes_to_post, 1)
    except Exception as e:
        print(f"⚠️ Failed to calculate minutes_to_post: {e}")
        return 999.0

# Alias used elsewhere
def minutes_to_off(start_time_str: str) -> float:
    return calculate_minutes_to_post(start_time_str)
