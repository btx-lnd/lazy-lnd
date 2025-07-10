import pytest
import time
import autotune.process_htlc as ph
from autotune.process_htlc import compute_peer_htlc_stats, classify_failure_source


def mk_event(ts, local=False, remote=False):
    event = {"ts": ts, "fwd": {}, "result": {}}
    if local:
        event["fwd"]["link_fail_event"] = {"failure_detail": "INSUFFICIENT_BALANCE"}
    if remote:
        event["result"]["forward_fail_event"] = {"foo": "bar"}
    return event

def test_all_local_fails():
    now = int(time.time())
    events = [mk_event(now, local=True) for _ in range(10)]
    stats = compute_peer_htlc_stats(events, now=now, windows=(3600,))
    s = stats[3600]
    assert s["total"] == 10
    assert s["fails"] == 10
    assert s["local_fails"] == 10
    assert s["remote_fails"] == 0
    assert s["successes"] == 0
    assert s["fail_rate"] == 1.0

def test_all_remote_fails():
    now = int(time.time())
    events = [mk_event(now, remote=True) for _ in range(7)]
    stats = compute_peer_htlc_stats(events, now=now, windows=(3600,))
    s = stats[3600]
    assert s["total"] == 7
    assert s["fails"] == 7
    assert s["local_fails"] == 0
    assert s["remote_fails"] == 7
    assert s["successes"] == 0
    assert s["fail_rate"] == 1.0

def test_mixed_fails():
    now = int(time.time())
    events = [mk_event(now, local=True) for _ in range(3)]
    events += [mk_event(now, remote=True) for _ in range(2)]
    events += [mk_event(now) for _ in range(5)]  # "success"
    stats = compute_peer_htlc_stats(events, now=now, windows=(3600,))
    s = stats[3600]
    assert s["total"] == 10
    assert s["fails"] == 5
    assert s["local_fails"] == 3
    assert s["remote_fails"] == 2
    assert s["successes"] == 5
    assert s["fail_rate"] == 0.5
    assert s["local_fail_rate"] == 0.3
    assert s["remote_fail_rate"] == 0.2

def test_empty_events():
    now = int(time.time())
    stats = compute_peer_htlc_stats([], now=now, windows=(3600,))
    s = stats[3600]
    assert s["total"] == 0
    assert s["fails"] == 0
    assert s["local_fails"] == 0
    assert s["remote_fails"] == 0
    assert s["successes"] == 0
    assert s["fail_rate"] == 0
    assert s["local_fail_rate"] == 0
    assert s["remote_fail_rate"] == 0

def test_time_window_filtering():
    now = int(time.time())
    events = [mk_event(now - 7200, local=True)]  # 2h ago
    events += [mk_event(now, local=True)]
    stats = compute_peer_htlc_stats(events, now=now, windows=(3600, 10800))
    s1 = stats[3600]
    s3 = stats[10800]
    assert s1["total"] == 1     # only recent event in 1h
    assert s1["local_fails"] == 1
    assert s3["total"] == 2     # both events in 3h
    assert s3["local_fails"] == 2

def test_upstream_failure():
    # Example event for upstream/remote failure (matched pair, forward_fail_event in result)
    event = {
        'fwd': {
            'incoming_channel_id': '991664930455093248',
            'outgoing_channel_id': '990884277108473857',
            'incoming_htlc_id': '2173',
            'outgoing_htlc_id': '5081',
            'timestamp_ns': '1751831308034753600',
            'event_type': 'FORWARD',
            'forward_event': {
                'info': {'incoming_timelock': 904930, 'outgoing_timelock': 904850,
                         'incoming_amt_msat': '40886261', 'outgoing_amt_msat': '40886261'}
            },
            '_event_time': 1751831308,
        },
        'result': {
            'incoming_channel_id': '991664930455093248',
            'outgoing_channel_id': '990884277108473857',
            'incoming_htlc_id': '2173',
            'outgoing_htlc_id': '5081',
            'timestamp_ns': '1751831308914406997',
            'event_type': 'FORWARD',
            'forward_fail_event': {},
            '_event_time': 1751831308,
        },
        'ts': 1751831308
    }
    assert classify_failure_source(event) == "remote"

def test_local_htlc_max_exceeded():
    # Local HTLC max exceeded (solo, link_fail_event in fwd)
    event = {
        'fwd': {
            'incoming_channel_id': '991666029896794113',
            'outgoing_channel_id': '991664930455093248',
            'incoming_htlc_id': '311',
            'timestamp_ns': '1751830158280202310',
            'event_type': 'FORWARD',
            'link_fail_event': {
                'info': {'incoming_timelock': 904581, 'outgoing_timelock': 904501,
                         'incoming_amt_msat': '500069500', 'outgoing_amt_msat': '500005000'},
                'wire_failure': 'TEMPORARY_CHANNEL_FAILURE',
                'failure_detail': 'HTLC_EXCEEDS_MAX',
                'failure_string': 'htlc exceeds maximum policy amount'
            },
            '_event_time': 1751830158,
        },
        'result': {},
        'ts': 1751830158
    }
    assert classify_failure_source(event) == "local"

def test_local_insufficient_liquidity():
    # Local insufficient liquidity (solo, link_fail_event in fwd)
    event = {
        'fwd': {
            'incoming_channel_id': '989901313733296129',
            'outgoing_channel_id': '991868339959103489',
            'incoming_htlc_id': '8324',
            'timestamp_ns': '1751814576809843467',
            'event_type': 'FORWARD',
            'link_fail_event': {
                'info': {'incoming_timelock': 905295, 'outgoing_timelock': 905215,
                         'incoming_amt_msat': '341211398', 'outgoing_amt_msat': '341203892'},
                'wire_failure': 'TEMPORARY_CHANNEL_FAILURE',
                'failure_detail': 'INSUFFICIENT_BALANCE',
                'failure_string': 'insufficient bandwidth to route htlc'
            },
            '_event_time': 1751814576,
        },
        'result': {},
        'ts': 1751814576
    }
    assert classify_failure_source(event) == "local"

def test_local_fees_too_high():
    # Local fees too high (solo, link_fail_event in fwd)
    event = {
        'fwd': {
            'incoming_channel_id': '990448870554075137',
            'outgoing_channel_id': '990884277108473857',
            'incoming_htlc_id': '3146',
            'timestamp_ns': '1751825607238010101',
            'event_type': 'FORWARD',
            'link_fail_event': {
                'info': {'incoming_timelock': 904723, 'outgoing_timelock': 904643,
                         'incoming_amt_msat': '24969155', 'outgoing_amt_msat': '24969155'},
                'wire_failure': 'FEE_INSUFFICIENT',
                'failure_detail': 'NO_DETAIL',
                'failure_string': 'FeeInsufficient(...)'
            },
            '_event_time': 1751825607,
        },
        'result': {},
        'ts': 1751825607
    }
    assert classify_failure_source(event) == "local"

def test_malformed_event_defaults_remote():
    # If nothing matches, defaults to remote (safe fallback)
    event = {
        'fwd': {},
        'result': {},
        'ts': 123
    }
    assert classify_failure_source(event) == "remote"

def test_group_htlc_events_by_peer_basic_outbound():
    htlc_events = [
        {"fwd": {"outgoing_channel_id": "101"}},
        {"fwd": {"outgoing_channel_id": "102"}},
        {"fwd": {"outgoing_channel_id": "999"}},  # unmatched
    ]
    peer_memory = {
        "peer1": {
            "node_id": "nodeA",
            "channels": [{"scid": "101"}, {"scid": "102"}],
        },
        "peer2": {
            "node_id": "nodeB",
            "channels": [{"scid": "103"}],
        },
    }
    result = ph.group_htlc_events_by_peer(
        htlc_events, peer_memory, direction="outbound"
    )
    assert set(result.keys()) == {"nodeA"}
    assert len(result["nodeA"]) == 2


def test_group_htlc_events_by_peer_inbound_type_flexibility():
    # Some scid as int, some as str
    htlc_events = [
        {"fwd": {"incoming_channel_id": 201}},
        {"fwd": {"incoming_channel_id": "202"}},
        {"fwd": {"incoming_channel_id": None}},  # skipped
    ]
    peer_memory = {
        "peerZ": {
            "node_id": "nodeZ",
            "channels": [{"scid": 201}, {"scid": "202"}],
        }
    }
    result = ph.group_htlc_events_by_peer(htlc_events, peer_memory, direction="inbound")
    assert set(result.keys()) == {"nodeZ"}
    assert len(result["nodeZ"]) == 2


def test_group_htlc_events_by_peer_empty_inputs():
    # No events, no peer_memory
    assert ph.group_htlc_events_by_peer([], {}) == {}
    assert ph.group_htlc_events_by_peer(None, {}) == {}
    assert ph.group_htlc_events_by_peer([], None) == {}


def test_group_htlc_events_by_peer_partial_peer_memory():
    htlc_events = [{"fwd": {"outgoing_channel_id": "555"}}]
    peer_memory = {"peer1": {"node_id": "nodeA", "channels": []}}  # no scids
    # No match possible
    assert ph.group_htlc_events_by_peer(htlc_events, peer_memory) == {}


def test_summarise_peer_events_regular_case():
    peer_htlc_events = [
        {
            "fwd": {
                "forward_event": {
                    "info": {"incoming_amt_msat": 1200, "outgoing_amt_msat": 200}
                }
            }
        },
        {
            "fwd": {
                "forward_event": {
                    "info": {"incoming_amt_msat": 800, "outgoing_amt_msat": 600}
                }
            }
        },
    ]
    summary = ph.summarise_peer_events(peer_htlc_events)
    # (1200-200) + (800-600) = 1200, then /1000 = 1.2
    assert summary == {"sats": 1.2, "events": 2}


def test_summarise_peer_events_missing_fields_and_zeroes():
    # Should handle missing info, msat fields, None, etc.
    peer_htlc_events = [
        {
            "fwd": {
                "forward_event": {
                    "info": {"incoming_amt_msat": None, "outgoing_amt_msat": 0}
                }
            }
        },
        {"fwd": {"forward_event": {"info": {}}}},
        {},  # missing everything
    ]
    summary = ph.summarise_peer_events(peer_htlc_events)
    # all values 0, should not throw
    assert summary == {"sats": 0.0, "events": 3}


def test_summarise_peer_events_empty():
    assert ph.summarise_peer_events([]) == {"sats": 0.0, "events": 0}
