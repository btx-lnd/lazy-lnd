# autotune/tests/test_rule_engine_property.py

import string
import pytest
import tomli
from hypothesis import given, settings, strategies as st
from autotune import rule_engine
from autotune.policy_utils import Policy


# For robust testing, use deterministic, fixed bump logic
def fixed_bump(*a, **k):
    return (100, 50, 25)


@st.composite
def context_strategy(draw):
    new_policy = None
    with open("autotune/params.toml", "rb") as f:
        new_policy = Policy(tomli.load(f))
    return rule_engine.Context(
        alias = draw(st.text(min_size=1, max_size=16, alphabet=string.ascii_letters)),
        vol=draw(st.integers(min_value=0, max_value=10_000_000)),
        vol_int=draw(st.integers(min_value=0, max_value=10_000_000)),
        revenue=draw(st.integers(min_value=0, max_value=1_000_000)),
        ema_blended=draw(
            st.floats(
                min_value=0, max_value=10_000_000, allow_nan=False, allow_infinity=False
            )
        ),
        ema_delta=draw(
            st.floats(
                min_value=-10_000_000,
                max_value=10_000_000,
                allow_nan=False,
                allow_infinity=False,
            )
        ),
        rev_ema_blended=draw(
            st.floats(
                min_value=0, max_value=1_000_000, allow_nan=False, allow_infinity=False
            )
        ),
        rev_delta=draw(
            st.floats(
                min_value=-1_000_000,
                max_value=1_000_000,
                allow_nan=False,
                allow_infinity=False,
            )
        ),
        last_daily_vol=draw(st.integers(min_value=0, max_value=10_000_000)),
        fee=draw(st.integers(min_value=0, max_value=5000)),
        min_fee=draw(st.integers(min_value=0, max_value=2500)),
        max_fee=draw(st.integers(min_value=1, max_value=5000)),
        inbound_fee=draw(st.integers(min_value=-2000, max_value=2000)),
        fee_bump_streak=draw(st.integers(min_value=0, max_value=12)),
        zero_ema_count=draw(st.integers(min_value=0, max_value=20)),
        role=draw(st.sampled_from(["tap", "sink", "balanced", "undefined"])),
        days_since_flip=draw(st.integers(min_value=0, max_value=31)),
        sink_ratio=draw(
            st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False)
        ),
        sink_delta=draw(
            st.floats(
                min_value=-10, max_value=10, allow_nan=False, allow_infinity=False
            )
        ),
        sink_risk_score=draw(
            st.floats(min_value=0, max_value=1.2, allow_nan=False, allow_infinity=False)
        ),
        ema_from_target=draw(
            st.floats(
                min_value=-10_000_000,
                max_value=10_000_000,
                allow_nan=False,
                allow_infinity=False,
            )
        ),
        FEE_INCREMENT_PPM=draw(st.integers(min_value=1, max_value=100)),
        FEE_MIN_PPM=draw(st.integers(min_value=0, max_value=100)),
        FEE_MAX_PPM=draw(st.integers(min_value=10, max_value=5000)),
        DELTA_THRESHOLD=draw(
            st.floats(
                min_value=0.01, max_value=1, allow_nan=False, allow_infinity=False
            )
        ),
        REVENUE_THRESHOLD=draw(
            st.floats(
                min_value=0.01, max_value=1, allow_nan=False, allow_infinity=False
            )
        ),
        FEE_BUMP_MAX=draw(st.integers(min_value=10, max_value=1000)),
        policy=new_policy,
        calculate_exponential_fee_bump=fixed_bump,
        percentage_outbound=draw(
            st.floats(min_value=0, max_value=1, allow_nan=False, allow_infinity=False)
        ),
        skip_outbound_fee_adjust=draw(st.booleans()),
        skip_inbound_fee_adjust=draw(st.booleans()),
    )


@given(ctx=context_strategy())
@settings(max_examples=200, deadline=500)
def test_rule_engine_never_negative_fees(ctx):
    out, inp = rule_engine.evaluate_fee_rules(ctx)
    # Outbound result
    if out:
        assert out.new_min >= 0, f"Rule {out.rule_id}: new_min < 0"
        assert out.new_max >= 0, f"Rule {out.rule_id}: new_max < 0"
        # Bound checks
        assert out.new_min <= out.new_max, f"Rule {out.rule_id}: new_min > new_max"
    # Inbound fee always sensible
    if inp and inp.inbound_fee is not None:
        assert inp.inbound_fee >= -ctx.min_fee
        assert inp.inbound_fee < 10_000


@given(ctx=context_strategy())
@settings(max_examples=100, deadline=500)
def test_rule_engine_respects_skip_flags(ctx):
    out, inp = rule_engine.evaluate_fee_rules(ctx)
    if ctx.skip_outbound_fee_adjust:
        assert out is None, f"Should skip outbound, got {out}"
    if ctx.skip_inbound_fee_adjust:
        assert inp is None, f"Should skip inbound, got {inp}"


@given(ctx=context_strategy())
@settings(max_examples=100, deadline=500)
def test_rule_engine_is_robust(ctx):
    # This should never throw
    rule_engine.evaluate_fee_rules(ctx)
