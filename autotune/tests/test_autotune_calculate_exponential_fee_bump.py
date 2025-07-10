import pytest
from autotune import calculate_exponential_fee_bump

class Fees:
    def __init__(self, increment_ppm=25, max_ppm=2500, bump_max=1000, min_ppm=0):
        self.increment_ppm = increment_ppm
        self.max_ppm = max_ppm
        self.bump_max = bump_max
        self.min_ppm = min_ppm

@pytest.mark.parametrize(
    "current_fee, streak, fees, exp_max, exp_min, exp_bump",
    [
        # Under increment_ppm: bump = 2^streak, clamp to increment_ppm
        (0, 0, Fees(), 1, 0, 1),       # 0+1 < 25 → 1
        (1, 1, Fees(), 3, 1, 2),       # 1+2 < 25 → 3
        (10, 3, Fees(), 18, 9, 8),     # 10+8 < 25 → 18
        (20, 4, Fees(), 25, 12, 16),   # 20+16=36 > 25 → 25 (clamped)
        (24, 2, Fees(), 25, 12, 4),    # 24+4=28 > 25 → 25

        # At/above increment_ppm: bump = increment_ppm * 2^streak, clamp to bump_max and max_ppm
        (25, 0, Fees(), 50, 25, 25),      # 25+25=50, bump_max not hit
        (100, 1, Fees(), 150, 75, 50),    # 100+50=150
        (1000, 2, Fees(), 1100, 550, 100),   # bump=100, new_max=1100, new_min=550
        (2000, 2, Fees(), 2100, 1050, 100), # 2000+100=2100 < 2500, so no clamp; min=1050

        # Bump is always positive, min = max // 2, max never exceeds max_ppm or bump_max
        (2450, 1, Fees(), 2500, 1250, 50),   # 2450+50=2500
        (2500, 3, Fees(), 2500, 1250, 200),  # At ceiling, bump is 200, clamp to 2500
    ]
)
def test_calculate_exponential_fee_bump(current_fee, streak, fees, exp_max, exp_min, exp_bump):
    new_max, new_min, bump = calculate_exponential_fee_bump(current_fee, streak, fees)
    assert new_max == exp_max
    assert new_min == exp_min
    assert bump == exp_bump

def test_bump_respects_bump_max_and_max_ppm():
    fees = Fees(increment_ppm=25, max_ppm=100, bump_max=60)
    # current_fee < increment_ppm so "tiny" branch:
    # bump = min(2**3, 25) = 8
    # new_max = min(100, 10+8) = 18
    new_max, new_min, bump = calculate_exponential_fee_bump(10, 3, fees)
    assert new_max == 18
    assert new_min == 9
    assert bump == 8

def test_min_is_integer_division():
    fees = Fees(increment_ppm=25, max_ppm=2500, bump_max=1000)
    # new_min should always be new_max // 2 (floor division)
    for max_fee in range(0, 500, 13):
        new_max, new_min, bump = calculate_exponential_fee_bump(max_fee, 1, fees)
        assert new_min == new_max // 2
