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
        cooldown_override=False,
        inbound_fee=None,
    ):
        self.rule_id = rule_id
        self.new_min = new_min
        self.new_max = new_max
        self.weight = weight
        self.cooldown_override = cooldown_override
        self.inbound_fee = inbound_fee  # optional

    def __iter__(self):
        return iter((self.rule_id, self.new_min, self.new_max, self.weight, self.cooldown_override))


# ── 1. CONTEXT ───────────────────────────────────────────────────────────────
@dataclass
class Context:
    # peer meta
    alias: str
    peer_mem: dict
    channel_data: dict

    # live metrics
    vol: int
    vol_int: int
    revenue: int
    prev_ema_blended: float
    ema_blended: float
    ema_delta: float
    prev_rev_ema_blended: float
    rev_ema_blended: float
    rev_delta: float
    last_daily_vol: int
    last_successful_fee: int

    # fees / state
    fee: int
    min_fee: int
    max_fee: int
    inbound_fee: int
    fee_bump_streak: int
    zero_ema_count: int
    htlc_stats: dict

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
    logger.debug(
        f"A1 check: max_fee={ctx.max_fee}, ema_blended={ctx.ema_blended}, vol={ctx.vol}, last_daily_vol={ctx.last_daily_vol}",
        extra={'section': ctx.alias},
    )
    
    if fee_increase_skip_check(ctx, "A1"):
        return None

    if ctx.max_fee <= 1 and ctx.ema_blended > 100_000 and ctx.vol > ctx.last_daily_vol:
        new_max = 2  # modest ceiling
        new_min = 1
        logger.info(
            f"A1 rule fired: Raising fee from {ctx.max_fee} to min={new_min}, max={new_max} (ema_blended={ctx.ema_blended}, vol={ctx.vol})",
            extra={'section': ctx.alias},
        )
        return RuleResult("A1_bootstrap_low_fee", new_min, new_max, 20)
    else:
        logger.debug(
            f"A1 not fired: (max_fee={ctx.max_fee} > 1 or ema_blended={ctx.ema_blended} <= 100k or vol={ctx.vol} <= last_daily_vol={ctx.last_daily_vol})",
            extra={'section': ctx.alias},
        )
        return None


def rule_a2_incremental_plus_one(ctx: Context):
    logger.debug(
        f"A2 check: max_fee={ctx.max_fee}, fee={ctx.fee}, FEE_INCREMENT_PPM={ctx.FEE_INCREMENT_PPM}, vol_int={ctx.vol_int}, ema_blended={ctx.ema_blended}, vol={ctx.vol}, last_daily_vol={ctx.last_daily_vol}",
        extra={'section': ctx.alias},
    )
    if fee_increase_skip_check(ctx, "A2"):
        return None

    if (
        ctx.max_fee < ctx.FEE_INCREMENT_PPM
        and ctx.vol_int > 10_000
        and ctx.ema_blended > 100_000
        and ctx.vol > ctx.last_daily_vol
    ):
        new_max = ctx.fee + 1
        new_min = new_max // 2
        logger.info(
            f"A2 rule fired: Incrementing max_fee from {ctx.max_fee} to {new_max} (ema_blended={ctx.ema_blended}, vol_int={ctx.vol_int}, vol={ctx.vol})",
            extra={'section': ctx.alias},
        )
        return RuleResult("A2_incremental_plus_one", new_min, new_max, 20)
    else:
        logger.debug(
            f"A2 not fired: (max_fee={ctx.max_fee} >= FEE_INCREMENT_PPM or vol_int={ctx.vol_int} <= 10k or ema_blended={ctx.ema_blended} <= 100k or vol={ctx.vol} <= last_daily_vol={ctx.last_daily_vol})",
            extra={'section': ctx.alias},
        )
        return None

# Group B – small decays
def rule_b1_small_decay(ctx: Context):
    logger.debug(
        f"B1 check: fee={ctx.fee}, FEE_INCREMENT_PPM={ctx.FEE_INCREMENT_PPM}, vol_int={ctx.vol_int}, vol={ctx.vol}, last_daily_vol={ctx.last_daily_vol}",
        extra={'section': ctx.alias},
    )
    
    if fee_decay_skip_check(ctx, "B1"):
        return None

    if ctx.fee <= 0:
        logger.debug(
            "B1 not fired: fee <= 0 (no decay possible)",
            extra={'section': ctx.alias},
        )
        return None  # No decay possible

    if (
        ctx.fee < ctx.FEE_INCREMENT_PPM
        and ctx.vol_int < 10_000
        and ctx.vol <= ctx.last_daily_vol
    ):
        new_max = max(0, ctx.fee - 1)
        new_min = new_max // 2
        logger.info(
            f"B1 rule fired: Small decay, lowering max_fee from {ctx.fee} to {new_max} (vol_int={ctx.vol_int}, vol={ctx.vol})",
            extra={'section': ctx.alias},
        )
        return RuleResult("B1_small_decay", new_min, new_max, 40)
    else:
        logger.debug(
            f"B1 not fired: (fee={ctx.fee} >= FEE_INCREMENT_PPM or vol_int={ctx.vol_int} >= 10k or vol={ctx.vol} > last_daily_vol={ctx.last_daily_vol})",
            extra={'section': ctx.alias},
        )
        return None
 

def rule_b2_zero_volume_decay(ctx: Context):
    logger.debug(
        f"B2 check: vol={ctx.vol}, max_fee={ctx.max_fee}, FEE_INCREMENT_PPM={ctx.FEE_INCREMENT_PPM}, revenue={ctx.revenue}, ema_blended={ctx.ema_blended}",
        extra={'section': ctx.alias},
    )
    
    if fee_decay_skip_check(ctx, "B2"):
        return None

    if (
        ctx.vol == 0
        and ctx.max_fee > 0
        and ctx.max_fee < ctx.FEE_INCREMENT_PPM
        and ctx.revenue == 0
        and ctx.ema_blended < 10_000
    ):
        new_max = max(ctx.FEE_MIN_PPM, ctx.fee - 1)
        new_min = new_max // 2
        logger.info(
            f"B2 rule fired: Zero volume decay, lowering max_fee from {ctx.fee} to {new_max} (ema_blended={ctx.ema_blended})",
            extra={'section': ctx.alias},
        )
        return RuleResult("B2_zero_volume_decay", new_min, new_max, 35)
    else:
        logger.debug(
            "B2 not fired: conditions not met (nonzero vol, fee, revenue, or high EMA)",
            extra={'section': ctx.alias},
        )
        return None
 

def rule_b3_generic_decay(ctx: Context):
    logger.debug(
        f"B3 check: vol={ctx.vol}, max_fee={ctx.max_fee}, FEE_INCREMENT_PPM={ctx.FEE_INCREMENT_PPM}",
        extra={'section': ctx.alias},
    )
    
    if fee_decay_skip_check(ctx, "B3"):
        return None

    if ctx.vol == 0:
        new_max = max(ctx.FEE_MIN_PPM, ctx.max_fee - ctx.FEE_INCREMENT_PPM)
        new_min = new_max // 2
        logger.info(
            f"B3 rule fired: Generic decay, lowering max_fee from {ctx.max_fee} to {new_max}",
            extra={'section': ctx.alias},
        )
        return RuleResult("B3_generic_decay", new_min, new_max, 30)
    else:
        logger.debug(
            "B3 not fired: volume not zero",
            extra={'section': ctx.alias},
        )
        return None
 

# Group C – exponential growth bump
def rule_c1_exponential_bump(ctx: Context):
    strong_growth = ctx.ema_delta > ctx.ema_blended * ctx.DELTA_THRESHOLD

    logger.debug(
        f"C1 check: strong_growth={strong_growth}, ema_delta={ctx.ema_delta}, "
        f"ema_blended={ctx.ema_blended}, DELTA_THRESHOLD={ctx.DELTA_THRESHOLD}, "
        f"max_fee={ctx.max_fee}, FEE_MAX_PPM={ctx.FEE_MAX_PPM}, revenue={ctx.revenue}",
        extra={'section': ctx.alias},
    )
    
    if fee_increase_skip_check(ctx, "C1"):
        return None

    if strong_growth and ctx.max_fee < ctx.FEE_MAX_PPM and ctx.revenue > 0:
        new_max, new_min, _ = ctx.calculate_exponential_fee_bump(
            ctx.fee, ctx.fee_bump_streak, ctx.policy.fees
        )
    else:
        logger.debug(
            "C1 not fired: Conditions not met for exponential bump.",
            extra={'section': ctx.alias},
        )
        return None
    
    max_allowed = int(ctx.last_successful_fee * 1.5) if ctx.last_successful_fee else ctx.max_fee
    if new_max > max_allowed:
        logger.info(
            f"Fee bump blocked: proposed {new_max} exceeds 2x last_successful_fee ({ctx.last_successful_fee})",
            extra={'section': ctx.alias},
        )
        return None
    
    logger.info(
        f"C1 rule fired: Exponential bump, raising max_fee to {new_max} (streak={ctx.fee_bump_streak})",
        extra={'section': ctx.alias},
    )
    return RuleResult(
        "C1_exponential_bump", new_min, new_max, 100, cooldown_override=True
    )


# Group D – negative-delta decay
def rule_d1_negative_delta_decay(ctx: Context):
    big_drop_v = ctx.ema_delta < -ctx.ema_blended * ctx.DELTA_THRESHOLD
    big_drop_r = ctx.rev_delta < -ctx.rev_ema_blended * ctx.REVENUE_THRESHOLD
    stagnating = (
        ctx.ema_delta == ctx.rev_delta == ctx.ema_blended == ctx.rev_ema_blended == 0
    ) 

    logger.debug(
        f"D1 check: big_drop_v={big_drop_v}, big_drop_r={big_drop_r}, "
        f"stagnating={stagnating}, ema_delta={ctx.ema_delta}, rev_delta={ctx.rev_delta}, "
        f"ema_blended={ctx.ema_blended}, rev_ema_blended={ctx.rev_ema_blended}, "
        f"DELTA_THRESHOLD={ctx.DELTA_THRESHOLD}, REVENUE_THRESHOLD={ctx.REVENUE_THRESHOLD}",
        extra={'section': ctx.alias},
    )
    
    if fee_decay_skip_check(ctx, "D1"):
        return None

    if (big_drop_v and big_drop_r) or stagnating:
        new_max = max(ctx.FEE_MIN_PPM, ctx.fee - ctx.FEE_INCREMENT_PPM)
        new_min = new_max // 2
        logger.info(
            f"D1 rule fired: Negative delta or stagnation, reducing max_fee to {new_max}.",
            extra={'section': ctx.alias},
        )
        return RuleResult("D1_negative_delta_decay", new_min, new_max, 25)
    else:
        logger.debug(
            "D1 not fired: Conditions not met for negative delta decay.",
            extra={'section': ctx.alias},
        )
        return None
 

# Group E – zero-EMA exponential decay
def rule_e1_zero_ema_exponential_decay(ctx: Context):

    if fee_decay_skip_check(ctx, "E1"):
        return None

    if ctx.zero_ema_count > 5:
        steps = min(ctx.zero_ema_count, 10)
        new_max = max(ctx.FEE_MIN_PPM, ctx.fee - steps * ctx.FEE_INCREMENT_PPM)
        new_min = new_max // 2
        logger.info(
            f"E1 rule fired: zero_ema_count={ctx.zero_ema_count}, decreasing max_fee to {new_max} (steps={steps}).",
            extra={'section': ctx.alias},
        )
        return RuleResult("E1_zero_ema_exponential_decay", new_min, new_max, 20)
    else:
        logger.debug(
            f"E1 not fired: zero_ema_count={ctx.zero_ema_count} <= 5.",
            extra={'section': ctx.alias},
        )
        return None
 

# Group F – role-flip stabilisers
def rule_f1_role_flip_freeze(ctx: Context):
    if ctx.days_since_flip < 1:
        logger.info(
            f"F1 rule fired: days_since_flip={ctx.days_since_flip} < 1, freezing fees at current values.",
            extra={'section': ctx.alias},
        )
        return RuleResult(
            "F1_role_flip_freeze",
            ctx.min_fee,
            ctx.max_fee,
            10,
            inbound_fee=ctx.inbound_fee,
        )
    else:
        logger.debug(
            f"F1 not fired: days_since_flip={ctx.days_since_flip} >= 1.",
            extra={'section': ctx.alias},
        )
        return None


def rule_f2_tap_surge_boost(ctx: Context):
    if (
        ctx.role == "tap"
        and ctx.days_since_flip <= 3
        and ctx.ema_delta > ctx.ema_blended * 0.05
        and ctx.fee == 0
    ):
        channel_inbound_fee = ctx.policy.channels.get(ctx.alias, {}).get("inbound_fee_ppm", 0)
        inbound_fee = min(ctx.inbound_fee, channel_inbound_fee)
        logger.info(
            f"F2 rule fired: tap in surge (days_since_flip={ctx.days_since_flip}, ema_delta={ctx.ema_delta}, fee={ctx.fee}).",
            extra={'section': ctx.alias},
        )
        return RuleResult("F2_tap_surge_boost", 0, 1, 85, inbound_fee=inbound_fee)
    logger.debug(
        f"F2 not fired: role={ctx.role}, days_since_flip={ctx.days_since_flip}, ema_delta={ctx.ema_delta}, fee={ctx.fee}",
        extra={'section': ctx.alias},
    )
    return None
 

def rule_f3_sink_ema_guard(ctx):
    """
    F3: EMA Sink Guard
    - Fires if:
        - sink_ratio > 5.0 (significant sink pattern)
        - ema_from_target < 250_000 (very little outbound left)
        - sink_ratio increasing rapidly (sink_delta > 0.5)
    - Bumps outbound fee to max_fee, min_fee to 80% of max_fee.
    """
    if ctx.sink_ratio > 5.0 and ctx.ema_from_target < 250_000 and ctx.sink_delta > 0.5:
        bump = max(0, ctx.max_fee)
        min_bump = int(bump * 0.8)
        logger.info(
            f"F3 rule fired: sink detected (sink_ratio={ctx.sink_ratio}, ema_from_target={ctx.ema_from_target}, sink_delta={ctx.sink_delta}). "
            f"Bumping min_fee to {min_bump}, max_fee to {bump}.",
            extra={'section': ctx.alias},
        )
        return RuleResult("F3_ema_sink_guard", min_bump, bump, 70)
    logger.debug(
        f"F3 not fired: sink_ratio={ctx.sink_ratio}, ema_from_target={ctx.ema_from_target}, sink_delta={ctx.sink_delta}",
        extra={'section': ctx.alias},
    )
    return None


def rule_f4_sink_score_guard(ctx: Context):
    """
    F4: Sink Risk Guard (Outbound Fee Bump)
    - Fires when sink_risk_score is high (≥ 0.9) and outbound fee is still low (< 1000).
    - Skips if channel is in exempt_from_sink_guard list.
    - Bumps outbound fee to at least 1000ppm or by FEE_INCREMENT_PPM, up to max_fee.
    - Used to defend against persistent draining (sink risk).
    """
    if ctx.sink_risk_score < 0.9:
        logger.debug(
            f"F4 not fired: sink_risk_score={ctx.sink_risk_score} < threshold 0.9",
            extra={'section': ctx.alias},
        )
        return None

    if ctx.alias in ctx.policy.rules.get("sink_guard_disabled", []):
        logger.info(
            f"F4 skipped: {ctx.alias} is exempt from sink guard",
            extra={'section': ctx.alias},
        )
        return None

    if ctx.fee >= 1000:
        logger.debug(
            f"F4 not fired: fee already ≥ 1000 (fee={ctx.fee})",
            extra={'section': ctx.alias},
        )
        return None

    # Bump fee to at least 1000, or by increment, capped at max_fee
    bump = min(max(ctx.fee + ctx.FEE_INCREMENT_PPM, 1000), ctx.max_fee)

    logger.info(
        f"F4 rule fired: sink_risk_score high ({ctx.sink_risk_score:.2f}), bumping fee to {bump}",
        extra={'section': ctx.alias},
    )

    return RuleResult(
        "F4_sink_score_guard", bump, bump, 65, inbound_fee=ctx.inbound_fee
    )


def rule_f5_sink_inbound_tax(ctx: Context):
    """
    F5: Sink Inbound Tax
    - Applies a dynamic inbound fee for known sink targets (e.g., CoinGate).
    - Fires only when sink_risk_score is high (≥ 0.5).
    - Inbound fee decays automatically with reduced flow (via ema_blended).
    - Discourages inbound payments into already-drained (sink) channels.
    """
    if inbound_fee_skip_check(ctx, "F5"):
        return None
  
    if ctx.sink_risk_score < 0.5:
        logger.debug(
            f"F5 not fired: sink_risk_score={ctx.sink_risk_score} too low",
            extra={'section': ctx.alias},
        )
        return None

    if ctx.alias in ctx.policy.rules.get("sink_guard_disabled", []):
        logger.info(
            f"F5 skipped: {ctx.alias} in sink_guard_disabled",
            extra={'section': ctx.alias},
        )
        return None

    if ctx.alias in ctx.policy.rules.get("inbound_fees_disabled", []):
        logger.debug(
            f"F5 not fired: {ctx.alias} in inbound_fees_disabled",
            extra={'section': ctx.alias},
        )
        return None

    min_fee = 0
    max_fee = ctx.policy.inbound_fees.max_fee_ppm
    current_inbound_fee = ctx.inbound_fee

    # Inbound fee scales with recent volume; you can tweak divisor as you wish
    inbound_fee = min(int(min_fee + (ctx.ema_blended / 4000)), max_fee)
    if current_inbound_fee > 0:
        inbound_fee = min(inbound_fee, current_inbound_fee)
    
    if inbound_fee == current_inbound_fee:
        logger.debug(
            f"F5 not fired: inbound_fee unchanged at {inbound_fee}.",
            extra={'section': ctx.alias},
        )
        return None
        
    logger.info(
        f"F5 rule fired: setting inbound_fee={inbound_fee} for {ctx.alias} (sink risk {ctx.sink_risk_score:.2f})",
        extra={'section': ctx.alias},
    )
    
    return RuleResult(
        "F5_sink_inbound_tax",
        ctx.min_fee,
        ctx.max_fee,  # outbound unchanged
        55,
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
        logger.debug(
            f"F6 not fired: inbound_fee={ctx.inbound_fee} already zero.",
            extra={'section': ctx.alias},
        )
        return None

    # Stop taxing entirely if node starts hoarding again
    if ctx.sink_risk_score < 0.5:
        logger.info(
            f"F6 triggered: sink_risk_score={ctx.sink_risk_score} < 0.5, resetting inbound_fee.",
            extra={'section': ctx.alias},
        )
        return RuleResult(
            "F6_inbound_fee_decay", ctx.min_fee, ctx.max_fee, 60, inbound_fee=0
        )

    # Thresholds
    decay_threshold = 100_000
    min_decay_pct = 0.85  # Don't drop more than 15%
    max_decay_ppm = 100  # Never drop more than 100ppm per run

    # Decay condition
    if ctx.ema_blended < decay_threshold:
        scale = max(
            ctx.ema_blended / decay_threshold, min_decay_pct
        )  # clamp to avoid sharp drops
        target_fee = int(ctx.inbound_fee * scale)
        decayed_fee = max(ctx.inbound_fee - max_decay_ppm, target_fee)
        if decayed_fee < ctx.inbound_fee:
            logger.info(
                f"F6 fired: ema_blended={ctx.ema_blended} < {decay_threshold}, decaying inbound_fee {ctx.inbound_fee} -> {decayed_fee}.",
                extra={'section': ctx.alias},
            )
            return RuleResult(
                "F6_inbound_fee_decay",
                ctx.min_fee,
                ctx.max_fee,
                55,
                inbound_fee=decayed_fee,
            )
        else:
            logger.debug(
                f"F6 checked: target_fee={target_fee}, decayed_fee={decayed_fee} but no drop.",
                extra={'section': ctx.alias},
            )

    logger.debug(
        f"F6 not fired: ema_blended={ctx.ema_blended} >= {decay_threshold} or decay not warranted.",
        extra={'section': ctx.alias},
    )
    return None


def rule_f7_subsidise_inbound(ctx: Context):
    """
    Subsidise inbound for channels with low outbound (tap state, not sink).
    Sets inbound_fee negative if <10% outbound and sink risk is low.
    Resets to zero otherwise.
    """
    if inbound_fee_skip_check(ctx, "F7"):
        return None
    
    # Only subsidize inbound if NOT at high sink risk
    if ctx.sink_risk_score >= 0.5:
        logger.debug(
            f"F7 not fired: sink_risk_score={ctx.sink_risk_score} >= 0.5, not subsidising inbound.",
            extra={'section': ctx.alias},
        )
        return None

    if ctx.percentage_outbound > 0.1:
        if ctx.inbound_fee < 0:
            inbound_fee = 0
            logger.info(
                f"F7 triggered: percentage_outbound={ctx.percentage_outbound:.2%} > 10%, resetting negative inbound_fee to 0.",
                extra={'section': ctx.alias},
            )
        else:
            logger.debug(
                f"F7 not fired: percentage_outbound={ctx.percentage_outbound:.2%} > 10%, inbound_fee already non-negative.",
                extra={'section': ctx.alias},
            )
            return None
    else:
        inbound_fee = -ctx.min_fee
        logger.info(
            f"F7 fired: percentage_outbound={ctx.percentage_outbound:.2%} <= 10%, subsidising inbound with fee {inbound_fee}.",
            extra={'section': ctx.alias},
        )

    return RuleResult(
        "F7_subsidise_inbound", ctx.min_fee, ctx.max_fee, 50, inbound_fee=inbound_fee
    )

    
def rule_h1_high_htlc_fail_rate(ctx):
    """
    H1: Respond to high HTLC failure rates.
    - If both short-term and long-term fail rates are high, escalate more.
    - Thresholds are set in params.toml under [htlc].
    Uses .channel_data["htlc_stats"] for current rolling rates.
    """
    htlc_stats = ctx.peer_mem.get("htlc_stats", {})
    # Defensive for int/str keys
    rate_1h = htlc_stats.get(3600, {}).get("fail_rate") or htlc_stats.get("3600", {}).get("fail_rate", 0)
    rate_24h = htlc_stats.get(86400, {}).get("fail_rate") or htlc_stats.get("86400", {}).get("fail_rate", 0)

    number_failed_events_1h = htlc_stats.get(3600, {}).get("fails") or htlc_stats.get("3600", {}).get("fails", 0)

    policy_htlc = ctx.policy.get("htlc", {})
    SHORT_TERM_HIGH = policy_htlc.get("fail_short_term", 0.4)
    SHORT_TERM_THRESHOLD = policy_htlc.get("fail_short_term_threshold", 25)
    LONG_TERM_HIGH = policy_htlc.get("fail_long_term", 0.3)
    FEE_INC = ctx.policy["fees"]["increment_ppm"]
    FEE_MAX = ctx.policy["fees"]["max_ppm"]
    FEE_MIN = ctx.policy["fees"]["min_ppm"]

    max_fee_ppm = ctx.max_fee
    min_fee_ppm = ctx.min_fee

    logger.debug(f"H1 rule check: fail_count={number_failed_events_1h} fail_rate_1h={rate_1h:.3f}, fail_rate_24h={rate_24h:.3f}, thresholds=({SHORT_TERM_THRESHOLD}, {SHORT_TERM_HIGH}, {LONG_TERM_HIGH})", extra={'section': ctx.alias})

    def next_fee(current, increment):
        if current < increment:
            return min(current + 1, increment)
        else:
            return current + increment

    if rate_1h > SHORT_TERM_HIGH and number_failed_events_1h > SHORT_TERM_THRESHOLD and rate_24h > LONG_TERM_HIGH:
        new_max_fee = min(next_fee(max_fee_ppm, FEE_INC), FEE_MAX)
        new_min_fee = int(new_max_fee / 2)
        logger.info(f"H1 rule fired: High fail rate (1h={rate_1h:.2%}, 24h={rate_24h:.2%}). Raising max_fee to {new_max_fee}.", extra={'section': ctx.alias})
        weight = 110
    elif rate_1h > SHORT_TERM_HIGH and number_failed_events_1h > SHORT_TERM_THRESHOLD:
        new_max_fee = min(next_fee(max_fee_ppm, FEE_INC), FEE_MAX)
        new_min_fee = int(new_max_fee / 2)
        logger.info(f"H1 rule fired: Short-term fail rate high (1h={rate_1h:.2%}). Raising max_fee to {new_max_fee}.", extra={'section': ctx.alias})
        weight = 90
    else:
        logger.debug(f"H1 rule not fired: fail rates below threshold.", extra={'section': ctx.alias})
        return None

    return RuleResult(
        "H1_high_htlc_fail_rate",
        new_min_fee,
        new_max_fee,
        weight,
        cooldown_override=True, 
    )
    

def rule_h2_dynamic_inbound_fee(ctx: Context):
    """
    H2: Unified Adaptive Inbound Fee
    - Raises inbound fee when channel is filling (outbound_pct > 75% or sink risk high or EMA rising).
    - Subsidises inbound (negative fee) when channel is emptying (outbound_pct < 25% or sink risk low or EMA falling).
    - Blends outbound_pct, sink_risk_score, and EMA delta for more adaptive behaviour.
    - Ignores tiny EMA delta to avoid noise.
    - Incorporates HTLC fail rates as a veto/override.
    """
    if inbound_fee_skip_check(ctx, "H2"):
        return None

    channel_conf = ctx.policy.channels.get(ctx.alias, {})
    inbound_conf = ctx.policy.get("inbound_fees", {})
    # Thresholds
    OUTBOUND_HIGH = inbound_conf.get("sink_pct", 0.75)
    OUTBOUND_LOW  = inbound_conf.get("tap_pct", 0.25)
    RISK_HIGH = inbound_conf.get("risk_high", 0.7)
    RISK_LOW  = inbound_conf.get("risk_low", 0.3)
    DELTA_THRESH = ctx.DELTA_THRESHOLD
    increment = inbound_conf.get("increment_ppm", 25)
    max_positive_ppm = channel_conf.get("max_fee_ppm") or inbound_conf.get("max_fee_ppm", 1500)
    min_negative_ppm = channel_conf.get("min_fee_ppm") or inbound_conf.get("min_fee_ppm", -100)
    #htlc_fail_limit = inbound_conf.get("max_htlc_inbound_fail_rate", 0.2)

    max_step = 2 * increment
    ema_blended = max(ctx.ema_blended, 1)
    percent_delta = abs(ctx.ema_delta) / ema_blended
    step = int(increment * percent_delta)
    step = max(1, min(step, max_step))

    # Current stats
    outbound_pct = ctx.percentage_outbound
    sink_risk = ctx.sink_risk_score
    ema_delta = ctx.ema_delta

    # Defensive stats lookup for HTLC fails
    htlc_stats = ctx.peer_mem.get("htlc_stats", {})
    stats_1h = htlc_stats.get(3600) or htlc_stats.get("3600", {})
    fail_rate_1h = stats_1h.get("fail_rate", 0)

    # --- Veto if HTLC fails are excessive (don't increase fee if payments are failing) ---
    #if fail_rate_1h > htlc_fail_limit:
        #logger.info(
            #f"H2 rule not fired: fail_rate_1h={fail_rate_1h:.2%} exceeds limit {htlc_fail_limit:.2%}, not increasing inbound_fee.",
            #extra={"section": ctx.alias}
        #)
        #return None

    # --- Raise inbound fee if channel is filling ---
    if (
        outbound_pct > OUTBOUND_HIGH or
        ((sink_risk > RISK_HIGH or
        ema_delta > ema_blended * DELTA_THRESH) and outbound_pct > OUTBOUND_LOW)
    ):
        # If any one signal says "filling", prefer to penalise inbound
        new_inbound_fee = min(ctx.inbound_fee + step, max_positive_ppm)
        logger.info(
            f"H2 fired (filling): outbound_pct={outbound_pct:.2%}, sink_risk={sink_risk:.2f}, ema_delta={ema_delta}, "
            f"→ Raising inbound_fee to {new_inbound_fee}",
            extra={"section": ctx.alias}
        )
        if new_inbound_fee != ctx.inbound_fee:
            return RuleResult(
                "H2_adaptive_inbound_fee_fill", ctx.min_fee, ctx.max_fee, 70, inbound_fee=new_inbound_fee
            )

    # --- Lower/subsidise inbound fee if channel is draining ---
    if (
        outbound_pct <= OUTBOUND_LOW or
        sink_risk < RISK_LOW or
        ema_delta < -ema_blended * DELTA_THRESH
    ):
        # If any one signal says "draining", attract inbound by lowering
        new_inbound_fee = max(ctx.inbound_fee - step, min_negative_ppm, -ctx.min_fee)
        logger.info(
            f"H2 fired (draining): outbound_pct={outbound_pct:.2%}, sink_risk={sink_risk:.2f}, ema_delta={ema_delta}, "
            f"→ Lowering inbound_fee to {new_inbound_fee}",
            extra={"section": ctx.alias}
        )
        if new_inbound_fee != ctx.inbound_fee:
            return RuleResult(
                "H2_adaptive_inbound_fee_drain", ctx.min_fee, ctx.max_fee, 70, inbound_fee=new_inbound_fee
            )

    # --- No action ---
    logger.debug(
        f"H2 not fired: outbound_pct={outbound_pct:.2%}, sink_risk={sink_risk:.2f}, ema_delta={ema_delta}",
        extra={"section": ctx.alias}
    )
    return None
 
 
# ── 3.  SKIPS ───────────────────────────────────────────────────────────────

def fee_increase_skip_check(ctx, rule): 
    if (
    abs(ctx.ema_blended - ctx.prev_ema_blended) < 0.01 * max(abs(ctx.prev_ema_blended), 1) and
    abs(ctx.rev_ema_blended - ctx.prev_rev_ema_blended) < 0.01 * max(abs(ctx.prev_rev_ema_blended), 1)
):
        logger.debug(f"{rule} rule not fired: No significant increase in volume or revenue EMA.", extra={'section': ctx.alias})
        return True
    return False 


def fee_decay_skip_check(ctx, rule):

    if ctx.fee <= ctx.min_fee:
        logger.debug(f"{rule} rule not fired: Fee already at minimum.", extra={'section': ctx.alias})
        return True

    if ctx.ema_blended >= ctx.prev_ema_blended or abs(ctx.ema_blended - ctx.prev_ema_blended) < 0.01 * max(abs(ctx.prev_ema_blended), 1):
        logger.debug(f"{rule} rule not fired: EMA not dropping significantly.", extra={'section': ctx.alias})
        return True
    return False 
    
    
def inbound_fee_skip_check(ctx, rule):

    if abs(ctx.ema_blended - ctx.prev_ema_blended) < 0.01 * max(abs(ctx.prev_ema_blended), 1):
        logger.debug(f"{rule} Inbound fee rule not fired: No significant change in EMA.", extra={'section': ctx.alias})
        return True
    return False 

# ── 4.   RULE ENGINE ───────────────────────────────────────────────────────────



ALL_RULES = [
    # Group H - HTLC driven rules
    rule_h1_high_htlc_fail_rate,
    rule_h2_dynamic_inbound_fee,
    # Group F – role-flip & predictive stabilisers
    rule_f1_role_flip_freeze,
    rule_f2_tap_surge_boost,
    rule_f3_sink_ema_guard,
    rule_f4_sink_score_guard,
    rule_f5_sink_inbound_tax,
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
        ):
            inbound_rules.append(result)

        if (
            result.new_min != ctx.min_fee or result.new_max != ctx.max_fee
        ):
            outbound_rules.append(result)

    best_outbound = max(outbound_rules, key=lambda r: r.weight, default=None)
    best_inbound = max(inbound_rules, key=lambda r: r.weight, default=None)
    
    if best_outbound:
        logger.info("Best outbound: %s", best_outbound.rule_id, extra={'section': ctx.alias})
    if best_inbound:
        logger.info("Best inbound: %s", best_inbound.rule_id, extra={'section': ctx.alias})
    return best_outbound, best_inbound
