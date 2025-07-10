import pytest
from autotune import get_dynamic_delta_threshold


class DummyThresholds:
    def __init__(
        self,
        base_delta=0.2,
        role_flip_days=3,
        role_flip_bonus=0.05,
        high_ema_delta_threshold=100,
        high_rev_delta_threshold=10,
        high_delta_bonus=0.03,
        mid_streak_min=2,
        mid_streak_max=5,
        mid_streak_bonus=0.02,
        high_streak_bonus=0.04,
        early_streak_max=1,
        early_streak_penalty=0.01,
        zero_ema_count_threshold=2,
        zero_ema_penalty=0.06,
        min_delta=0.1,
        max_delta=0.5,
    ):
        self.base_delta = base_delta
        self.role_flip_days = role_flip_days
        self.role_flip_bonus = role_flip_bonus
        self.high_ema_delta_threshold = high_ema_delta_threshold
        self.high_rev_delta_threshold = high_rev_delta_threshold
        self.high_delta_bonus = high_delta_bonus
        self.mid_streak_min = mid_streak_min
        self.mid_streak_max = mid_streak_max
        self.mid_streak_bonus = mid_streak_bonus
        self.high_streak_bonus = high_streak_bonus
        self.early_streak_max = early_streak_max
        self.early_streak_penalty = early_streak_penalty
        self.zero_ema_count_threshold = zero_ema_count_threshold
        self.zero_ema_penalty = zero_ema_penalty
        self.min_delta = min_delta
        self.max_delta = max_delta


def base_section(**overrides):
    # Defaults that exercise only the base
    s = {
        "fee_bump_streak": 0,
        "days_since_flip": 999,
        "ema_delta": 0,
        "rev_delta": 0,
        "zero_ema_count": 0,
    }
    s.update(overrides)
    return s


def test_get_dynamic_delta_threshold_base_value():
    th = DummyThresholds(base_delta=0.2)
    s = base_section()
    assert get_dynamic_delta_threshold(s, th) == 0.2


def test_role_flip_bonus_applied():
    th = DummyThresholds()
    s = base_section(days_since_flip=2)
    val = get_dynamic_delta_threshold(s, th)
    assert val == round(th.base_delta - th.role_flip_bonus, 4)


def test_high_ema_delta_bonus_applied():
    th = DummyThresholds()
    s = base_section(ema_delta=200)
    val = get_dynamic_delta_threshold(s, th)
    assert val == round(th.base_delta - th.high_delta_bonus, 4)


def test_high_rev_delta_bonus_applied():
    th = DummyThresholds()
    s = base_section(rev_delta=20)
    val = get_dynamic_delta_threshold(s, th)
    assert val == round(th.base_delta - th.high_delta_bonus, 4)


def test_mid_streak_bonus_applied():
    th = DummyThresholds()
    s = base_section(fee_bump_streak=3)
    val = get_dynamic_delta_threshold(s, th)
    assert val == round(th.base_delta - th.mid_streak_bonus, 4)


def test_high_streak_bonus_applied():
    th = DummyThresholds()
    s = base_section(fee_bump_streak=6)
    val = get_dynamic_delta_threshold(s, th)
    assert val == round(th.base_delta - th.high_streak_bonus, 4)


def test_early_streak_penalty_applied():
    th = DummyThresholds()
    s = base_section(fee_bump_streak=1)
    val = get_dynamic_delta_threshold(s, th)
    assert val == round(th.base_delta + th.early_streak_penalty, 4)


def test_zero_ema_penalty_applied():
    th = DummyThresholds()
    s = base_section(zero_ema_count=2)
    val = get_dynamic_delta_threshold(s, th)
    assert val == round(th.base_delta + th.zero_ema_penalty, 4)


def test_all_bonuses_and_penalties_additive():
    th = DummyThresholds()
    s = base_section(
        days_since_flip=1, ema_delta=200, fee_bump_streak=3, zero_ema_count=2
    )
    # Should apply: -role_flip_bonus, -high_delta_bonus, -mid_streak_bonus, +zero_ema_penalty
    expected = (
        th.base_delta
        - th.role_flip_bonus
        - th.high_delta_bonus
        - th.mid_streak_bonus
        + th.zero_ema_penalty
    )
    assert get_dynamic_delta_threshold(s, th) == round(expected, 4)


def test_min_delta_enforced():
    th = DummyThresholds(min_delta=0.15)
    s = base_section(
        ema_delta=1_000_000,
        rev_delta=1_000_000,
        fee_bump_streak=99,
        days_since_flip=0,
        zero_ema_count=999,
    )
    # All bonuses penalise base way below min_delta
    assert get_dynamic_delta_threshold(s, th) == th.min_delta


def test_max_delta_enforced():
    th = DummyThresholds(max_delta=0.25)
    s = base_section()
    th.base_delta = 0.5  # deliberately over max_delta
    assert get_dynamic_delta_threshold(s, th) == th.max_delta
