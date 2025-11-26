def preoff_loop(risk_manager, signal_queue):
    print("🔄 Pre-off loop started, waiting for signals...")
    
    while True:
        signal = signal_queue.get()
        print(f"📡 Signal received: {signal}")

        marketId, selectionId, odds, stake, num_runners, signal_type = parse_signal(signal)
        approved = risk_manager.approve_trade(
            bot_name="PreOffBot",
            marketId=marketId,
            selectionId=selectionId,
            odds=odds,
            stake=stake,
            num_runners=num_runners,
            signal_type=signal_type
        )

        if approved:
            print(f"✅ Trade approved: Market {marketId}, Odds {odds}")
            execute_trade(marketId, selectionId, odds, stake)
        else:
            print(f"❌ Trade rejected: Market {marketId}, Odds {odds}")

        time.sleep(2)
