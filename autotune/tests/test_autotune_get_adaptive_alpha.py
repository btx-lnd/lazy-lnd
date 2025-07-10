import pytest
from datetime import datetime, timedelta
from autotune import get_adaptive_alpha

@pytest.fixture
def alpha_params():
    # All values unique for branch-disambiguation
    return {
        "role_flip_days": 3,
        "min_role_flips": 2,
        "zero_ema_trigger": 1,
        "weighted_1d": 0.11,
        "weighted_5d": 0.22,
        "weighted_7d": 0.33,
        "zero_ema_max_1d": 0.2,
        "zero_ema_max_5d": 0.3,
        "zero_ema_max_7d": 0.4,
        "zero_ema_1d_boost": 0.02,
        "zero_ema_5d_boost": 0.03,
        "zero_ema_7d_boost": 0.04,
        "fee_bump_streak_threshold": 2,
        "fee_bump_min_1d": 0.04,
        "fee_bump_min_5d": 0.05,
        "fee_bump_min_7d": 0.06,
        "fee_bump_decay_1d": 0.01,
        "fee_bump_decay_5d": 0.02,
        "fee_bump_decay_7d": 0.03,
        "balanced_1d": 0.07,
        "balanced_5d": 0.08,
        "balanced_7d": 0.09,
    }

def test_returns_weighted_when_recent_role_flip(alpha_params):
    section = {
        "days_since_flip": 2,
        "role_flips": [1, 2],
        "fee_bump_streak": 0,
        "zero_ema_count": 0,
    }
    result = get_adaptive_alpha(section, alpha_params)
    assert result == (0.11, 0.22, 0.33)

def test_returns_zero_ema_branch(alpha_params):
    section = {
        "days_since_flip": 10,
        "role_flips": [1, 2, 3],
        "fee_bump_streak": 0,
        "zero_ema_count": 2,  # >= trigger
    }
    # zero_ema branch: min(max, balanced + boost)
    expected = (
        min(alpha_params["zero_ema_max_1d"], alpha_params["balanced_1d"] + alpha_params["zero_ema_1d_boost"]),
        min(alpha_params["zero_ema_max_5d"], alpha_params["balanced_5d"] + alpha_params["zero_ema_5d_boost"]),
        min(alpha_params["zero_ema_max_7d"], alpha_params["balanced_7d"] + alpha_params["zero_ema_7d_boost"]),
    )
    assert get_adaptive_alpha(section, alpha_params) == expected

def test_returns_fee_bump_decay_branch(alpha_params):
    section = {
        "days_since_flip": 5,
        "role_flips": [1, 2, 3],
        "fee_bump_streak": 2,  # >= threshold
        "zero_ema_count": 0,
    }
    # fee_bump_decay branch: max(min, balanced - decay)
    expected = (
        max(alpha_params["fee_bump_min_1d"], alpha_params["balanced_1d"] - alpha_params["fee_bump_decay_1d"]),
        max(alpha_params["fee_bump_min_5d"], alpha_params["balanced_5d"] - alpha_params["fee_bump_decay_5d"]),
        max(alpha_params["fee_bump_min_7d"], alpha_params["balanced_7d"] - alpha_params["fee_bump_decay_7d"]),
    )
    assert get_adaptive_alpha(section, alpha_params) == expected

def test_returns_default_balanced_branch(alpha_params):
    section = {
        "days_since_flip": 5,
        "role_flips": [1],
        "fee_bump_streak": 0,
        "zero_ema_count": 0,
    }
    assert get_adaptive_alpha(section, alpha_params) == (0.07, 0.08, 0.09)

def test_weighted_branch_requires_min_role_flips(alpha_params):
    section = {
        "days_since_flip": 1,
        "role_flips": [1],  # Not enough
        "fee_bump_streak": 0,
        "zero_ema_count": 0,
    }
    # Not enough role_flips, should default
    assert get_adaptive_alpha(section, alpha_params) == (0.07, 0.08, 0.09)

def test_zero_ema_branch_clamped_by_max(alpha_params):
    params = alpha_params.copy()
    params["balanced_1d"] = 0.19
    params["zero_ema_1d_boost"] = 0.02
    params["zero_ema_max_1d"] = 0.2  # Clamp applies
    section = {
        "days_since_flip": 100,
        "role_flips": [1, 2, 3],
        "fee_bump_streak": 0,
        "zero_ema_count": 10,
    }
    # 0.19+0.02=0.21 > 0.2 → clamped
    assert get_adaptive_alpha(section, params)[0] == 0.2

def test_fee_bump_decay_branch_clamped_by_min(alpha_params):
    params = alpha_params.copy()
    params["balanced_1d"] = 0.04
    params["fee_bump_decay_1d"] = 0.05
    params["fee_bump_min_1d"] = 0.03
    section = {
        "days_since_flip": 100,
        "role_flips": [1, 2, 3],
        "fee_bump_streak": 9,
        "zero_ema_count": 0,
    }
    # 0.04-0.05=-0.01 < min=0.03 → clamped to 0.03
    assert get_adaptive_alpha(section, params)[0] == 0.03
