import json
import logging
from datetime import datetime, timezone, timedelta, UTC
from collections import defaultdict
from subprocess import check_output
import math

from autotune.rule_engine import Context, evaluate_fee_rules
from autotune.policy_utils import enforce_policy

logger = logging.getLogger(__name__)


def log_fee_change(
    section, old_fee, new_fee, vol, revenue, timestamp, log_file, inbound=False
):
    if old_fee != new_fee:
        with open(log_file, "a") as logf:
            if not inbound:
                logf.write(
                    f"{timestamp.strftime('%Y-%m-%d %H:%M:%S')} - Fee changed for {section}: {old_fee} → {new_fee} ppm (vol={vol}, rev={revenue})\n"
                )
            else:
                logf.write(
                    f"{timestamp.strftime('%Y-%m-%d %H:%M:%S')} - Inbound fee changed for {section}: {old_fee} → {new_fee} ppm\n"
                )


def run_command(cmd):
    try:
        return check_output(cmd, shell=True, text=True)
    except:
        return ""


def get_forwarding_events(lnd_container, fetch_interval_secs):
    now = datetime.now(timezone.utc)
    start_day = int((now - timedelta(days=1)).timestamp())
    start_interval = int((now - timedelta(seconds=fetch_interval_secs)).timestamp())
    end = int(now.timestamp())
    cmd_day = f"docker exec {lnd_container} lncli fwdinghistory --start_time {start_day} --end_time {end} --max_events 50000"

    cmd_interval = f"docker exec {lnd_container} lncli fwdinghistory --start_time {start_interval} --end_time {end} --max_events 50000"

    return run_command(cmd_day), run_command(cmd_interval)


def get_dynamic_delta_threshold(section, thresholds):
    """
    Dynamically compute the fee delta threshold for triggering a fee bump.
    Applies layered business rules for increased/decreased sensitivity,
    based on recent channel activity and state.

    Args:
        section (dict): Current channel state (e.g. streak, EMA, etc)
        thresholds (object): Policy threshold config (attributes, not dict!)

    Returns:
        float: Rounded delta threshold, clamped to [min_delta, max_delta]
    """
    import logging

    logger = logging.getLogger(__name__)

    streak = section.get("fee_bump_streak", 0)
    days_since_flip = section.get("days_since_flip", 999)
    last_ema_delta = abs(section.get("ema_delta", 0))
    last_rev_delta = abs(section.get("rev_delta", 0))
    zero_ema_count = section.get("zero_ema_count", 0)

    base = thresholds.base_delta
    logger.debug(f"[get_dynamic_delta_threshold] Start: base_delta={base}")

    # 1. Early after a role flip: lower the threshold for faster response.
    if days_since_flip <= thresholds.role_flip_days:
        base -= thresholds.role_flip_bonus
        logger.debug(
            f"  Early role flip: days_since_flip={days_since_flip} ≤ {thresholds.role_flip_days}, -role_flip_bonus ({thresholds.role_flip_bonus}), base={base}"
        )

    # 2. Large recent activity: lower threshold for higher sensitivity.
    if (
        last_ema_delta > thresholds.high_ema_delta_threshold
        or last_rev_delta > thresholds.high_rev_delta_threshold
    ):
        base -= thresholds.high_delta_bonus
        logger.debug(
            f"  High EMA/revenue delta: ema_delta={last_ema_delta}, rev_delta={last_rev_delta}, -high_delta_bonus ({thresholds.high_delta_bonus}), base={base}"
        )

    # 3. Sustained bump streak: lower threshold more to maintain momentum.
    if thresholds.mid_streak_min <= streak <= thresholds.mid_streak_max:
        base -= thresholds.mid_streak_bonus
        logger.debug(
            f"  Mid streak: {thresholds.mid_streak_min} ≤ streak={streak} ≤ {thresholds.mid_streak_max}, -mid_streak_bonus ({thresholds.mid_streak_bonus}), base={base}"
        )
    if streak >= thresholds.mid_streak_max + 1:
        base -= thresholds.high_streak_bonus
        logger.debug(
            f"  High streak: streak={streak} ≥ {thresholds.mid_streak_max+1}, -high_streak_bonus ({thresholds.high_streak_bonus}), base={base}"
        )

    # 4. Early streaks (incl. zero): increase threshold (less eager to bump).
    if streak <= getattr(thresholds, "early_streak_max", 0) and streak != 0:
        base += getattr(thresholds, "early_streak_penalty", 0)
        logger.debug(
            f"  Early streak penalty: streak={streak} ≤ {getattr(thresholds, 'early_streak_max', 0)}, +early_streak_penalty ({getattr(thresholds, 'early_streak_penalty', 0)}), base={base}"
        )

    # 5. If stuck in zero-EMA (no volume for many cycles): make less sensitive.
    if zero_ema_count >= getattr(thresholds, "zero_ema_count_threshold", 9999):
        base += getattr(thresholds, "zero_ema_penalty", 0)
        logger.debug(
            f"  Zero EMA penalty: zero_ema_count={zero_ema_count} ≥ {getattr(thresholds, 'zero_ema_count_threshold', 9999)}, +zero_ema_penalty ({getattr(thresholds, 'zero_ema_penalty', 0)}), base={base}"
        )

    # 6. Enforce [min_delta, max_delta] bounds.
    before_bounds = base
    base = max(thresholds.min_delta, min(thresholds.max_delta, base))
    logger.debug(
        f"  Clamp: {before_bounds} → [{thresholds.min_delta}, {thresholds.max_delta}] = {base}"
    )

    base_rounded = round(base, 4)
    logger.debug(f"[get_dynamic_delta_threshold] Final: {base_rounded}")
    return base_rounded


def parse_forwarding_data(forward_json, alias_fragment):
    """
    Parse forwarding history JSON for a specific alias, calculating:
    - Total outbound sats
    - Total inbound sats
    - Total fees earned
    - Per-peer stats for inbound and outbound routing, including attributed fees.

    Args:
        forward_json (str): JSON string of forwarding history.
        alias_fragment (str): Partial alias to search for in peer_alias_in and peer_alias_out.

    Returns:
        dict: Aggregated stats including:
              - "total_in_sats": Total inbound sats routed through this alias
              - "total_out_sats": Total outbound sats routed through this alias
              - "total_fees": Total fees earned by this alias
              - "peer_stats": Per-peer stats with inbound, outbound, and fees
    """
    try:
        data = json.loads(forward_json)
    except Exception as e:
        logger.error(f"Error parsing forwarding history JSON: {e}")
        return {
            "total_in_sats": 0,
            "total_out_sats": 0,
            "total_fees": 0,
            "peer_stats": {},
        }

    # Initialize totals and per-peer stats
    total_in_sats = 0
    total_out_sats = 0
    total_fees = 0
    peer_stats = defaultdict(lambda: {"in": 0, "out": 0, "fees": 0})

    # Iterate through forwarding events
    for event in data.get("forwarding_events", []):
        try:
            ts = int(event["timestamp"])
            date = datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d")

            peer_in = event.get("peer_alias_in", "unknown_peer").strip().lower()
            peer_out = event.get("peer_alias_out", "unknown_peer").strip().lower()

            # Skip invalid peer aliases
            if (
                not peer_in
                or not peer_out
                or "unable to lookup peer" in peer_in
                or "unable to lookup peer" in peer_out
            ):
                continue

            # Match outbound activity for alias_fragment
            if alias_fragment.lower() in peer_out.lower():
                amt_out = int(event.get("amt_out", 0))
                fee = int(event.get("fee", 0))

                total_out_sats += amt_out
                total_fees += fee

                # Update peer stats for outbound activity
                peer_stats[peer_in]["out"] += amt_out  # Outbound to this peer
                peer_stats[peer_in]["fees"] += fee  # Fees attributed to this peer

            # Match inbound activity for alias_fragment
            elif alias_fragment.lower() in peer_in.lower():
                amt_in = int(event.get("amt_in", 0))
                total_in_sats += amt_in

                # Update peer stats for inbound activity
                peer_stats[peer_out]["in"] += amt_in  # Inbound from this peer

        except Exception as e:
            logger.error(f"Error processing event: {e}")
            continue

    return {
        "total_in_sats": total_in_sats,
        "total_out_sats": total_out_sats,
        "total_fees": total_fees,
        "peer_stats": dict(
            peer_stats
        ),  # Convert defaultdict to regular dict for output
    }


def get_existing_fees(lines, section, fees):
    inside = False
    min_fee, max_fee = fees.min_ppm, fees.max_ppm
    for i, line in enumerate(lines):
        if line.strip().lower() == f"[{section}]":
            inside = True
            continue
        if inside and line.strip().startswith("["):
            break
        if inside:
            if "min_fee_ppm" in line and "delta" not in line:
                try:
                    min_fee = int(line.split("=")[1].strip())
                except:
                    pass
            elif "max_fee_ppm" in line:
                try:
                    max_fee = int(line.split("=")[1].strip())
                except:
                    pass
    return min_fee, max_fee


def classify_peer(total_in_sats, total_out_sats, role_ratio):
    """
    Adjust alpha weights dynamically based on role flips.

    Args:
        state (dict): The current state of the channels.
        section (str): The section name (channel).

    Returns:
        tuple: (alpha_1d, alpha_5d, alpha_7d)
    """
    if total_in_sats > total_out_sats * role_ratio:
        return "sink"  # Peer receives significantly more sats than it sends
    elif total_out_sats > total_in_sats * role_ratio:
        return "tap"  # Peer sends significantly more sats than it receives
    else:
        return "balanced"  # Peer has roughly equal inbound and outbound flow


def get_adaptive_alpha(section, alpha):
    """
    Adjust ALPHA values dynamically based on:
    - Recent role flips (prioritize 1D EMA)
    - Zero EMA count (decay responsiveness)
    - Fee bump streak (slow down adaptation if streak is high)
    """
    days_since_flip = section.get("days_since_flip", 999)
    role_flips = section.get("role_flips", [])
    fee_bump_streak = section.get("fee_bump_streak", 0)
    zero_ema_count = section.get("zero_ema_count", 0)

    # Fast adaptation if recently flipped roles
    if (
        days_since_flip <= alpha["role_flip_days"]
        and len(role_flips) >= alpha["min_role_flips"]
    ):
        return (alpha["weighted_1d"], alpha["weighted_5d"], alpha["weighted_7d"])

    # Aggressive decay when stuck with zero EMA
    if zero_ema_count >= alpha["zero_ema_trigger"]:
        return (
            min(
                alpha["zero_ema_max_1d"],
                alpha["balanced_1d"] + alpha["zero_ema_1d_boost"],
            ),
            min(
                alpha["zero_ema_max_5d"],
                alpha["balanced_5d"] + alpha["zero_ema_5d_boost"],
            ),
            min(
                alpha["zero_ema_max_7d"],
                alpha["balanced_7d"] + alpha["zero_ema_7d_boost"],
            ),
        )

    # Smoother adaptation if bumping too fast
    if fee_bump_streak >= alpha["fee_bump_streak_threshold"]:
        return (
            max(
                alpha["fee_bump_min_1d"],
                alpha["balanced_1d"] - alpha["fee_bump_decay_1d"],
            ),
            max(
                alpha["fee_bump_min_5d"],
                alpha["balanced_5d"] - alpha["fee_bump_decay_5d"],
            ),
            max(
                alpha["fee_bump_min_7d"],
                alpha["balanced_7d"] - alpha["fee_bump_decay_7d"],
            ),
        )

    # Default balanced response
    return (alpha["balanced_1d"], alpha["balanced_5d"], alpha["balanced_7d"])


def get_htlc_sizes(section, reserve_deduction, htlc_min_capacity):

    skip_outbound_fee_adjust = section.get("peer_outbound_percent", 0) < htlc_min_capacity
    skip_inbound_fee_adjust = (1 - section.get("peer_outbound_percent", 0)) < htlc_min_capacity
    
    reserve = int(section.get("peer_total_capacity", 0) * 1000 * reserve_deduction)
    outbound = int(section.get("peer_total_local", 0) * 1000)
    
    max_htlc = max(0, outbound - reserve)
    
    section["max_htlc_msat"] = max_htlc
    return section, outbound, skip_outbound_fee_adjust, skip_inbound_fee_adjust


def compute_sink_risk_score(state_section):
    """
    Predicts sink risk based on declining volume, drying revenue,
    repeated fee bumps, and quiet activity.
    Returns a score between 0.0 and 1.0.
    """
    ema_blended = state_section.get("ema_blended", 0)
    ema_delta = state_section.get("ema_delta", 0)
    rev_ema_blended = state_section.get("rev_ema_blended", 0)
    rev_delta = state_section.get("rev_delta", 0)
    zero_ema_count = state_section.get("zero_ema_count", 0)
    fee_bump_streak = state_section.get("fee_bump_streak", 0)

    score = 0.0

    # Looser thresholds
    if ema_blended > 25_000 and ema_delta < 0:
        score += 0.4
    if rev_ema_blended < 100 and rev_delta <= 0:
        score += 0.3
    if zero_ema_count >= 1:
        score += 0.2
    if fee_bump_streak >= 5:
        score += 0.1
    # Add this at the end
    if score < 0.5:
        prev_score = state_section.get("sink_risk_score", 0.0)
        score = max(0.0, prev_score - 0.1)  # decays by 0.1 each run

    return min(1.0, round(score, 2))


def calculate_exponential_fee_bump(current_fee, fee_bump_streak, fees):
    """
    Hybrid exponential bump:
    - Below fee_increment (e.g. 25): bump as 0,1,2,4,8,... until hitting fee_increment
    - After that: standard exponential bumps using fee_increment * 2^streak
    """
    if current_fee < fees.increment_ppm:
        # Soft exponential under fee_increment
        bump = 2**fee_bump_streak
        new_max = min(fees.increment_ppm, current_fee + bump)
    else:
        # Normal exponential bumps
        bump = min(fees.increment_ppm * (2**fee_bump_streak), fees.bump_max)
        new_max = min(fees.max_ppm, current_fee + bump)

    new_min = new_max // 2
    return new_max, new_min, bump


def update_role_state(section, new_role):
    """
    Update the role state for a section, tracking flips and days since the last flip.

    Args:
        section (dict): The section.
        new_role (str): The newly classified role ('sink', 'tap', 'balanced').

    Returns:
        dict: Updated section.
    """
    now = datetime.now(UTC).strftime("%Y-%m-%d")  # Current UTC date
    previous_role = section.get("role", "")

    # Initialize role history if not present
    if "role_flips" not in section:
        section["role_flips"] = []
        section["days_since_flip"] = 0
        section["last_updated"] = now  # Track the last date the state was updated

    # Check if the role has flipped
    if new_role != previous_role:
        # Record the role flip
        section["role_flips"].append({"timestamp": now, "role": new_role})
        if previous_role != "":
            # Reset the days_since_flip counter
            section["days_since_flip"] = 0
        # Update the last updated date
        section["last_updated"] = now
    else:
        # Increment days_since_flip only if a full day has passed
        last_updated = section.get("last_updated", now)
        if last_updated != now:  # A new day has started
            section["days_since_flip"] += 1
            section["last_updated"] = now  # Update the last updated date

    # Update the current role in the state
    section["role"] = new_role
    return section


def process_channel_data(
    section, alias, forwarding_data_day, forwarding_data_init, policy
):
    """Processes channel-specific data like volume, revenue, and EMAs."""

    daily_totals = parse_forwarding_data(forwarding_data_day, alias)
    vol = daily_totals["total_out_sats"]
    section["total_in_sats"] = daily_totals["total_in_sats"]
    section["total_out_sats"] = daily_totals["total_out_sats"]
    revenue = daily_totals["total_fees"]

    ema = section.get("ema_blended", 0)
    ema_target = policy.thresholds.sink_ema_target

    # How far from target (for F3 rule)
    section["ema_from"] = ema_target - ema

    # Sink ratio: in/out
    in_total = section.get("total_in_sats", 1)
    out_total = section.get("total_out_sats", 1)
    section["sink_ratio"] = max(in_total, 1) / max(out_total, 1)

    # Delta from previous ratio, smoothed
    prev = section.get("prev_sink_ratio", section["sink_ratio"])
    delta = section["sink_ratio"] - prev
    section["sink_delta"] = delta
    section["prev_sink_ratio"] = section["sink_ratio"]

    int_totals = parse_forwarding_data(forwarding_data_init, alias)
    vol_int = int_totals["total_out_sats"]
    revenue_int = int_totals["total_fees"]

    role = classify_peer(
        daily_totals["total_in_sats"],
        daily_totals["total_out_sats"],
        policy.thresholds.role_ratio,
    )
    if vol > 0 or revenue > 0:
        section = update_role_state(section, role)
    # Update section with role
    # Load individual EMAs or default to 0
    ema_1d = section.get("ema_1d", 0)
    ema_5d = section.get("ema_5d", 0)
    ema_7d = section.get("ema_7d", 0)

    alpha_1d, alpha_5d, alpha_7d = get_adaptive_alpha(section, policy.alpha)

    # Update EMAs
    ema_1d_new = ema_1d + alpha_1d * (vol - ema_1d)
    ema_5d_new = ema_5d + alpha_5d * (vol - ema_5d)
    ema_7d_new = ema_7d + alpha_7d * (vol - ema_7d)

    # Revenue EMAs
    revenue_ema_1d = section.get("revenue_ema_1d", 0)
    revenue_ema_5d = section.get("revenue_ema_5d", 0)
    revenue_ema_7d = section.get("revenue_ema_7d", 0)
    revenue_ema_1d_new = revenue_ema_1d + alpha_1d * (revenue - revenue_ema_1d)
    revenue_ema_5d_new = revenue_ema_5d + alpha_5d * (revenue - revenue_ema_5d)
    revenue_ema_7d_new = revenue_ema_7d + alpha_7d * (revenue - revenue_ema_7d)

    # Calculate blended EMAs and deltas
    ema_blended = (ema_1d_new + ema_5d_new + ema_7d_new) / 3
    rev_ema_blended = (revenue_ema_1d_new + revenue_ema_5d_new + revenue_ema_7d_new) / 3
    ema_delta = int(vol - ema_blended)
    rev_delta = int(revenue - rev_ema_blended)

    section["ema_blended"] = ema_blended
    section["ema_delta"] = ema_delta
    section["rev_ema_blended"] = rev_ema_blended
    section["rev_delta"] = rev_delta

    return {
        "vol": vol,
        "revenue": revenue,
        "ema_1d": ema_1d,
        "ema_5d": ema_5d,
        "ema_7d": ema_7d,
        "ema_1d_new": ema_1d_new,
        "ema_5d_new": ema_5d_new,
        "ema_7d_new": ema_7d_new,
        "revenue_ema_1d": revenue_ema_1d,
        "revenue_ema_5d": revenue_ema_5d,
        "revenue_ema_7d": revenue_ema_7d,
        "revenue_ema_1d_new": revenue_ema_1d_new,
        "revenue_ema_5d_new": revenue_ema_5d_new,
        "revenue_ema_7d_new": revenue_ema_7d_new,
        "vol_int": vol_int,
        "revenue_int": revenue_int,
    }, section


def adjust_channel_fees(
    section,
    section_name,
    channel_data,
    lines,
    now,
    observe_only,
    dry_run,
    skip_outbound_fee_adjust,
    skip_inbound_fee_adjust,
    final_report_logs,  # pass by reference so logs are appended
    rule_stats,
    policy,
):
    # Unpack channel data and state as before
    vol = max(channel_data["vol"], 0)
    revenue = max(channel_data["revenue"], 0)

    ema_1d = channel_data["ema_1d"]
    ema_5d = channel_data["ema_5d"]
    ema_7d = channel_data["ema_7d"]
    ema_1d_new = channel_data["ema_1d_new"]
    ema_5d_new = channel_data["ema_5d_new"]
    ema_7d_new = channel_data["ema_7d_new"]
    revenue_ema_1d = channel_data["revenue_ema_1d"]
    revenue_ema_5d = channel_data["revenue_ema_5d"]
    revenue_ema_7d = channel_data["revenue_ema_7d"]
    revenue_ema_1d_new = channel_data["revenue_ema_1d_new"]
    revenue_ema_5d_new = channel_data["revenue_ema_5d_new"]
    revenue_ema_7d_new = channel_data["revenue_ema_7d_new"]
    vol_int = channel_data["vol_int"]
    revenue_int = channel_data["revenue_int"]
    outbound = channel_data.get("outbound", 0)  # if available

    ema_blended = section.get("ema_blended", 0)
    ema_delta = section.get("ema_delta", 0)
    rev_ema_blended = section.get("rev_ema_blended", 0)
    rev_delta = section.get("rev_delta", 0)

    min_fee, max_fee = get_existing_fees(lines, section_name, policy.fees)
    fee = section.get("fee", max_fee)

    if vol_int > 0:
        section["last_successful_fee"] = fee
    last_successful_fee = section.get("last_successful_fee", -1)
    last_daily_vol = section.get("last_daily_vol", vol)

    new_max = fee
    new_min = round(new_max * policy.fees.min_max_ratio)
    inbound_fee = section.get("inbound_fee", 0)
    old_fees = {
        "min_fee_ppm": min_fee,
        "max_fee_ppm": max_fee,
        "inbound_fee_ppm": inbound_fee,
    }

    fee_bump_streak = section.get("fee_bump_streak", 0)
    cooldown_until_str = section.get("cooldown_until")
    cooldown_until = (
        datetime.fromisoformat(cooldown_until_str)
        if cooldown_until_str
        else datetime.min.replace(tzinfo=timezone.utc)
    )
    cooldown = now <= cooldown_until
    cooldown_override = False
    sink_risk_score = section.get("sink_risk_score")
    zero_ema_count = section.get("zero_ema_count", 0)
    failed_htlc_count = section.get("htlc_fail_count", 0)
    fail_time_str = section.get("fee_increase_failed_at")
    fail_time = datetime.fromisoformat(fail_time_str) if fail_time_str else None
    bump_time_str = section.get("fee_bump_attempted_at")
    bump_time = datetime.fromisoformat(bump_time_str) if bump_time_str else None

    new_inbound_fee = inbound_fee
    rule_id = None
    outbound_updated = False
    inbound_updated = False
    fee_bump_applied = False
    percentage_outbound = section.get("peer_outbound_percent", 0.5)
    rule_ids = []
    # -- Core fee adjustment logic --
    final_report_logs.append(
        f"{section['alias']}: observe: {observe_only}, cooldown: {cooldown}, skip_outbound/inbound_fee_adjust: {skip_outbound_fee_adjust}/{skip_inbound_fee_adjust}"
    )
    if not observe_only and not cooldown:
        if failed_htlc_count >= policy.htlc.failed_htlc_threshold:
            new_max, new_min, bump_amount = calculate_exponential_fee_bump(
                max_fee, fee_bump_streak, policy.fees
            )
            section["htlc_fail_count"] = 0
            if final_report_logs is not None:
                final_report_logs.append(
                    f"Failed HTLC count {failed_htlc_count} triggered fee bump for {section}"
                )
        else:
            # ------------------------------------------------------------------
            # Modular rule engine (replaces legacy A-H ladder)
            # ------------------------------------------------------------------
            delta_threshold = get_dynamic_delta_threshold(section, policy.thresholds)
            ctx = Context(
                alias=section.get("alias"),
                vol=vol,
                vol_int=vol_int,
                revenue=revenue,
                ema_blended=ema_blended,
                ema_delta=ema_delta,
                rev_ema_blended=rev_ema_blended,
                rev_delta=rev_delta,
                last_daily_vol=last_daily_vol,
                fee=fee,
                min_fee=min_fee,
                max_fee=max_fee,
                inbound_fee=inbound_fee,
                fee_bump_streak=fee_bump_streak,
                zero_ema_count=zero_ema_count,
                role=section.get("role_override") or section.get("role", "undefined"),
                days_since_flip=section.get("days_since_flip", 999),
                FEE_INCREMENT_PPM=policy.fees.increment_ppm,
                FEE_MIN_PPM=policy.fees.min_ppm,
                FEE_MAX_PPM=policy.fees.max_ppm,
                DELTA_THRESHOLD=delta_threshold,
                REVENUE_THRESHOLD=policy.thresholds.revenue,
                FEE_BUMP_MAX=policy.fees.bump_max,
                policy=policy,
                sink_ratio=section.get("sink_ratio", 1.0),
                sink_delta=section.get("sink_delta", 0.0),
                sink_risk_score=sink_risk_score,
                ema_from_target=section.get("ema_from", 0),
                calculate_exponential_fee_bump=calculate_exponential_fee_bump,
                percentage_outbound=percentage_outbound,
                skip_outbound_fee_adjust=skip_outbound_fee_adjust,
                skip_inbound_fee_adjust=skip_inbound_fee_adjust,
            )

            best_outbound, best_inbound = evaluate_fee_rules(ctx)
            rule_stats = rule_stats or {}

            # === Outbound Fee Application ===

            if best_outbound and not skip_outbound_fee_adjust:
                outbound_rule_id, new_min, new_max, _ = best_outbound
                rule_ids.append(outbound_rule_id)
                if getattr(best_outbound, "override_cooldown", False):
                    cooldown = False

                if outbound_rule_id not in rule_stats:
                    rule_stats[outbound_rule_id] = {
                        "fired": 0,
                        "applied": 0,
                        "skipped": 0,
                    }
                rule_stats[outbound_rule_id]["applied"] += 1

                final_report_logs.append(
                    f"{section['alias']}: Fee change to {new_min}/{new_max} via {outbound_rule_id}"
                )
                outbound_updated = True
                if outbound_rule_id == "F3_ema_sink_guard":
                    final_report_logs.append(
                        f"{section}['alias']: Sink protection triggered. "
                        f"Ratio={section['sink_ratio']:.2f}, "
                        f"Δ={section['sink_delta']:.2f}, "
                        f"EMA gap={section['ema_from']}"
                    )
                    cooldown_override = True
                    cooldown = False
            else:
                new_min, new_max = min_fee, max_fee

            # === Inbound Fee Application ===
            if (
                best_inbound
                and hasattr(best_inbound, "inbound_fee")
                and not skip_inbound_fee_adjust
            ):
                new_inbound_fee = best_inbound.inbound_fee
                inbound_rule_id = best_inbound.rule_id
                rule_ids.append(inbound_rule_id)

                if inbound_rule_id not in rule_stats:
                    rule_stats[inbound_rule_id] = {
                        "fired": 0,
                        "applied": 0,
                        "skipped": 0,
                    }
                rule_stats[inbound_rule_id]["applied"] += 1
                inbound_updated = True
                final_report_logs.append(
                    f"{section['alias']}: Inbound fee set to {new_inbound_fee}ppm via {inbound_rule_id}"
                )

            # === Fee bump flag
            fee_bump_applied = new_max > max_fee

    new_min = math.floor(new_min)
    new_max = math.ceil(new_max)
    new_inbound_fee = math.floor(new_inbound_fee)
    new_fees = {
        "min_fee_ppm": new_min,
        "max_fee_ppm": new_max,
        "inbound_fee_ppm": new_inbound_fee,
    }

    # -- catch negative fees --
    if new_max < 0 or new_min < 0:
        final_report_logs.append(
            f"ERROR: {section['alias']}: Fee was negative {new_min}/{new_max}. Reset to 0."
        )
        new_max = 0
        new_min = 0

    # -- failure cooldown logic --
    fail_cooldown = False
    if (
        fee_bump_applied
        and fail_time
        and (now - fail_time < timedelta(hours=policy.timing.fee_backoff_hours))
    ):
        if cooldown_override:
            final_report_logs.append(
                f"{section['alias']}: Fee bump to {new_min}/{new_max} NOT skipped. Cooldown override: {cooldown_override}"
            )
            outbound_updated = True
        else:
            final_report_logs.append(
                f"{section['alias']}: Fee bump to {new_min}/{new_max} skipped. In failed cooldown period"
            )
            fail_cooldown = True
    if (new_min != min_fee or new_max != max_fee) and not fail_cooldown:
        final_report_logs.append(f"{section['alias']}: Fee change will apply")
        outbound_updated = True
        # -- Fee streak and raise/lower tracking --
    if not observe_only and not cooldown and not skip_outbound_fee_adjust:
        if (
            not fee_bump_applied
            and bump_time
            and (now - bump_time)
            < timedelta(hours=policy.timing.failed_bump_flag_hours)
        ):
            section["fee_increase_failed_at"] = now.isoformat()
            section["fee_bump_streak"] = 0
            final_report_logs.append(
                f"{section['alias']}: Fee bump reset and bump attempt mark failed"
            )
        elif (
            not fee_bump_applied and not cooldown and new_max == max_fee
        ) or fail_cooldown:
            section["fee_bump_streak"] = 0
            final_report_logs.append(f"{section['alias']}: Fee bump reset")
        elif fee_bump_applied:
            section["fee_bump_attempted_at"] = (now - timedelta(minutes=1)).isoformat()
            section["fee_bump_streak"] = fee_bump_streak + 1
            final_report_logs.append(
                f"{section['alias']}: Fee bump increased to {section['fee_bump_streak']}"
            )

    # -- State and logging updates --
    if outbound_updated:
        log_fee_change(
            section["alias"], max_fee, new_max, vol, revenue, now, policy.paths.log_file
        )
        section["fee"] = new_max

        if rule_id:
            rule_stats[rule_id]["fired"] += 1
            if (new_max != max_fee) or (new_min != min_fee):
                rule_stats[rule_id]["applied"] += 1
            else:
                rule_stats[rule_id]["skipped"] += 1
        if new_max > policy.fees.increment_ppm:
            cooldown_period = timedelta(
                minutes=((policy.timing.cooldown_hours * 60) - 1)
            )
        else:
            cooldown_period = timedelta(minutes=((1 * 60) - 1))
        section["cooldown_until"] = (now + cooldown_period).isoformat()

    if inbound_updated:
        log_fee_change(
            section["alias"],
            inbound_fee,
            new_inbound_fee,
            vol,
            revenue,
            now,
            policy.paths.log_file,
            inbound=True,
        )
        section["inbound_fee"] = new_inbound_fee

    if failed_htlc_count < policy.htlc.failed_htlc_threshold:
        section["htlc_fail_count"] = failed_htlc_count

    if ema_blended <= 1000:
        if "zero_ema_count" in section:
            section["zero_ema_count"] += 1
        else:
            section["zero_ema_count"] = 1
        section["sink_score_high_count"] = 0
        section["sink_score_low_count"] = 0
        section["neutral_sink_score_count"] = 0
    else:
        section["zero_ema_count"] = 0

    # These are safe in observe_only too
    section["last_daily_vol"] = vol
    section["prev_ema_blended"] = ema_blended
    section["ema_1d"] = ema_1d_new
    section["ema_5d"] = ema_5d_new
    section["ema_7d"] = ema_7d_new
    section["revenue_ema_1d"] = revenue_ema_1d_new
    section["revenue_ema_5d"] = revenue_ema_5d_new
    section["revenue_ema_7d"] = revenue_ema_7d_new
    section["htlc_stuck_count"] = 0

    role = section.get("role_override") or section.get("role", "undefined")
    final_report_logs.append(
        f"{section['alias']}: sink_score={sink_risk_score:.2f} "
        f"(ema={ema_blended}, Δ={ema_delta}, rev={rev_ema_blended}, Δrev={rev_delta})"
    )
    role_raw = section.get("role", "undefined")
    role_effective = role
    if role_effective != role_raw:
        role_effective += "*"
    now_str = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    logger.info(
        f"{now_str} [{section['alias']}] fwd={vol} emaΔ={ema_delta:+} ema={int(ema_1d)}→{int(ema_blended)} "
        f"fee={max_fee}->{new_max} in={new_inbound_fee} rev={revenue} role={role_effective} sink={sink_risk_score:.2f}  sink ratio={section['sink_ratio']:.2f} "
        f"sinkΔ={section['sink_delta']:.2f} ema gap={section['ema_from']}"
    )

    return section, final_report_logs, old_fees, new_fees, rule_ids


def recommend_and_update_fees(
    section_name,
    alias,
    policy,
    peer_mem,
    lines,
    now,
    observe_only,
    dry_run,
    final_report_logs,
    rule_stats,
    forward_data_day,
    forward_data_int,
):
    """
    Core per-peer pipeline. Run your fee/EMA/role/logic/rules.
    Returns:
      - rec: dict (min_fee_ppm, max_fee_ppm, inbound_fee_ppm)
      - updated_state: dict (persistable per-peer state)
      - logs: list of NDJSON event log lines
    """
    # 1. Prepare state for this peer
    state = peer_mem.get(section_name, {}).copy()
    state["alias"] = alias

    # 2. Metrics and role state
    channel_data, state = process_channel_data(
        state, alias, forward_data_day, forward_data_int, policy
    )
    state["sink_risk_score"] = compute_sink_risk_score(state)
    sink_score = state["sink_risk_score"]

    # --- Debounced Role Override Logic ---
    override = state.get("role_override")
    neutral_count = state.get("neutral_sink_score_count", 0)
    sink_count = state.get("sink_score_high_count", 0)
    tap_count = state.get("sink_score_low_count", 0)

    if sink_score >= 0.8:
        sink_count += 1
        tap_count = 0
        neutral_count = 0
    elif sink_score <= 0.2:
        tap_count += 1
        sink_count = 0
        neutral_count = 0
    else:
        neutral_count += 1
        sink_count = 0
        tap_count = 0

    if sink_count >= 3:
        state["role_override"] = "sink"
    elif tap_count >= 3:
        state["role_override"] = "tap"
    elif neutral_count >= 3 and override:
        del state["role_override"]

    state["sink_score_high_count"] = sink_count
    state["sink_score_low_count"] = tap_count
    state["neutral_sink_score_count"] = neutral_count
    # --------------------------------------

    # 3. HTLC & fee logic
    state, outbound, skip_outbound_fee_adjust, skip_inbound_fee_adjust = get_htlc_sizes(
        state, policy.htlc.reserve_deduction, policy.htlc.min_capacity
    )

    state, final_report_logs, old_fees, new_fees, rule_ids = adjust_channel_fees(
        state,
        section_name,
        channel_data,
        lines,
        now,
        observe_only,
        dry_run,
        skip_outbound_fee_adjust,
        skip_inbound_fee_adjust,
        final_report_logs,
        rule_stats,
        policy,
    )
    new_fees, state = enforce_policy(
        section_name,
        new_fees,
        state,
        policy,
        log=lambda msg: final_report_logs.append(msg),
    )

    # 4. Logging/events for this peer
    logs = []
    if rule_ids:
        change_event = {
            "ts": now.isoformat(),
            "chan": alias,
            "rules": rule_ids,
            "old_fees": old_fees,
            "new_fees": new_fees,
            "vol_before": channel_data["vol"],
            "rev_before": channel_data["revenue"],
            "outbound_action": (
                "lower"
                if new_fees["max_fee_ppm"] < old_fees["max_fee_ppm"]
                else (
                    "raise"
                    if new_fees["max_fee_ppm"] > old_fees["max_fee_ppm"]
                    else "same"
                )
            ),
            "inbound_action": (
                "lower"
                if new_fees["inbound_fee_ppm"] < old_fees["inbound_fee_ppm"]
                else (
                    "raise"
                    if new_fees["inbound_fee_ppm"] > old_fees["inbound_fee_ppm"]
                    else "same"
                )
            ),
        }
        log_file_handle = open(policy.paths.fee_log_file, "a")
        json.dump(change_event, log_file_handle)
        log_file_handle.write("\n")
        log_file_handle.close()
    logs.extend(final_report_logs)  # Optionally add more logs as needed

    # 5. TOML config for this channel
    rec = {
        "min_fee_ppm": new_fees["min_fee_ppm"],
        "max_fee_ppm": new_fees["max_fee_ppm"],
        "inbound_fee_ppm": new_fees["inbound_fee_ppm"],
        "max_htlc_msat": state.get("max_htlc_msat", 500_000),
    }

    return rec, state, logs
