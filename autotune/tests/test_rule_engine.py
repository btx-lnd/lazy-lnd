# autotune/tests/test_rule_engine.py

import pytest
import random
from types import SimpleNamespace
from unittest.mock import patch

from autotune import rule_engine
from autotune.policy_utils import Policy


# Dummy exponential fee bump helper for context
def dummy_bump(fee, streak, fees):
    inc = 2
    return (fee + inc, max(1, (fee + inc) // 2), inc)


# Minimal valid params for rules that reference PARAMS
MIN_PARAMS = {
    "rules": {
        "exempt_from_sink_guard": [],
        "inbound_fee_targets": ["testalias"],
    },
    "inbound_fees": {
        "min_fee_ppm": 10,
        "max_fee_ppm": 1000,
    },
    "fees": {
        "min_ppm": 0,
        "max_ppm": 2500,
        "bump_max": 250,
        "min_max_ratio": 0.5,
        "failed_htlc_bump": 25,
    },
    "channels": {
        "testalias": {
            "inbound_fee_ppm": 25
            }  
    },
    "thresholds": {},
    "node": {"lnd_container": "test", "name": "testrouter"},
}


def build_ctx(**kwargs):
    # Fills out every field with defaults or kwargs overrides
    new_policy = Policy(MIN_PARAMS)
    defaults = dict(
        alias="testalias",
        vol=100000,
        vol_int=20000,
        revenue=500,
        ema_blended=150000,
        ema_delta=20000,
        rev_ema_blended=200,
        rev_delta=20,
        last_daily_vol=50000,
        fee=1,
        min_fee=1,
        max_fee=1,
        inbound_fee=10,
        fee_bump_streak=0,
        zero_ema_count=0,
        role="tap",
        days_since_flip=0,
        sink_ratio=0,
        sink_delta=0,
        sink_risk_score=0,
        ema_from_target=0,
        FEE_INCREMENT_PPM=2,
        FEE_MIN_PPM=1,
        FEE_MAX_PPM=5000,
        DELTA_THRESHOLD=0.05,
        REVENUE_THRESHOLD=0.05,
        FEE_BUMP_MAX=100,
        policy=new_policy,
        calculate_exponential_fee_bump=dummy_bump,
        percentage_outbound=0.5,
        skip_outbound_fee_adjust=False,
        skip_inbound_fee_adjust=False,
    )
    defaults.update(kwargs)
    return rule_engine.Context(**defaults)


def test_rule_a1_bootstrap_low_fee_triggers():
    ctx = build_ctx(max_fee=1, ema_blended=200_000, vol=100_000, last_daily_vol=50_000)
    res = rule_engine.rule_a1_bootstrap_low_fee(ctx)
    assert res is not None
    assert res.rule_id == "A1_bootstrap_low_fee"


def test_rule_a1_bootstrap_low_fee_not_trigger():
    ctx = build_ctx(max_fee=2, ema_blended=50_000, vol=20_000, last_daily_vol=20_000)
    assert rule_engine.rule_a1_bootstrap_low_fee(ctx) is None


def test_rule_b1_small_decay_triggers():
    ctx = build_ctx(fee=1, vol_int=0, vol=10, last_daily_vol=100)
    res = rule_engine.rule_b1_small_decay(ctx)
    assert res and res.rule_id == "B1_small_decay"


def test_rule_c1_exponential_bump_triggers():
    ctx = build_ctx(ema_delta=100_000, ema_blended=10_000, max_fee=2_000, revenue=1)
    res = rule_engine.rule_c1_exponential_bump(ctx)
    assert res and res.rule_id == "C1_exponential_bump"


def test_rule_f3_sink_ema_guard_triggers():
    ctx = build_ctx(sink_ratio=10.0, ema_from_target=100_000, sink_delta=1)
    res = rule_engine.rule_f3_sink_ema_guard(ctx)
    assert res and res.rule_id == "F3_ema_sink_guard"


def test_rule_f5_tap_inbound_tax_triggers():
    # Use MIN_PARAMS Policy for controlled test
    policy = Policy(MIN_PARAMS)
    ctx = build_ctx(
        alias="testalias", sink_risk_score=0, ema_blended=400_000, policy=policy
    )
    res = rule_engine.rule_f5_tap_inbound_tax(ctx)
    assert res and res.rule_id == "F5_tap_inbound_tax"
    assert res.inbound_fee > 0


def test_rule_f6_inbound_fee_decay_triggers():
    ctx = build_ctx(inbound_fee=100, sink_risk_score=0, ema_blended=10_000)
    res = rule_engine.rule_f6_inbound_fee_decay(ctx)
    assert res and res.rule_id == "F6_inbound_fee_decay"
    assert res.inbound_fee < 100


def test_evaluate_fee_rules_a1_only():
    # Remove "testalias" from inbound_fee_targets to prevent F5 firing
    policy = Policy(
        {
            **MIN_PARAMS,
            "rules": {
                **MIN_PARAMS["rules"],
                "inbound_fee_targets": [],
            },
        }
    )
    ctx = build_ctx(
        max_fee=1,
        ema_blended=200_000,
        vol=100_000,
        last_daily_vol=50_000,
        ema_delta=0,  # so C1 will not fire
        revenue=0,  # so C1 will not fire
        policy=policy,
    )
    out, inp = rule_engine.evaluate_fee_rules(ctx)
    assert out is not None
    assert out.rule_id == "A1_bootstrap_low_fee"
    assert inp is None


def test_evaluate_fee_rules_inbound():
    # Remove "testalias" from inbound_fee_targets to prevent F5 firing
    policy = Policy(
        {
            **MIN_PARAMS,
            "rules": {
                **MIN_PARAMS["rules"],
                "inbound_fee_targets": [],
            },
        }
    )
    ctx = build_ctx(
        inbound_fee=100,
        ema_blended=10_000,  # triggers decay
        sink_risk_score=0.1,  # not too high (so doesn't hard reset to 0)
        policy=policy,
    )
    out, inp = rule_engine.evaluate_fee_rules(ctx)
    assert inp is not None
    assert inp.rule_id == "F6_inbound_fee_decay"


@pytest.mark.parametrize("rand", [random.randint(1, 1000) for _ in range(3)])
def test_rule_engine_robustness_to_random_fields(rand):
    policy = Policy(MIN_PARAMS)
    ctx = build_ctx(fee=rand, last_daily_vol=rand, ema_blended=rand, policy=policy)
    rule_engine.evaluate_fee_rules(ctx)
