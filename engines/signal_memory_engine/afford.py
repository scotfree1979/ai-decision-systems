from signal_memory_engine.risk.estimate_liability import estimate_liability
from signal_memory_engine.risk.current_liability import get_current_liability


def can_afford_bet(self, market_id, selection_id, odds, stake, side):
    new_liability = estimate_liability(odds, stake, side)
    current_liability = get_current_liability(self, market_id, selection_id)
    remaining_balance = self.current_balance - current_liability
    return new_liability <= remaining_balance



def get_adaptive_stake(self):
    try:
        # 🧠 Find the last unmatched signal if available
        if not self.unmatched_signals:
            print("⚠️ No unmatched signals available. Returning default stake.")
            return self.current_stake

        # Get the most recent unmatched signal (latest timestamp across all keys)
        latest = None
        latest_key = None
        for unmatched_key, signals in self.unmatched_signals.items():
            for s in signals:
                if not latest or s["timestamp"] > latest["timestamp"]:
                    latest = s
                    latest_key = unmatched_key

        signal = latest or {}
        market_id = latest_key[0] if latest_key else None
        selection_id = latest_key[1] if latest_key else None

        confidence = signal.get("confidence", 0.0)
        blueprint_match = signal.get("blueprint_match", None)
        range_low = signal.get("range_low", 0)
        range_high = signal.get("range_high", 999)
        tick_pattern = signal.get("tick_pattern", "flat")
        forced = signal.get("forced", False)

        base_stake = 10.00

        if forced:
            print(f"🔒 Forced signal: £10 stake.")
            return base_stake

        odds = signal.get("odds", 0)
        if not isinstance(odds, (float, int)) or odds < 1.01:
            print(f"⚠️ Invalid odds {odds}. Using £2 fallback.")
            return 2.00

        if market_id and selection_id:
            if not self.can_place_more_bets(market_id, selection_id):
                print(f"⚠️ Max bets on runner {selection_id}. Using £4 reduced stake.")
                return 4.00

        if confidence < 0.4:
            print(f"📉 Low confidence {confidence}. Using £4 stake.")
            return 4.00

        if not blueprint_match:
            print(f"❌ No blueprint match. Using £6 stake.")
            return 6.00

        # 🧠 Live intraday P&L-based adjustment
        if blueprint_match in self.live_pattern_pnl:
            stats = self.live_pattern_pnl.get(blueprint_match, {})
            trades = stats.get("trades", 0)
            pnl = stats.get("net_pnl", 0.0)

            # 🧪 Let cold blueprints run as normal
            if trades < 3:
                print(f"🧪 Cold blueprint {blueprint_match} – No stake penalty (trades={trades})")
                return base_stake

            # 🪙 If confidence or profit is low, reduce stake only (no blocking)
            if pnl < 5 or confidence < 0.68:
                print(f"⚠️ Reduced stake for {blueprint_match}: PnL £{pnl:.2f}, conf {confidence:.2f}")
                return 4.00

            # 🚀 Promote profitable blueprints (optional)
            if trades >= 10 and pnl >= 40 and confidence >= 0.75:
                print(f"📈 Pattern {blueprint_match} promoted: {trades} trades, £{pnl:.2f} PnL, {confidence:.2f} confidence. Stake: £14")
                return 14.00

            if pnl < -20:
                print(f"🔻 Live pattern {blueprint_match} down £{pnl:.2f}. Capping stake at £2.")
                return 2.00
            elif pnl > 30:
                print(f"🚀 Live pattern {blueprint_match} up £{pnl:.2f}. Boosting stake to £12.")
                return 12.00

        if odds > 12.0:
            print(f"💥 High odds {odds}. Using £6 stake.")
            return 6.00

        range_size = range_high - range_low if range_high and range_low else 999
        if range_size <= 1.5 and tick_pattern in ["came_in", "reversed"]:
            print(f"🚀 Tight range and aggressive pattern. Boosted stake to £12.")
            return 12.00

        print(f"✅ Standard case. Using base stake £10.")
        return base_stake

    except Exception as e:
        print(f"💥 Error in get_adaptive_stake: {e}")
        return 2.00
