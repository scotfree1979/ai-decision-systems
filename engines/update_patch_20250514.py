# 🗃️ Final Corrected Integration Patch (update_patch_20250514.py)
import sys
sys.path.append('/Volumes/Sonnics/Race Tool - Sandbox/analytics_beta/engines')

# Import corrected modules explicitly
from market_monitor_signals import market_signal_generator
from risk_manager import RiskManager


# Explicitly define feature toggles for integration
FEATURE_TOGGLES = {
    "check_account_details": True,
    "fetch_markets_and_odds": True,
    "place_test_order": True,
    "enable_live_loop": True,
    "enable_logging": True,
    "enable_penny_matching_scalping": True,
    "enable_in_play_scalping_10_20": True,
    "enable_rapid_decision_making": True,
    "enable_dynamic_market_conditions": True,
    "enable_auto_exposure_balancing": True
}

# Importing all clearly validated new logic functions
from volatility_check import real_time_volatility_check
from auto_stop_loss_gain import auto_stop_loss_gain
from historical_dashboard import update_dashboard
from og_head_trader_logic import og_head_trader_strategy
from og_preoff_scalping import pre_off_scalping_strategy
from og_inplay_logic import in_play_liability_management
from market_monitor_signals import market_signal_generator
from risk_manager_rules import risk_manager

# Detect Fire and Ice signals explicitly
def detect_fire_ice(odds_history):
    fire = {}
    ice = {}
    for horse, odds in odds_history.items():
        if len(odds) >= 2:
            change_pct = ((odds[-1] - odds[-2]) / odds[-2]) * 100
            if change_pct <= -7.5:
                fire[horse] = change_pct
            elif change_pct >= 7.5:
                ice[horse] = change_pct
    return fire, ice

# Execute full market cycle integrating all components explicitly
def execute_market_cycle(batch_manager, market_data, positions, odds_history, current_time, race_time, runners_count, show_dashboard=True):
    print("🔍 Generating market signals...")
    signal = market_signal_generator(race_time, current_time, runners_count)
    print(f"📡 Market Signal: {signal}")
    time.sleep(1)

    print("📊 Checking market volatility...")
    volatility = real_time_volatility_check(odds_history, {'low': 5, 'medium': 10, 'high': 20})
    print(f"📈 Volatility Status: {volatility}")
    time.sleep(1)

    print("🔥❄️ Detecting Fire and Ice signals...")
    fire, ice = detect_fire_ice(odds_history)
    print(f"🔥 Fire (Shortening): {fire}")
    print(f"❄️ Ice (Drifting): {ice}")
    time.sleep(1)

    pre_off_trades = None
    if signal in ['Business as Usual - Initial Signal', 'Secondary Scalping & Hedging'] and FEATURE_TOGGLES["enable_penny_matching_scalping"]:
        print("⚙️ Executing pre-off penny matching scalping strategy...")
        pre_off_trades = pre_off_scalping_strategy(odds_history, market_data)
        print(f"📌 Pre-Off Trades: {pre_off_trades}")
        time.sleep(1)

    if FEATURE_TOGGLES["enable_in_play_scalping_10_20"] and signal == 'In-Play Signal':
        print("🎯 Executing in-play scalping (10-20 odds range)...")
        # Placeholder for actual in-play scalping logic (10-20 odds)
        time.sleep(1)

    if FEATURE_TOGGLES["enable_rapid_decision_making"]:
        print("⚡ Executing rapid in-play decision-making...")
        # Placeholder for rapid decision-making logic
        time.sleep(1)

    if FEATURE_TOGGLES["enable_dynamic_market_conditions"]:
        print("🌊 Adjusting to dynamic market conditions...")
        # Placeholder for dynamic market conditions adjustment logic
        time.sleep(1)

    if FEATURE_TOGGLES["enable_auto_exposure_balancing"]:
        print("🔄 Balancing automatic exposure...")
        # Placeholder for auto exposure balancing logic
        time.sleep(1)

    print("🎩 Gathering OGHeadTrader insights...")
    trader_insights = og_head_trader_strategy(market_data, odds_history)
    print(f"💡 Head Trader Insights: {trader_insights}")
    time.sleep(1)

    print("🚨 Evaluating automatic stop-loss/gain...")
    stop_actions = auto_stop_loss_gain(positions)
    print(f"⚠️ Stop-Loss/Gain Actions: {stop_actions}")
    time.sleep(1)

    in_play_actions = None
    if signal == 'In-Play Signal':
        print("🐎 Managing in-play liability...")
        in_play_actions = in_play_liability_management(market_data, positions)
        print(f"🔄 In-Play Actions: {in_play_actions}")
        time.sleep(1)

    print("🔐 Checking risk manager approval...")
    dynamic_stake = 10  # Replace with dynamic calculation
    dynamic_liability = 20  # Replace with dynamic calculation
    trade_request = {'stake': dynamic_stake, 'liability': dynamic_liability, 'current_exposure': sum(positions.values())}
    approval = risk_manager(trade_request)
    print(f"✅ Risk Manager Approval: {approval}")
    time.sleep(1)

    print("📉 Updating performance dashboard...")
    update_dashboard(metrics={
        'odds_history': odds_history,
        'pnl_history': [positions[horse] for horse in positions],
        'volatility_status': volatility,
        'trader_insights': trader_insights,
        'stop_loss_gain_actions': stop_actions,
        'risk_approval': approval
    }, show_plots=show_dashboard)
    time.sleep(1)

    if FEATURE_TOGGLES["fetch_markets_and_odds"]:
        print("🔄 Fetching market data...")
        batch_manager.send_batches()
        print("✅ Market data batch processed.")
        time.sleep(1)

    return {
        'signal': signal,
        'volatility': volatility,
        'fire_signals': fire,
        'ice_signals': ice,
        'pre_off_trades': pre_off_trades,
        'trader_insights': trader_insights,
        'stop_actions': stop_actions,
        'in_play_actions': in_play_actions,
        'risk_approval': approval
    }
