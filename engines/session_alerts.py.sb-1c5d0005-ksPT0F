# 🛡 Risk Manager Centralized Rules & Integration

def risk_manager(trade_request, global_exposure_limit=50):
    """
    Centralized risk assessment and approval system for trade requests.

    Parameters:
    - trade_request: dict containing details about the proposed trade (stake, liability, current exposure).
    - global_exposure_limit: float, maximum allowable exposure per market.

    Returns:
    - Dict indicating whether the trade is approved and the reason for decision.
    """
    current_exposure = trade_request['current_exposure']
    proposed_liability = trade_request['liability']

    if (current_exposure + proposed_liability) > global_exposure_limit:
        return {
            'Approved': False,
            'Reason': 'Exposure limit exceeded',
            'Allowed Exposure': global_exposure_limit - current_exposure
        }
    else:
        return {
            'Approved': True,
            'Reason': 'Within exposure limits',
            'Remaining Exposure': global_exposure_limit - (current_exposure + proposed_liability)
        }

# Example usage:
# trade_request_example = {'stake': 10, 'liability': 20, 'current_exposure': 25}
# approval = risk_manager(trade_request_example)
# print(approval)
