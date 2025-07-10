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
    Args:
        section_name (str): Channel section name.
        new_fees (dict): Dict with min_fee_ppm, max_fee_ppm, inbound_fee_ppm, etc.
        state (dict): Mutable state dict for this peer/channel.
        policy (dict): The loaded policy config, including [channels.<section>] blocks.
        log (callable): Optional log callback. Signature log(msg: str).
    Returns:
        tuple: (clamped_new_fees, clamped_state)
    """
    # Find the range (if defined)
    try:
        chconf = policy.channels[section_name]
        min_fee = chconf.get("min_range_ppm", None)
        max_fee = chconf.get("max_range_ppm", None)
        inbound_fee = chconf.get("inbound_fee_ppm", None)
    except Exception:
        logger.exception(
            "Policy config error. Values missing for section '%s'", section_name
        )
        max_fee = 3000
        min_fee = 3000
        inbound_fee = 0

    before_fees = dict(new_fees)
    before_state = {k: state.get(k) for k in ("fee", "min_fee", "max_fee")}

    # Clamp fee dict
    if min_fee is not None:
        new_min_fee = max(min_fee, new_fees["min_fee_ppm"])
        new_min_fee = max(0, new_min_fee)
        new_fees["min_fee_ppm"] = new_min_fee
    if max_fee is not None:
        new_max_fee = min(max_fee, new_fees["max_fee_ppm"])
        new_max_fee = max(new_fees["min_fee_ppm"], new_max_fee)
        new_fees["max_fee_ppm"] = int(new_max_fee)
        state["fee"] = new_max_fee
        # Clamp 'fee' as well (if you have it in state)
        if new_fees["min_fee_ppm"] < new_fees["max_fee_ppm"] / 2:
            new_fees["min_fee_ppm"] = int(new_fees["max_fee_ppm"] / 2)
    if inbound_fee is not None:
        new_inbound_fee = (
            inbound_fee
            if inbound_fee > new_fees["inbound_fee_ppm"]
            and new_fees["inbound_fee_ppm"] >= 0
            else new_fees["inbound_fee_ppm"]
        )
        new_fees["inbound_fee_ppm"] = new_inbound_fee
        state["inbound_fee"] = new_inbound_fee

    if new_fees['min_fee_ppm'] > new_fees['max_fee_ppm']:
        temp = new_fees['min_fee_ppm']
        new_fees['min_fee_ppm'] = new_fees['max_fee_ppm']
        new_fees['max_fee_ppm'] = temp

    if log and (
        before_fees != new_fees
        or before_state != {k: state.get(k) for k in before_state}
    ):
        log(
            f"Policy enforced for {section_name}: fees {before_fees} → {new_fees}; "
            f"state {before_state} → "
            f"{{max_fee: {new_fees['max_fee_ppm']}, min_fee: {new_fees['min_fee_ppm']}, inbound_fee: {new_fees['inbound_fee_ppm']}}}"
        )

    return new_fees, state
