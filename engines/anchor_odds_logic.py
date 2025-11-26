# 🔑 Anchor Odds Integration (Final System Logic) - Complete and Corrected Anchor Odds Update

import sqlite3
from datetime import datetime, timedelta

# --- Explicit Logic to Capture Initial (Anchor) Odds ---

def capture_anchor_odds(
    marketId, selectionId, stake, odds, bet_type, placed_at, status, matched_liability,
    unmatched_liability, pnl, liability, result, timestamp, matched_timestamp, betfair_bet_id,
    horse_name, strategy_name, bot_name, date, horse, can_accept_liability, time_session,
    race_name, market_name, event_name, anchor_odd, odds_check_1, odds_check_2, odds_check_3,
    odds_check_4, odds_check_5, odds_check_6, customerOrderRef, bet_settled
):
    conn = sqlite3.connect('bets.db')
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE hourly_odds_snapshots SET
            anchor_odd=?, odds_check_1=?, odds_check_2=?, odds_check_3=?,
            odds_check_4=?, odds_check_5=?, odds_check_6=?, timestamp=?
        WHERE customerOrderRef=?
    ''', (
        anchor_odd, odds_check_1, odds_check_2, odds_check_3,
        odds_check_4, odds_check_5, odds_check_6, timestamp, customerOrderRef
    ))
    conn.commit()
    conn.close()

# --- Explicit Logic to Store and Manage Hourly Snapshots ---

def store_hourly_snapshot(customerOrderRef, snapshot_column, odds):
    conn = sqlite3.connect('bets.db')
    cursor = conn.cursor()
    cursor.execute(f'''
        UPDATE hourly_odds_snapshots
        SET {snapshot_column} = ?
        WHERE customerOrderRef = ?
    ''', (odds, customerOrderRef))
    conn.commit()
    conn.close()

# --- Explicit Logic to Retrieve Anchor Odds ---

def get_anchor_odds(customerOrderRef):
    conn = sqlite3.connect('bets.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT anchor_odd FROM hourly_odds_snapshots
        WHERE customerOrderRef = ?
        ORDER BY snapshot_time ASC LIMIT 1
    ''', (customerOrderRef,))

    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

# --- Explicit Logic to Retrieve Historical Snapshots ---

def get_historical_snapshots(customerOrderRef):
    conn = sqlite3.connect('bets.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT odds_check_1, odds_check_2, odds_check_3,
               odds_check_4, odds_check_5, odds_check_6
        FROM hourly_odds_snapshots
        WHERE customerOrderRef = ?
    ''', (customerOrderRef,))

    result = cursor.fetchone()
    conn.close()
    return result

# --- Integration with Market Monitor ---

def integrate_with_market_monitor(
    marketId, selectionId, stake, odds, bet_type, placed_at, status, matched_liability,
    unmatched_liability, pnl, liability, result, timestamp, matched_timestamp, betfair_bet_id,
    horse_name, strategy_name, bot_name, date, horse, can_accept_liability, time_session,
    race_name, market_name, event_name, anchor_odd, odds_check_1, odds_check_2, odds_check_3,
    odds_check_4, odds_check_5, odds_check_6, customerOrderRef, race_start_time
):
    snapshot_intervals = [7, 6, 5, 4, 3, 2, 1/60]  # hours before race
    snapshot_columns = ["anchor_odd", "odds_check_1", "odds_check_2", "odds_check_3", "odds_check_4", "odds_check_5", "odds_check_6"]

    odds_checks = {}

    for interval, column in zip(snapshot_intervals, snapshot_columns):
        snapshot_time = (race_start_time - timedelta(hours=interval)).strftime('%Y-%m-%d %H:%M:%S')
        current_odds = odds  # Integrate real-time fetching logic

        if column == "anchor_odd":
            capture_anchor_odds(
                marketId, selectionId, stake, odds, bet_type, placed_at, status, matched_liability,
                unmatched_liability, pnl, liability, result, timestamp, matched_timestamp, betfair_bet_id,
                horse_name, strategy_name, bot_name, date, horse, can_accept_liability, time_session,
                race_name, market_name, event_name, current_odds, 0, 0, 0, 0, 0, 0, customerOrderRef
            )
        else:
            odds_checks[column] = current_odds
            store_hourly_snapshot(customerOrderRef, column, current_odds)

# Example Integration Call:
# integrate_with_market_monitor("market_xyz", 123, 10, 5.2, "back", datetime.utcnow(), "placed", 0, 0, 0, 0, "", datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'), "", "bet123", "Horse A", "strategy", "botA", datetime.utcnow().strftime('%Y-%m-%d'), "Horse A", 1, "session", "Race 1", "Market 1", "Event 1", 5.2, 0, 0, 0, 0, 0, 0, "order_xyz", datetime.utcnow() + timedelta(hours=7))
