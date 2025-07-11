import pytest
from unittest.mock import MagicMock
from autotune.rule_engine import rule_f2_tap_surge_boost, Context, RuleResult
from autotune.policy_utils import Policy


def test_rule_f2_tap_surge_boost_missing_alias_in_channels():
    """Test that rule_f2_tap_surge_boost handles missing alias gracefully."""
    # Create a mock policy with empty channels dict
    mock_policy = MagicMock(spec=Policy)
    mock_policy.channels = {}
    
    # Create context with alias that doesn't exist in channels
    ctx = Context(
        alias="nonexistent_peer",
        vol=1000,
        vol_int=500,
        revenue=100,
        revenue_int=50,
        fee=0,
        inbound_fee=100,
        ema_5d=0.8,
        ema_24h=0.9,
        ema_delta=0.06,  # > 0.05 threshold
        ema_blended=1.0,
        percentage_outbound=0.3,
        policy=mock_policy,
        skip_outbound_fee_adjust=False,
        skip_inbound_fee_adjust=False,
    )
    
    # Should not raise KeyError, should handle missing alias gracefully
    result = rule_f2_tap_surge_boost(ctx)
    
    # Should return the rule result with channel_inbound_fee defaulting to 0
    assert isinstance(result, RuleResult)
    assert result.rule_name == "F2_tap_surge_boost"
    assert result.inbound_fee == ctx.inbound_fee  # Should use ctx.inbound_fee since channel_inbound_fee=0


def test_rule_f2_tap_surge_boost_with_existing_alias():
    """Test that rule works normally when alias exists in channels."""
    # Create a mock policy with channels dict containing the alias
    mock_policy = MagicMock(spec=Policy)
    mock_policy.channels = {
        "existing_peer": {"inbound_fee_ppm": 50}
    }
    
    ctx = Context(
        alias="existing_peer",
        vol=1000,
        vol_int=500,
        revenue=100,
        revenue_int=50,
        fee=0,
        inbound_fee=100,
        ema_5d=0.8,
        ema_24h=0.9,
        ema_delta=0.06,
        ema_blended=1.0,
        percentage_outbound=0.3,
        policy=mock_policy,
        skip_outbound_fee_adjust=False,
        skip_inbound_fee_adjust=False,
    )
    
    result = rule_f2_tap_surge_boost(ctx)
    
    assert isinstance(result, RuleResult)
    assert result.rule_name == "F2_tap_surge_boost"
    # Should use the minimum of ctx.inbound_fee and channel_inbound_fee
    assert result.inbound_fee == min(ctx.inbound_fee, 50)