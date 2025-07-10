import pytest
from collections import defaultdict

from autotune import parse_forwarding_data


def simple_event(**kwargs):
    # Returns a forwarding event dict with required + arbitrary fields
    d = {
        "timestamp": "1719999999",
        "amt_in": 1000,
        "amt_out": 900,
        "fee": 10,
        "peer_alias_in": "PeerA",
        "peer_alias_out": "PeerB",
    }
    d.update(kwargs)
    return d


def test_parse_forwarding_data_outbound_and_inbound_sum():
    # Outbound match: alias is peer_out, inbound match: alias is peer_in
    # 1 out, 1 in, 1 unrelated
    fwd = {
        "forwarding_events": [
            simple_event(
                peer_alias_in="PeerZ", peer_alias_out="MyNode", amt_out=200, fee=2
            ),  # OUT
            simple_event(
                peer_alias_in="MyNode", peer_alias_out="PeerY", amt_in=150
            ),  # IN
            simple_event(peer_alias_in="PeerQ", peer_alias_out="PeerR"),  # neither
        ]
    }
    js = __import__("json").dumps(fwd)
    stats = parse_forwarding_data(js, "MyNode")
    assert stats["total_out_sats"] == 200
    assert stats["total_in_sats"] == 150
    assert stats["total_fees"] == 2
    # Peer stats: outbound event counts for peer_in, inbound for peer_out
    assert stats["peer_stats"]["peerz"]["out"] == 200
    assert stats["peer_stats"]["peerz"]["fees"] == 2
    assert stats["peer_stats"]["peery"]["in"] == 150


def test_parse_forwarding_data_case_insensitivity_and_strip():
    fwd = {
        "forwarding_events": [
            simple_event(
                peer_alias_in="  pEeRa ", peer_alias_out="mYnOdE", amt_out=10, fee=1
            ),
            simple_event(peer_alias_in="mynode", peer_alias_out="peerb", amt_in=5),
        ]
    }
    js = __import__("json").dumps(fwd)
    stats = parse_forwarding_data(js, "MYNODE")
    # Both events should match despite case/whitespace
    assert stats["total_out_sats"] == 10
    assert stats["total_in_sats"] == 5


def test_parse_forwarding_data_invalid_json_returns_zeros():
    stats = parse_forwarding_data("{not json", "foo")
    assert stats["total_in_sats"] == 0
    assert stats["total_out_sats"] == 0
    assert stats["total_fees"] == 0
    assert stats["peer_stats"] == {}


def test_parse_forwarding_data_skip_invalid_peers():
    fwd = {
        "forwarding_events": [
            simple_event(peer_alias_in="", peer_alias_out="PeerZ"),  # skip
            simple_event(
                peer_alias_in="unable to lookup peer", peer_alias_out="PeerZ"
            ),  # skip
            simple_event(
                peer_alias_in="PeerA", peer_alias_out="unable to lookup peer"
            ),  # skip
            simple_event(
                peer_alias_in="PeerA", peer_alias_out="PeerZ", amt_out=100, fee=5
            ),  # OK
        ]
    }
    js = __import__("json").dumps(fwd)
    stats = parse_forwarding_data(js, "PeerZ")
    assert stats["total_out_sats"] == 100
    assert stats["total_fees"] == 5
    # Only one valid event processed


def test_parse_forwarding_data_empty_and_missing_events():
    stats = parse_forwarding_data("{}", "foo")
    assert stats["total_in_sats"] == 0
    assert stats["total_out_sats"] == 0
    assert stats["total_fees"] == 0
    assert stats["peer_stats"] == {}
    stats = parse_forwarding_data('{"forwarding_events":[]}', "foo")
    assert stats["total_in_sats"] == 0
    assert stats["total_out_sats"] == 0
    assert stats["total_fees"] == 0
    assert stats["peer_stats"] == {}


def test_parse_forwarding_data_handles_missing_amount_and_fee_fields():
    fwd = {
        "forwarding_events": [
            # Missing amt_out/fee, treated as 0
            {
                "timestamp": "1719999999",
                "peer_alias_in": "PeerA",
                "peer_alias_out": "MyNode",
            }
        ]
    }
    js = __import__("json").dumps(fwd)
    stats = parse_forwarding_data(js, "MyNode")
    assert stats["total_out_sats"] == 0
    assert stats["total_fees"] == 0
