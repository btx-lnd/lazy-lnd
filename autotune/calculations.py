import logging

logger = logging.getLogger(__name__)

def update_all_rolling_stats(state):
    """
    For a given state (peer section), update rolling mean/std for key stats.
    Modifies state in place.
    """
    alias = state.get("alias", "unknown")

    # Rolling stats for EMAs
    for stat in ["ema_blended", "rev_ema_blended"]:
        value = state.get(stat)
        if value is not None:
            hist_key = f"{stat}_history"
            state.setdefault(hist_key, {})
            logger.debug(f"Updating rolling stats for {stat}: value={value}", extra={'section': alias})
            update_rolling_stats(state[hist_key], value)
        else:
            logger.info(f"Skipping rolling stats for {stat} (no value present)", extra={'section': alias})

    # Rolling stats for HTLC fail rates (per window)
    htlc_stats = state.get("htlc_stats", {})
    for win in (3600, 86400):
        # Defensive for int/str keys
        stat_obj = htlc_stats.get(win) or htlc_stats.get(str(win)) or {}
        fail_rate = stat_obj.get("fail_rate", 0)
        hist_key = f"fail_rate_{win}_history"
        state.setdefault(hist_key, {})
        logger.debug(f"Updating rolling stats for fail_rate_{win}: fail_rate={fail_rate}", extra={'section': alias})
        update_rolling_stats(state[hist_key], fail_rate)
        
    logger.info(f"Rolling stats updated: ema_blended={state.get('ema_blended_history', {})}, "
                f"rev_ema_blended={state.get('rev_ema_blended_history', {})}, "
                f"fail_rate_3600={state.get('fail_rate_3600_history', {})}, "
                f"fail_rate_86400={state.get('fail_rate_86400_history', {})}", extra={'section': alias})
    return state

def update_rolling_stats(history, new_value):
    """
    Update running mean and stddev (Welford’s method).
    Modifies dict in place.
    """
    n = history.get("n", 0) + 1
    mean = history.get("mean", 0)
    M2 = history.get("M2", 0)

    delta = new_value - mean
    mean += delta / n
    delta2 = new_value - mean
    M2 += delta * delta2

    std = (M2 / n) ** 0.5 if n > 1 else 0

    history.update({
        "mean": mean,
        "M2": M2,
        "std": std,
        "n": n,
    })
    return history
 
 
def compute_sink_risk_score(state_section):
    """
    Predict sink risk based on declining volume, drying revenue,
    repeated fee bumps, outbound hoarding, and liquidity imbalance.
    Adapts to each channel's normal using rolling mean and std.
    Returns a score between 0.0 and 1.0.
    """
    # Live/current metrics
    ema_blended = state_section.get("ema_blended", 0)
    ema_delta = state_section.get("ema_delta", 0)
    rev_ema_blended = state_section.get("rev_ema_blended", 0)
    rev_delta = state_section.get("rev_delta", 0)
    zero_ema_count = state_section.get("zero_ema_count", 0)
    fee_bump_streak = state_section.get("fee_bump_streak", 0)
    fail_rate_1h = state_section.get("htlc_stats", {}).get(3600, {}).get("fail_rate", 0)
    percentage_outbound = state_section.get("peer_outbound_percent", 0)
    alias = state_section.get("alias", "unknown")

    # Rolling stats
    ema_hist = state_section.get("ema_blended_history", {})
    rev_ema_hist = state_section.get("rev_ema_blended_history", {})
    fail_rate_hist = state_section.get("fail_rate_3600_history", {})
    ema_mean = ema_hist.get("mean", 0)
    ema_std = ema_hist.get("std", 0)
    rev_ema_mean = rev_ema_hist.get("mean", 0)
    rev_ema_std = rev_ema_hist.get("std", 0)
    fail_mean = fail_rate_hist.get("mean", 0)
    fail_std = fail_rate_hist.get("std", 0)
    n = ema_hist.get("n", 0)

    score = 0.0
    enough_history = n >= 100
    recovery = False

    logger.debug(
        f"[{alias}] Sink risk scoring: out_pct={percentage_outbound:.2%}, "
        f"ema={ema_blended}, rev_ema={rev_ema_blended}, fail_rate_1h={fail_rate_1h:.2%}, n={n}, "
        f"means=(ema:{ema_mean}, rev:{rev_ema_mean}, fail:{fail_mean}) "
        f"stds=(ema:{ema_std}, rev:{rev_ema_std}, fail:{fail_std})",
        extra={'section': alias}
    )

    # Outbound balance is the dominant signal (sinks are all-outbound)
    if percentage_outbound <= 0.1:
        score += 0.5
        logger.info(f"Sink risk: outbound_pct {percentage_outbound:.2%} ≤ 10%", extra={'section': alias})
    elif percentage_outbound <= 0.2:
        score += 0.3
        logger.info(f"Sink risk: outbound_pct {percentage_outbound:.2%} ≤ 20%", extra={'section': alias})
    elif percentage_outbound <= 0.3:
        score += 0.15
        logger.info(f"Sink risk: outbound_pct {percentage_outbound:.2%} ≤ 30%", extra={'section': alias})
    elif percentage_outbound <= 0.4:
        score += 0.05
        logger.info(f"Sink risk: outbound_pct {percentage_outbound:.2%} ≤ 40%", extra={'section': alias})
    elif percentage_outbound >= 0.7:
        score -= 0.5
        logger.info(f"Sink risk: outbound_pct {percentage_outbound:.2%} ≥ 80% (likely tap)", extra={'section': alias})

    if enough_history:
        if ema_blended < max(ema_mean - ema_std, 0):
            score += 0.2
            logger.info(f"Sink risk: EMA {ema_blended:.1f} < mean-std ({ema_mean-ema_std:.1f})", extra={'section': alias})
        if rev_ema_blended < max(rev_ema_mean - rev_ema_std, 0):
            score += 0.1
            logger.info(f"Sink risk: Revenue EMA {rev_ema_blended:.1f} < mean-std ({rev_ema_mean-rev_ema_std:.1f})", extra={'section': alias})
        if fail_std > 0 and fail_rate_1h > fail_mean + 2 * fail_std:
            score += 0.1
            logger.info(f"Sink risk: 1h fail rate {fail_rate_1h:.2%} > 2σ above mean ({fail_mean+2*fail_std:.2%})", extra={'section': alias})
        if ema_blended > ema_mean + ema_std:
            score -= 0.2
            logger.info(f"Sink recovery: EMA {ema_blended} above mean+std ({ema_mean+ema_std:.2f})", extra={'section': alias})
            recovery = True
        if rev_ema_blended > rev_ema_mean + rev_ema_std:
            score -= 0.2
            logger.info(f"Sink recovery: Revenue EMA {rev_ema_blended} above mean+std ({rev_ema_mean+rev_ema_std:.2f})", extra={'section': alias})
            recovery = True
        if fail_std > 0 and fail_rate_1h < max(fail_mean - 2 * fail_std, 0):
            score -= 0.15
            logger.info(f"Sink recovery: 1h fail rate {fail_rate_1h:.2%} < 2 stddev below mean ({fail_mean-2*fail_std:.2%})", extra={'section': alias})
            recovery = True
        # Big step-down if all three strong
        if recovery and score < 0:
            logger.info(f"Sink recovery: Strong signals across all metrics, resetting score to 0", extra={'section': alias})
            score = 0.0
    
    else:
        logger.debug(f"Insufficient history (n={n}), using static thresholds", extra={'section': alias})
        if ema_blended < 25_000 and ema_delta < 0:
            score += 0.4
            logger.info(f"Sink risk (static): EMA {ema_blended} < 25_000 and declining", extra={'section': alias})
        elif ema_blended > 50_000 and ema_delta > 0:
            score -= 0.2
            logger.info(f"Sink RECOVERY (static): EMA {ema_blended} > 50_000 and rising", extra={'section': alias})
        if rev_ema_blended < 100 and rev_delta <= 0:
            score += 0.3
            logger.info(f"Sink risk (static): Rev EMA {rev_ema_blended} < 100 and declining", extra={'section': alias})
        elif rev_ema_blended > 500 and rev_delta > 0:
            score -= 0.15
            logger.info(f"Sink RECOVERY (static): Rev EMA {rev_ema_blended} > 500 and rising", extra={'section': alias})
        if zero_ema_count >= 1:
            score += 0.05
            logger.info(f"Sink risk: Zero EMA count {zero_ema_count} ≥ 1", extra={'section': alias})
        if fee_bump_streak >= 5:
            score += 0.05
            logger.info(f"Sink risk: Fee bump streak {fee_bump_streak} ≥ 5", extra={'section': alias})

    prev_score = state_section.get("sink_risk_score", 0.0)
    # Decay if no triggers fired
    if score == 0.0:
        score = max(0.0, prev_score - 0.05)
        logger.info(f"Sink risk: No triggers, decaying score from {prev_score} to {score}", extra={'section': alias})
    else:
        score = min(1.0, prev_score + score)
        logger.info(f"Sink risk: Triggers fired, raising score from {prev_score} to {score}", extra={'section': alias})

    final_score = min(1.0, round(score, 2))
    final_score = max(0, final_score)
    logger.info(f"Final sink risk score: {final_score}", extra={'section': alias})
    return final_score
    
    