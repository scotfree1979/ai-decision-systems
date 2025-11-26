import logging
from budget_manager import BudgetManager

# Setup logging clearly
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Mock RiskManager class just for this test
class MockRiskManager:
    def can_accept_liability(self, bot_type, liability):
        # Allow all liabilities for testing purposes
        return True

# Headers with your actual Betfair session token and app key
headers = {
    'X-Application': 'CZHojduNWa3kxWIn',
    'X-Authentication': 'igIGz4BV+NSvGOUdUk3DFem/97Bmpmuzto0Q2yansP0=',
    'Content-Type': 'application/json',
    'Accept': 'application/json'
}

# Create a test instance of BudgetManager
risk_mgr = MockRiskManager()
budget_mgr = BudgetManager(risk_manager=risk_mgr, headers=headers)

# Example test signal explicitly defined
test_signal = {
    'marketId': '1.23456789',
    'botName': 'OGHeadTrader',
    'stake': 2.0,
    'odds': 3.5,
    'botType': 'pre_off'
}

# Simulate rejection reason due to budget
rejection_reason = "Max market liability reached"

# Explicitly test escalation logic
result_signal = budget_mgr.handle_rejected_signal(test_signal, rejection_reason)

if result_signal:
    print(f"✅ Escalation successful: {result_signal}")
else:
    print("🚫 Escalation rejected or failed.")
