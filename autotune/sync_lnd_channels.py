import subprocess
import json

import logging

logger = logging.getLogger(__name__)


def get_all_channels(lnd_container_name="lnd"):
    """
    Calls lncli listchannels via Docker and returns a list of channels as dicts.
    """
    cmd = [
        "docker",
        "exec",
        lnd_container_name,
        "lncli",
        "listchannels",
        "--active_only",
    ]
    try:
        result = subprocess.check_output(cmd, text=True)
        data = json.loads(result)
        return data.get("channels", [])
    except Exception as e:
        logger.error(f"Error getting channel list: {e}")
        return []


def get_peer_channels(all_channels, node_id):
    """Return a list of channel dicts matching the remote_pubkey (node_id)."""
    return [
        select_channel_fields(chan)
        for chan in all_channels
        if chan["remote_pubkey"] == node_id
    ]


def select_channel_fields(chan):
    """Return only the fields we want to keep for a channel."""
    return {
        "channel_point": chan.get("channel_point"),
        "scid": chan.get("scid"),
        "chan_id": chan.get("chan_id"),
        "capacity": chan.get("capacity"),
        "local_balance": chan.get("local_balance"),
        "remote_balance": chan.get("remote_balance"),
        # add more fields here as needed
    }


def merge_channels(existing, current):
    """
    Merge/clean the 'channels' list in state.
    - Keeps old channels as tombstones with zero balances if closed/inactive.
    - Updates info for channels present in both.
    - Adds new channels.
    """
    by_chan_point = {c["channel_point"]: c for c in current}
    merged = []
    seen = set()

    # Update or tombstone existing channels
    for old in existing:
        cp = old.get("channel_point")
        if cp in by_chan_point:
            # Update old with latest info
            updated = {**old, **by_chan_point[cp]}
            updated["active"] = True
            merged.append(updated)
            seen.add(cp)
        else:
            # Channel gone--tombstone
            tombstoned = old.copy()
            tombstoned["local_balance"] = "0"
            tombstoned["remote_balance"] = "0"
            tombstoned["capacity"] = "0"
            tombstoned["active"] = False
            merged.append(tombstoned)

    # Add any brand new channels
    for cp, c in by_chan_point.items():
        if cp not in seen:
            c = c.copy()
            c["active"] = True
            merged.append(c)

    return merged


def aggregate_peer_stats(channels):
    """
    Aggregate total balances and capacities for a peer.
    Returns dict with total/local/remote balances, capacity, and outbound percent.
    """
    total_capacity = sum(int(c["capacity"]) for c in channels)
    total_local = sum(int(c["local_balance"]) for c in channels)
    total_remote = sum(int(c["remote_balance"]) for c in channels)
    pct_outbound = total_local / total_capacity if total_capacity else 0

    return {
        "peer_total_capacity": total_capacity,
        "peer_total_local": total_local,
        "peer_total_remote": total_remote,
        "peer_outbound_percent": pct_outbound,
    }


def update_all_states_with_channel_info(all_states, all_channels, policy):
    for section, state in all_states.items():
        # Prefer state node_id, then policy
        node_id = state.get("node_id") or policy["channels"].get(section, {}).get(
            "node_id"
        )
        if not node_id:
            continue  # can't update without id

        # Get all current channels for this node_id
        current_chans = get_peer_channels(all_channels, node_id)
        # Merge with any in state (keep existing keys, but update latest info)
        state["channels"] = merge_channels(state.get("channels", []), current_chans)
        # Aggregate up-to-date balances/stats for this peer
        state.update(aggregate_peer_stats(state["channels"]))

    return all_states
