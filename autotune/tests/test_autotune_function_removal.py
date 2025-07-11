import pytest
import autotune


def test_get_peers_function_removed():
    """Test that get_peers function has been removed from autotune module."""
    # Verify that get_peers is no longer available in the autotune module
    assert not hasattr(autotune, 'get_peers')
    
    # Verify that attempting to import get_peers raises ImportError
    with pytest.raises(ImportError):
        from autotune import get_peers


def test_get_htlc_sizes_new_signature():
    """Test that get_htlc_sizes has the new signature without raw_peers parameter."""
    from autotune import get_htlc_sizes
    import inspect
    
    # Verify the function exists and has 4 parameters (excluding raw_peers)
    sig = inspect.signature(get_htlc_sizes)
    param_names = list(sig.parameters.keys())
    
    assert len(param_names) == 4
    assert param_names == ['section', 'alias', 'reserve_deduction', 'htlc_min_capacity']
    assert 'raw_peers' not in param_names