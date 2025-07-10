import pytest
from autotune import get_htlc_sizes

RAW_PEERS_EXAMPLE = """
│ PeerName │ 1.00000000 │ x │ 2.00000000 │ y │
│ Other    │ 9.00000000 │ x │ 8.00000000 │ y │
"""

def test_get_htlc_sizes_basic_success():
    section = {}
    alias = "PeerName"
    reserve_deduction = 0.01
    htlc_min_capacity = 0.2
    updated, outbound, skip_out, skip_in = get_htlc_sizes(
        section, alias, RAW_PEERS_EXAMPLE, reserve_deduction, htlc_min_capacity
    )
    assert outbound == 2.0 * 1e8
    inbound = 1.0 * 1e8
    capacity = outbound + inbound
    reserve = int(capacity * 1000 * reserve_deduction)
    expected_max_htlc = max(0, int(outbound * 1000) - reserve)
    assert updated["max_htlc_msat"] == expected_max_htlc
    assert pytest.approx(updated["percentage_outbound"], 0.001) == outbound / (outbound + inbound)
    # Outbound percent = 2/(1+2) = 0.6666..., should NOT skip (since 0.666 > 0.2)
    assert skip_out is False
    # Inbound percent = 1/(1+2) = 0.333..., so skip_in is True if 1-0.666 < 0.2
    assert skip_in is False

def test_get_htlc_sizes_handles_no_match():
    section = {}
    alias = "Unknown"
    updated, outbound, skip_out, skip_in = get_htlc_sizes(
        section, alias, RAW_PEERS_EXAMPLE, 0.01, 0.2
    )
    assert outbound == 0
    assert updated["max_htlc_msat"] == 0
    assert updated["percentage_outbound"] == 0.5

def test_get_htlc_sizes_handles_parse_error():
    section = {}
    alias = "PeerName"
    raw_peers = "This is not parsable │ │ │"
    updated, outbound, skip_out, skip_in = get_htlc_sizes(
        section, alias, raw_peers, 0.01, 0.2
    )
    assert outbound == 0
    assert updated["max_htlc_msat"] == 0
    assert updated["percentage_outbound"] == 0.5