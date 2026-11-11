"""The blackbody table and the ramps built from it.

channel_factors used to clamp at 25100K while the table stopped at 10000K, so
every request above the table silently returned the last entry and the comment
promised a range that was never there. These pin the clamp to the table itself.
"""

import pytest

from fluxv2 import gamma, theme


def test_table_covers_its_documented_range():
    assert len(gamma._BLACKBODY) == 91
    assert 1000 + 100 * (len(gamma._BLACKBODY) - 1) == 10000


def test_6500k_is_the_identity_ramp():
    assert gamma.channel_factors(6500) == (1.0, 1.0, 1.0)


def test_clamp_high_matches_the_table_not_a_bigger_number():
    last = gamma._BLACKBODY[-1]
    assert gamma.channel_factors(10000) == last
    assert gamma.channel_factors(25100) == last
    # A clamp above the table would agree here by accident, so check the
    # interpolation just below the end is still a distinct value.
    assert gamma.channel_factors(9950) != last


def test_clamp_low_is_the_hottest_colour():
    first = gamma._BLACKBODY[0]
    assert gamma.channel_factors(500) == first
    assert gamma.channel_factors(-1) == first


def test_interpolation_lands_between_neighbours():
    below = gamma.channel_factors(3000)
    above = gamma.channel_factors(3100)
    mid = gamma.channel_factors(3050)
    for channel in range(3):
        assert min(below[channel], above[channel]) <= mid[channel]
        assert mid[channel] <= max(below[channel], above[channel])


def test_ui_range_stays_inside_the_table():
    # The slider must never be able to ask for a temperature the table cannot
    # answer, which is what kept the old mismatch dormant.
    assert theme.MIN_TEMP >= 1000
    assert theme.MAX_TEMP <= 1000 + 100 * (len(gamma._BLACKBODY) - 1)


@pytest.mark.parametrize("temp", [theme.MIN_TEMP, 2700, 3400, 5000, theme.MAX_TEMP])
def test_ramps_are_monotonic_and_bounded(temp):
    for ramp in gamma.temperature_ramps(temp, 1024):
        assert len(ramp) == 1024
        assert ramp[0] == 0
        assert all(0 <= value <= 65535 for value in ramp)
        assert all(b >= a for a, b in zip(ramp, ramp[1:]))


def test_ramp_bytes_layout():
    data = gamma.ramp_bytes(6500, 256)
    assert len(data) == 256 * 3 * 2
    red = data[: 256 * 2]
    green = data[256 * 2 : 256 * 4]
    blue = data[256 * 4 :]
    # 6500K is identity, so all three channels are the same ramp.
    assert red == green == blue


def test_tiny_ramp_is_refused():
    with pytest.raises(ValueError):
        gamma.fill_ramp(1.0, 1)
