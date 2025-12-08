# === PATCH START ============================================================
# 📍 NEW FILE: engines/bus/tests/test_stoploss_pipeline.py
# 📆 PATCHED: 2026-02-14
# ============================================================================

def test_stoploss_pipeline(mid, sid):
    from engines.live.overwatcher import inject_fake_stoploss
    from engines.bus.bus import BUS
    print("=== STOPLOSS PIPELINE TEST ===")

    inject_fake_stoploss(mid, sid, px=4.6, stake=2)

    BUS.tick()

    print("Stoploss pipeline executed.")

# === PATCH END ================================================================
