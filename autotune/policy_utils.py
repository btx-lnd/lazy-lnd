import logging

logger = logging.getLogger(__name__)


class Policy:
    def __init__(self, data):
        self._data = data

    def __getattr__(self, key):
        if key not in self._data:
            raise AttributeError(f"Policy key '{key}' not found")
        val = self._data[key]
        return Policy(val) if isinstance(val, dict) else val

    def __getitem__(self, key):  # still supports dict-style access
        return self._data[key]

    def items(self):
        return self._data.items()

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def __repr__(self):
        return repr(self.to_dict())

    def to_dict(self):
        def unwrap(value):
            if isinstance(value, Policy):
                return value.to_dict()
            elif isinstance(value, dict):
                return {k: unwrap(v) for k, v in value.items()}
            else:
                return value

        return unwrap(self._data)

    def get(self, key, default=None):
        val = self._data.get(key, default)
        return Policy(val) if isinstance(val, dict) else val


def enforce_policy(section_name, new_fees, state, policy, log=None):
    """
    Clamp new_fees AND state to the enforced min/max for this channel.
    Returns (clamped_new_fees, clamped_state).
    """
    chconf = policy.channels.get(section_name, {})
    logger.debug(f"Channel config: {chconf}", extra={'section': section_name})

    global_fees = policy.get("fees", {})
    global_inbound_fees = policy.get("inbound_fees", {})
    global_min_fee = global_fees.get("min_ppm", 0)
    global_max_fee = global_fees.get("max_ppm", 3000)
    
    global_max_inbound_fee = global_inbound_fees.get("max_ppm", 3000)
    global_min_inbound_fee = global_inbound_fees.get("min_ppm", -1000)
    global_inbound_increment = global_inbound_fees.get("increment_ppm", 25)
    
    chan_min_fee = chconf.get("min_range_ppm", global_min_fee)
    chan_max_fee = chconf.get("max_range_ppm", global_max_fee)
    
    chan_min_inbound = chconf.get("min_inbound_fee_ppm", global_max_inbound_fee)
    chan_max_inbound = chconf.get("max_inbound_fee_ppm", global_max_inbound_fee)

    before_fees = dict(new_fees)
    max_fee = new_fees["max_fee_ppm"]
    min_fee = new_fees["min_fee_ppm"]
    inbound_fee = new_fees.get("inbound_fee_ppm", 0)
    
    last_successful_fee = state.get("last_successful_fee", chan_max_fee)
    last_successful_inbound_fee = state.get("last_successful_inbound_fee", global_inbound_increment)

    logger.debug(
        f"Policy values: global_min={global_min_fee}, global_max={global_max_fee}, "
        f"chan_min={chan_min_fee}, chan_max={chan_max_fee}, last_successful_fee={last_successful_fee}",
        extra={'section': section_name}
    )

    # --- Policy limits ---
    policy_max = min(
        v for v in [global_max_fee, chan_max_fee, int(last_successful_fee * 1.5)] if v is not None
    )
    policy_min = int(policy_max / 2)
    policy_inbound_max = min(
        v for v in [global_max_inbound_fee, chan_max_inbound, int(last_successful_inbound_fee * 1.5)] if v is not None
    )
    policy_inbound_min = min(
        v for v in [global_min_inbound_fee, chan_min_inbound] if v is not None
    )


    logger.debug(
        f"Calculated policy_max={policy_max}, policy_min={policy_min}",
        extra={'section': section_name}
    )

    # --- Clamp max_fee_ppm --
    if max_fee > policy_max:
        logger.info(
            f"max_fee ({max_fee}) exceeds policy_max ({policy_max}). Clamping.",
            extra={'section': section_name}
        )
    max_fee = min(max_fee, int(policy_max))

    # --- Clamp min_fee_ppm ---
    if min_fee > policy_min:
        logger.info(
            f"min_fee ({min_fee}) above policy_min ({policy_min}). Clamping.",
            extra={'section': section_name}
        )
    min_fee = min(min_fee, int(policy_min))

    # --- Ensure max >= min ---
    if max_fee < min_fee:
        logger.warning(
            f"max_fee ({max_fee}) < min_fee ({min_fee}). Adjusting max_fee = min_fee.",
            extra={'section': section_name}
        )
    max_fee = max(max_fee, min_fee)

    # --- Clamp inbound_fee_ppm ---
    if inbound_fee > policy_inbound_max:
        logger.info(
            f"inbound_fee ({inbound_fee}) exceeds policy_inbound_max ({policy_inbound_max}). Clamping.",
            extra={'section': section_name}
        )
        inbound_fee = min(inbound_fee, int(policy_inbound_max))
  
    if inbound_fee < 0 and inbound_fee < policy_inbound_min:
        logger.info(
            f"inbound_fee ({inbound_fee}) below policy_inbound_min ({policy_inbound_min}). Clamping.",
            extra={'section': section_name}
        )
        inbound_fee = max(inbound_fee, int(policy_inbound_min))

    # Clamp negative inbound to at most -max_fee
    if inbound_fee < 0 and inbound_fee < -max_fee:
        logger.info(
            f"inbound_fee ({inbound_fee}) < -max_fee ({-max_fee}). Clamping.",
            extra={'section': section_name}
        )
        inbound_fee = max(inbound_fee, -max_fee)

    logger.debug(
        f"Pre-clamp: max_fee={before_fees['max_fee_ppm']}, min_fee={before_fees['min_fee_ppm']}, inbound_fee={before_fees.get('inbound_fee_ppm', 0)}",
        extra={'section': section_name}
    )
    logger.debug(
        f"Post-clamp: max_fee={max_fee}, min_fee={min_fee}, inbound_fee={inbound_fee}",
        extra={'section': section_name}
    )

    new_fees["min_fee_ppm"] = int(min_fee)
    new_fees["max_fee_ppm"] = int(max_fee)
    new_fees["inbound_fee_ppm"] = int(inbound_fee)
    state["fee"] = int(max_fee)
    state["inbound_fee"] = int(inbound_fee)

    if log and before_fees != new_fees:
        log(
            f"[{section_name}] Policy enforced: fees {before_fees} → {new_fees}; "
            f"{{max_fee: {max_fee}, min_fee: {min_fee}, inbound_fee: {inbound_fee}}}"
        )

    logger.info(
        f"Final enforced: max_fee={max_fee}, min_fee={min_fee}, inbound_fee={inbound_fee} "
        f"(last_successful_fee={last_successful_fee})",
        extra={'section': section_name}
    )

    return new_fees, state