import pytest
from autotune import get_htlc_sizes


@pytest.mark.parametrize("section, alias, reserve_deduction, htlc_min_capacity, exp_max_htlc, exp_outbound, exp_skip_out, exp_skip_in", [
    # 1. Basic: sufficient outbound, no skipping
    ({"peer_outbound_percent": 0.6, "peer_total_capacity": 2_000_000, "peer_total_local": 1_500_000}, "peer1", 0.01, 0.1,
     1_480_000_000, 1_500_000_000, False, False),
    # 2. Outbound percent below threshold: skip_outbound_fee_adjust True
    ({"peer_outbound_percent": 0.05, "peer_total_capacity": 1_000_000, "peer_total_local": 1_000_000}, "peer2", 0.02, 0.1,
     980_000_000, 1_000_000_000, True, False),
    # 3. Inbound percent below threshold: skip_inbound_fee_adjust True
    ({"peer_outbound_percent": 0.95, "peer_total_capacity": 1_000_000, "peer_total_local": 950_000}, "peer3", 0.05, 0.1,
     900_000_000, 950_000_000, False, True),
    # 4. Both below threshold: both skip True
    ({"peer_outbound_percent": 0.05, "peer_total_capacity": 1_000_000, "peer_total_local": 50_000}, "peer4", 0.01, 0.95,
     40_000_000, 50_000_000, True, False),
    # 5. Reserve exceeds outbound: max_htlc clamps to zero
    ({"peer_outbound_percent": 0.5, "peer_total_capacity": 2_000_000, "peer_total_local": 1}, "peer5", 1.0, 0.1,
     0, 1_000, False, False),
    # 6. Missing fields: all default to zero
    ({}, "peer6", 0.2, 0.5,
     0, 0, True, False),  # skip_inbound_fee_adjust is False (since (1-0)<0.5 is False)
])
def test_get_htlc_sizes(section, alias, reserve_deduction, htlc_min_capacity,
                       exp_max_htlc, exp_outbound, exp_skip_out, exp_skip_in):
    result_section, outbound, skip_out, skip_in = get_htlc_sizes(
        section.copy(),  # Don't mutate the original between tests
        reserve_deduction,
        htlc_min_capacity,
        1
    )
    assert result_section["max_htlc_msat"] == exp_max_htlc
    assert outbound == exp_outbound
    assert skip_out == exp_skip_out
    assert skip_in == exp_skip_in
 