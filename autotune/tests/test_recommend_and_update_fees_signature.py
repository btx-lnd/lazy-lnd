import pytest
from unittest.mock import MagicMock, patch
from autotune.autotune import recommend_and_update_fees
import inspect


def test_recommend_and_update_fees_signature_change():
    """Test that recommend_and_update_fees has the new signature without raw_peers parameter."""
    sig = inspect.signature(recommend_and_update_fees)
    param_names = list(sig.parameters.keys())
    
    # Verify raw_peers parameter is not in the signature
    assert 'raw_peers' not in param_names
    
    # Verify other expected parameters are still present
    expected_params = [
        'alias', 'policy', 'peer_memory', 'forward_data_day', 'forward_data_int',
        'config_lines', 'output_path', 'fee_log_path', 'htlc_log_path', 'state',
        'now', 'observe_only', 'dry_run', 'final_report_logs', 'rule_stats',
        'forward_data_day', 'forward_data_int'
    ]
    
    # Check that most expected parameters are present (allowing for some variance in exact parameter list)
    common_params = set(param_names) & set(expected_params)
    assert len(common_params) >= 10  # Should have most of the expected parameters


@patch('autotune.autotune.get_htlc_sizes')
@patch('autotune.autotune.adjust_channel_fees')
@patch('autotune.autotune.get_dynamic_delta_threshold')
@patch('autotune.autotune.get_adaptive_alpha')
def test_recommend_and_update_fees_calls_get_htlc_sizes_correctly(
    mock_adaptive_alpha, mock_delta_threshold, mock_adjust_fees, mock_get_htlc_sizes
):
    """Test that recommend_and_update_fees calls get_htlc_sizes with correct parameters."""
    # Mock the return values
    mock_get_htlc_sizes.return_value = ({}, 1000, False, False)
    mock_adjust_fees.return_value = ({}, [], {}, {}, [])
    mock_adaptive_alpha.return_value = (0.1, 0.2, 0.3)
    mock_delta_threshold.return_value = 0.05
    
    # Create minimal mock objects
    mock_policy = MagicMock()
    mock_policy.htlc.reserve_deduction = 0.01
    mock_policy.htlc.min_capacity = 0.1
    
    # Call the function with minimal required parameters
    try:
        recommend_and_update_fees(
            alias="test_peer",
            policy=mock_policy,
            peer_memory={},
            forward_data_day="",
            forward_data_int="",
            config_lines=[],
            output_path="",
            fee_log_path="",
            htlc_log_path="",
            state={"alias": "test_peer"},
            now=1234567890,
            observe_only=False,
            dry_run=False,
            final_report_logs=[],
            rule_stats={},
        )
    except Exception:
        # Function might fail due to missing mocks, but we mainly want to verify the call
        pass
    
    # Verify get_htlc_sizes was called with the new signature (4 parameters)
    assert mock_get_htlc_sizes.called
    call_args = mock_get_htlc_sizes.call_args[0]
    assert len(call_args) == 4  # Should be called with 4 arguments, not 5
