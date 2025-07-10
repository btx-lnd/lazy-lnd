# process_htlc.py
import logging
import time
import datetime

logger = logging.getLogger(__name__)


def group_htlc_events_by_peer(htlc_events, peer_memory, direction="outbound"):
    """
    Event map results: {}eturns: { node_id: [event, ...], ... }
    direction: 'outbound' (uses outgoing_channel_id) or 'inbound' (uses incoming_channel_id)
    """
    # Build mapping from scid (as string) to (peer_key, node_id)
    scid_to_peer = {}
    if not peer_memory:
        return {}
    for peer_key, data in peer_memory.items():
        node_id = data.get("node_id")
        for chan in data.get("channels", []):
            scid = str(chan.get("scid"))
            if scid:
                scid_to_peer[scid] = (peer_key, node_id)
    logger.debug(f"Scid to peer: {scid_to_peer}")
    key = "outgoing_channel_id" if direction == "outbound" else "incoming_channel_id"
    result = {}
    if htlc_events:
        for event in htlc_events:
            scid = event["fwd"].get(key)
            if not scid:
                continue
            # Some events use int, some str, so coerce
            scid = str(scid)
            if scid in scid_to_peer:
                peer_key, node_id = scid_to_peer[scid]
                # Group by node_id, could also use peer_key if preferred
                if node_id not in result:
                    result[node_id] = []
                result[node_id].append(event)
                logger.debug(f"Event matched: {event}")
            else:
                logger.debug(f"Event NOT matched: {event}")
    logger.info(f"Event map results: {result}")

    return result


def compute_peer_htlc_stats(htlc_events, now=None, windows=(3600, 86400)):
    now = now or int(time.time())
    import datetime
    results = {}
    for win in windows:
        window_start = int(now.timestamp()) - win if isinstance(now, datetime.datetime) else now - win
        in_window = [e for e in htlc_events if e["ts"] >= window_start]
        total = len(in_window)
        fails = 0
        successes = 0
        local_fails = 0
        remote_fails = 0

        for event in in_window:
            if "link_fail_event" in event.get("fwd", {}):
                fails += 1
                local_fails += 1
            elif "forward_fail_event" in event.get("result", {}):
                fails += 1
                remote_fails += 1
            else:
                successes += 1
        fail_rate = fails / max(total, 1)
        local_fail_rate = local_fails / max(total, 1)
        remote_fail_rate = remote_fails / max(total, 1)
        results[win] = dict(
            total=total,
            fails=fails,
            fail_rate=fail_rate,
            successes=successes,
            local_fails=local_fails,
            local_fail_rate=local_fail_rate,
            remote_fails=remote_fails,
            remote_fail_rate=remote_fail_rate,
        )
    return results

def classify_failure_source(event):
    """
    Returns 'local' if this is a solo match (your node failed), otherwise 'remote'.
    """
    fwd = event.get("fwd", {})
    res = event.get("result", {})
    # If the forward side had a link_fail_event, it's a local fail (solo match)
    if "link_fail_event" in fwd:
        return "local"
    # If the result is a FORWARD_FAIL, but not a solo match, it's remote
    if "forward_fail_event" in res:
        return "remote"
    # Defensive fallback: if 'link_fail_event' in result, also treat as local
    if "link_fail_event" in res:
        return "local"
    # Otherwise, treat as remote (could add more guards if needed)
    return "remote"

def summarise_peer_events(peer_htlc_events):
    total_missed_msat = 0
    total_missed_events = len(peer_htlc_events)
    for event in peer_htlc_events:
        inbound = int(
            event.get("fwd", {})
            .get("forward_event", {})
            .get("info", {})
            .get("incoming_amt_msat", 0)
            or 0
        )
        outbound = int(
            event.get("fwd", {})
            .get("forward_event", {})
            .get("info", {})
            .get("outgoing_amt_msat", 0)
            or 0
        )
        total_missed_msat += inbound - outbound

    return {"sats": total_missed_msat / 1000, "events": total_missed_events}
