# ─── rule_engine.py ───────────────────────────────────────────────────────────
import logging
from dataclasses import dataclass
from typing import Callable, List, Optional
from autotune.policy_utils import Policy

logger = logging.getLogger(__name__)


class RuleResult:
    def __init__(
        self,
        rule_id,
        new_min,
        new_max,
        weight,
        override_cooldown=False,
        inbound_fee=None,
    ):
        self.rule_id = rule_id
        self.new_min = new_min
        self.new_max = new_max
        self.weight = weight
        self.override_cooldown = override_cooldown
        self.inbound_fee = inbound_fee  # optional

    def __iter__(self):
        return iter((self.rule_id, self.new_min, self.new_max, self.weight))


# ── 1. CONTEXT ───────────────────────────────────────────────────────────────
@dataclass
class Context:
    # peer meta
    alias: str

    # live metrics
    vol: int
    vol_int: int
    revenue: int
    ema_blended: float
    ema_delta: float
    rev_ema_blended: float
    rev_delta: float
    last_daily_vol: int

    # fees / state
    fee: int
    min_fee: int
    max_fee: int
    inbound_fee: int
    fee_bump_streak: int
    zero_ema_count: int

    # role-flip data
    role: str
    days_since_flip: int

    # sink guard
    sink_ratio: float
    sink_delta: float
    sink_risk_score: float
    ema_from_target: float

    # constants / helpers
    FEE_INCREMENT_PPM: int
    FEE_MIN_PPM: int
    FEE_MAX_PPM: int
    DELTA_THRESHOLD: float
    REVENUE_THRESHOLD: float
    FEE_BUMP_MAX: int
    calculate_exponential_fee_bump: Callable
    policy: Policy

    # capacity
    percentage_outbound: float

    # bool flags
    skip_outbound_fee_adjust: bool
    skip_inbound_fee_adjust: bool


# ── 2. RULES ─────────────────────────────────────────────────────────────────
# Group A – incremental +1 style
def rule_a1_bootstrap_low_fee(ctx: Context):
    if ctx.max_fee <= 1 and ctx.ema_blended > 100_000 and ctx.vol > ctx.last_daily_vol:
        new_max = 2  # modest ceiling
        new_min = 1
        return RuleResult("A1_bootstrap_low_fee", new_min, new_max, 20)


def rule_a2_incremental_plus_one(ctx: Context):
    if (
        ctx.max_fee < ctx.FEE_INCREMENT_PPM
        and ctx.vol_int > 10_000
        and ctx.ema_blended > 100_000
        and ctx.vol > ctx.last_daily_vol
    ):
        new_max = ctx.fee + 1
        new_min = new_max // 2
        return RuleResult("A2_incremental_plus_one", new_min, new_max, 20)


# Group B – small decays
def rule_b1_small_decay(ctx: Context):
    if ctx.fee <= 0:
        return None  # No decay possible
    if (
        ctx.fee < ctx.FEE_INCREMENT_PPM
        and ctx.vol_int < 10_000
        and ctx.vol <= ctx.last_daily_vol
    ):
        new_max = max(0, ctx.fee - 1)
        new_min = new_max // 2
        return RuleResult("B1_small_decay", new_min, new_max, 40)


def rule_b2_zero_volume_decay(ctx: Context):
    if (
        ctx.vol == 0
        and ctx.max_fee > 0
        and ctx.max_fee < ctx.FEE_INCREMENT_PPM
        and ctx.revenue == 0
        and ctx.ema_blended < 10_000
    ):
        new_max = max(ctx.FEE_MIN_PPM, ctx.fee - 1)
        new_min = new_max // 2
        return RuleResult("B2_zero_volume_decay", new_min, new_max, 35)


def rule_b3_generic_decay(ctx: Context):
    if ctx.vol == 0:
        new_max = max(ctx.FEE_MIN_PPM, ctx.max_fee - ctx.FEE_INCREMENT_PPM)
        new_min = new_max // 2
        return RuleResult("B3_generic_decay", new_min, new_max, 30)


# Group C – exponential growth bump
def rule_c1_exponential_bump(ctx: Context):
    strong_growth = ctx.ema_delta > ctx.ema_blended * ctx.DELTA_THRESHOLD
    if strong_growth and ctx.max_fee < ctx.FEE_MAX_PPM and ctx.revenue > 0:
        new_max, new_min, _ = ctx.calculate_exponential_fee_bump(
            ctx.fee, ctx.fee_bump_streak, ctx.policy.fees
        )
        return RuleResult(
            "C1_exponential_bump", new_min, new_max, 100, override_cooldown=True
        )


# Group D – negative-delta decay
def rule_d1_negative_delta_decay(ctx: Context):
    big_drop_v = ctx.ema_delta < -ctx.ema_blended * ctx.DELTA_THRESHOLD
    big_drop_r = ctx.rev_delta < -ctx.rev_ema_blended * ctx.REVENUE_THRESHOLD
    stagnating = (
        ctx.ema_delta == ctx.rev_delta == ctx.ema_blended == ctx.rev_ema_blended == 0
    )
    if big_drop_v and big_drop_r or stagnating:
        new_max = max(ctx.FEE_MIN_PPM, ctx.fee - ctx.FEE_INCREMENT_PPM)
        new_min = new_max // 2
        return RuleResult("D1_negative_delta_decay", new_min, new_max, 25)


# Group E – zero-EMA exponential decay
def rule_e1_zero_ema_exponential_decay(ctx: Context):
    if ctx.zero_ema_count > 5:
        steps = min(ctx.zero_ema_count, 10)
        new_max = max(ctx.FEE_MIN_PPM, ctx.fee - steps * ctx.FEE_INCREMENT_PPM)
        new_min = new_max // 2
        return RuleResult("E1_zero_ema_exponential_decay", new_min, new_max, 20)


# Group F – role-flip stabilisers
def rule_f1_role_flip_freeze(ctx: Context):
    if ctx.days_since_flip < 1:
        return RuleResult(
            "F1_role_flip_freeze",
            ctx.min_fee,
            ctx.max_fee,
            10,
            inbound_fee=ctx.inbound_fee,
        )


def rule_f2_tap_surge_boost(ctx: Context):
    if (
        ctx.role == "tap"
        and ctx.days_since_flip <= 3
        and ctx.ema_delta > ctx.ema_blended * 0.05
        and ctx.fee == 0
    ):
        channel_inbound_fee = ctx.policy.channels.get(ctx.alias, {}).get("inbound_fee_ppm", 0)
        inbound_fee = min(ctx.inbound_fee, channel_inbound_fee)
        return RuleResult("F2_tap_surge_boost", 0, 1, 85, inbound_fee=ctx.inbound_fee)


def rule_f3_sink_ema_guard(ctx):
    """
    Rule: Detect sink pattern from EMA-based ratios and apply protective fee bump.
    Fires if:
        - sink_ratio > 5.0
        - very little outbound
        - sink_ratio increasing
    """
    if ctx.sink_ratio > 5.0 and ctx.ema_from_target < 250_000 and ctx.sink_delta > 0.5:
        bump = max(0, ctx.max_fee)
        return RuleResult("F3_ema_sink_guard", bump / 1.25, bump, 70)
    return None


def rule_f4_sink_score_guard(ctx: Context):
    """
    Rule: Use predictive sink risk score to trigger:
    - outbound fee bump for non-exempt channels

    Triggers when:
    - sink_risk_score is high (≥ 0.9)
    - outbound fee still low (< 1000)
    - and/or alias is in 'inbound_fee_targets'
    """
    # Determine if outbound sink protection should apply
    if ctx.sink_risk_score < 0.9:
        return None

    ignore_rule = ctx.alias in ctx.policy.rules.exempt_from_sink_guard
    if ignore_rule:
        return None

    bump = (
        max(1000, ctx.fee + ctx.FEE_INCREMENT_PPM)
        if not ignore_rule and ctx.fee < 1000
        else ctx.max_fee
    )
    new_min_fee = min(ctx.min_fee, bump) if ctx.min_fee <= bump else bump
    inbound_fee = 0

    return RuleResult(
        "F4_sink_score_guard", new_min_fee, bump, 65, inbound_fee=inbound_fee
    )


def rule_f5_tap_inbound_tax(ctx: Context):
    """
    Dynamic inbound fee for known taps like CoinGate.
    Fee decays automatically with reduced flow (via ema_delta).
    """
    if ctx.sink_risk_score > 0.2:
        return None

    if ctx.alias in ctx.policy.rules.exempt_from_sink_guard:
        return None

    if ctx.alias not in ctx.policy.rules.get("inbound_fee_targets", []):
        return None

    min_fee = ctx.policy.inbound_fees.min_fee_ppm
    max_fee = ctx.policy.inbound_fees.max_fee_ppm
    channel_inbound_fee = ctx.policy.channels[ctx.alias].get("inbound_fee_ppm", 0)

    inbound_fee = min(int(min_fee + (ctx.ema_blended / 4000)), channel_inbound_fee)

    return RuleResult(
        "F5_tap_inbound_tax",
        ctx.min_fee,
        ctx.max_fee,  # no change to outbound
        65,
        inbound_fee=inbound_fee,
    )


def rule_f6_inbound_fee_decay(ctx: Context):
    """
    Gently decays inbound fee for prior taps based on EMA.
    - Reduces only when traffic drops significantly.
    - Prevents large drops per cycle.
    - Hard reset if sink_score rises.
    """
    if ctx.inbound_fee <= 0:
        return None

    # Stop taxing entirely if node starts hoarding again
    if ctx.sink_risk_score > 0.5:
        return RuleResult(
            "F6_inbound_fee_decay", ctx.min_fee, ctx.max_fee, 60, inbound_fee=0
        )

    # Thresholds
    decay_threshold = 100_000
    min_decay_pct = 0.85  # Don't drop more than 15%
    max_decay_ppm = 100  # Never drop more than 200ppm per run

    # Decay condition
    if ctx.ema_blended < decay_threshold:
        scale = max(
            ctx.ema_blended / decay_threshold, min_decay_pct
        )  # clamp to avoid sharp drops
        target_fee = int(ctx.inbound_fee * scale)
        decayed_fee = max(ctx.inbound_fee - max_decay_ppm, target_fee)
        if decayed_fee < ctx.inbound_fee:
            return RuleResult(
                "F6_inbound_fee_decay",
                ctx.min_fee,
                ctx.max_fee,
                55,
                inbound_fee=decayed_fee,
            )

    return None


def rule_f7_subsidise_inbound(ctx: Context):
    """
    Subsidise inbound for channels with low outbound
    inbound_fee = ctx.inbound_fee
    """
    if ctx.percentage_outbound > 0.1:
        if ctx.inbound_fee < 0:
            inbound_fee = 0
        else:
            return None
    else:
        inbound_fee = -ctx.min_fee

    return RuleResult(
        "F7_subsidise_inbound", ctx.min_fee, ctx.max_fee, 50, inbound_fee=inbound_fee
    )


# Group H - HTLC driven rules
#def rule_h1_missed_events(ctx: Context):
#    """
#    Subsidise inbound for channels with low outbound
#    inbound_fee = ctx.inbound_fee
#    """
#    if (
#        ctx.policy.thresholds.get("htlc_forward_failures_raise")
#        and ctx.peer_event_summary.get("events", 0)
#        > ctx.policy.thresholds.htlc_forward_failures_raise
#   ):
        #max_fee = ctx.max_fee + ctx.policy.fees.increment_ppm
        #min_fee = int(max_fee / 2)
    #elif (
        #ctx.policy.thresholds.get("htlc_forward_failures_hold")
        #and ctx.peer_event_summary.get("events", 0)
        #> ctx.policy.thresholds.htlc_forward_failures_hold
    #):
        #max_fee = ctx.max_fee
        #min_fee = ctx.min_fee
    #else:
        #return None

    #return RuleResult(
       # "H1_missed_events",
        #min_fee,
        #max_fee,
        #100,
    #)


# ── 3. RULE ENGINE ───────────────────────────────────────────────────────────
ALL_RULES = [
    # Group H - HTLC driven rules
    #rule_h1_missed_events,
    # Group F – role-flip & predictive stabilisers
    rule_f1_role_flip_freeze,
    rule_f2_tap_surge_boost,
    rule_f3_sink_ema_guard,
    rule_f4_sink_score_guard,
    rule_f5_tap_inbound_tax,
    rule_f6_inbound_fee_decay,
    rule_f7_subsidise_inbound,
    # Group A – incremental growth
    rule_a1_bootstrap_low_fee,
    rule_a2_incremental_plus_one,
    # Group C – exponential growth
    rule_c1_exponential_bump,
    # Group B – decays
    rule_b1_small_decay,
    rule_b2_zero_volume_decay,
    rule_b3_generic_decay,
    # Group D – reactive decay
    rule_d1_negative_delta_decay,
    # Group E – zero EMA decay
    rule_e1_zero_ema_exponential_decay,
]


def evaluate_fee_rules(ctx):
    inbound_rules = []
    outbound_rules = []

    for rule in ALL_RULES:
        result = rule(ctx)
        if not result:
            continue

        if (
            result.inbound_fee is not None
            and result.inbound_fee != ctx.inbound_fee
            and not ctx.skip_inbound_fee_adjust
        ):
            inbound_rules.append(result)

        if (
            result.new_min != ctx.min_fee or result.new_max != ctx.max_fee
        ) and not ctx.skip_outbound_fee_adjust:
            outbound_rules.append(result)

    best_outbound = max(outbound_rules, key=lambda r: r.weight, default=None)
    best_inbound = max(inbound_rules, key=lambda r: r.weight, default=None)

    return best_outbound, best_inbound
