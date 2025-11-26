from upgrade.upgrade_20250525 import place_basic_lay_bet, place_ladder_lay_bet

# This module safely re-exports patched bet functions to avoid circular import issues
# Use this module from inside bot files like og_head_trader_bot.py

__all__ = ["place_basic_lay_bet", "place_ladder_lay_bet"]
