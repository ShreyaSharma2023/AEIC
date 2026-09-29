"""Tests for converting MERRA2 winds to the fixed pressure levels AEIC reads.

The vertical tests use a wind profile that is linear in ln(pressure), which
log-pressure interpolation reproduces exactly, so the expected values are
written out independently of the code under test.
"""

import numpy as np
import pytest

from AEIC.utils.geos_levels import mid_pressures_hpa
from AEIC.weather_conversion.merra2 import (
    interp_to_pressure_levels,
    linear_in_time,
)

A, B = 3.0, 2.5  # wind = A + B * ln(p / hPa), m/s


def _column_winds(surface_hpa):
    surface = np.array([[surface_hpa]])
    mids = mid_pressures_hpa(surface)
    return A + B * np.log(mids), mids, surface


def test_winds_are_interpolated_linearly_in_log_pressure():
    values, mids, surface = _column_winds(1000.0)
    targets = np.array([850.0, 500.0, 250.0])

    out = interp_to_pressure_levels(values, mids, surface, targets)

    assert out.shape == (3, 1, 1)
    assert out[:, 0, 0] == pytest.approx(A + B * np.log(targets))


def test_the_output_follows_the_order_of_the_target_levels():
    values, mids, surface = _column_winds(1000.0)

    up = interp_to_pressure_levels(values, mids, surface, np.array([300.0, 800.0]))
    down = interp_to_pressure_levels(values, mids, surface, np.array([800.0, 300.0]))

    assert up[:, 0, 0] == pytest.approx(down[::-1, 0, 0])


def test_levels_below_the_ground_have_zero_wind_not_nan():
    """A 700 hPa surface is a mountain: the 850 and 1000 hPa levels are inside
    it. The Weather reader rejects NaN, so they must be zero."""
    values, mids, surface = _column_winds(700.0)

    out = interp_to_pressure_levels(
        values, mids, surface, np.array([1000.0, 850.0, 500.0])
    )

    assert out[0, 0, 0] == 0.0 and out[1, 0, 0] == 0.0
    assert out[2, 0, 0] == pytest.approx(A + B * np.log(500.0))


def test_between_the_lowest_level_and_the_ground_the_lowest_winds_are_held():
    """The lowest model level is centred about 7 hPa above the surface. A target
    between it and the ground is above the ground, so it gets that level's wind
    rather than being left undefined."""
    values, mids, surface = _column_winds(1000.0)
    between = 0.5 * (mids[0, 0, 0] + 1000.0)

    out = interp_to_pressure_levels(values, mids, surface, np.array([between]))

    assert out[0, 0, 0] == pytest.approx(values[0, 0, 0])


def test_every_output_value_is_finite():
    surface = np.array([[1013.25, 950.0], [700.0, 1040.0]])
    mids = mid_pressures_hpa(surface)
    values = np.broadcast_to(A + B * np.log(mids), mids.shape)
    targets = np.array([1050, 1025, 1000, 850, 500, 250, 100, 70], dtype=float)

    out = interp_to_pressure_levels(values, mids, surface, targets)

    assert np.isfinite(out).all()


def test_each_column_uses_its_own_surface_pressure():
    surface = np.array([[1000.0, 700.0]])
    mids = mid_pressures_hpa(surface)
    values = A + B * np.log(mids)

    out = interp_to_pressure_levels(values, mids, surface, np.array([850.0]))

    assert out[0, 0, 0] == pytest.approx(A + B * np.log(850.0))
    assert out[0, 0, 1] == 0.0  # underground in the 700 hPa column only


###########################################
######   Time                        ######
###########################################

HOUR = np.timedelta64(1, 'h')
T0 = np.datetime64('2025-01-01T01:30')


def test_values_are_interpolated_linearly_between_the_given_times():
    times = T0 + np.arange(4) * 3 * HOUR  # 01:30, 04:30, 07:30, 10:30
    values = (10.0 * np.arange(4))[:, None, None]  # 10 per 3 hours
    hours = np.datetime64('2025-01-01T02:00') + np.arange(3) * HOUR

    out = linear_in_time(values, times, hours)

    # 02:00 is 0.5 h after 01:30, i.e. 10 * 0.5 / 3 in.
    assert out[:, 0, 0] == pytest.approx([10 * 0.5 / 3, 10 * 1.5 / 3, 10 * 2.5 / 3])


def test_a_time_outside_the_given_range_is_an_error_not_an_extrapolation():
    times = T0 + np.arange(2) * 3 * HOUR
    values = np.zeros((2, 1, 1))

    with pytest.raises(ValueError, match='outside'):
        linear_in_time(values, times, np.array([np.datetime64('2025-01-01T00:00')]))
