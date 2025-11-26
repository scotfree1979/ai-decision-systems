def should_force_exit_early(self, signal, current_odds):
    entry_odds = signal.get("odds")
    ticks_moved = self.get_tick_difference(entry_odds, current_odds)

    if ticks_moved >= 5:
        print(f"⚠️ Exiting early: Moved {ticks_moved} ticks from {entry_odds} to {current_odds}")
        return True

    return False
