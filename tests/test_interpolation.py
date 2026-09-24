"""Tests for `Interpolator`, the (FL, mass) grid interpolator.

The tables here are tiny and hand-written; only interpolation mechanics and
input validation are asserted, not physical plausibility.
"""

import pandas as pd
import pytest

from AEIC.performance.interpolation import Interpolator


def _table(rows):
    return pd.DataFrame(rows, columns=['fl', 'mass', 'tas', 'rocd', 'fuel_flow'])


def test_bilinear_interpolation_at_the_centre_of_a_cell_averages_its_corners():
    interpolator = Interpolator(
        _table(
            [
                (100, 50000, 200.0, 10.0, 1.0),
                (100, 60000, 200.0, 8.0, 1.2),
                (200, 50000, 220.0, 6.0, 0.8),
                (200, 60000, 220.0, 4.0, 1.0),
            ]
        )
    )
    perf = interpolator(150, 55000)
    assert perf.true_airspeed == pytest.approx(210.0)
    assert perf.rate_of_climb == pytest.approx((10.0 + 8.0 + 6.0 + 4.0) / 4)
    assert perf.fuel_flow == pytest.approx((1.0 + 1.2 + 0.8 + 1.0) / 4)


def test_a_single_mass_interpolates_in_flight_level_only():
    """The BADA descent segment tabulates one mass; mass is then ignored."""
    interpolator = Interpolator(
        _table(
            [
                (100, 50000, 200.0, -10.0, 1.0),
                (200, 50000, 220.0, -20.0, 0.6),
            ]
        )
    )
    perf = interpolator(150, 12345)
    assert perf.true_airspeed == pytest.approx(210.0)
    assert perf.rate_of_climb == pytest.approx(-15.0)
    assert perf.fuel_flow == pytest.approx(0.8)


def test_a_grid_with_a_missing_cell_is_rejected():
    """A missing cell used to be filled with zeros, so a query near it
    returned zero fuel flow and airspeed instead of raising."""
    with pytest.raises(ValueError, match='every.*pair'):
        Interpolator(
            _table(
                [
                    (100, 50000, 200.0, 10.0, 1.0),
                    (100, 60000, 200.0, 8.0, 1.2),
                    (200, 50000, 220.0, 6.0, 0.8),
                ]
            )
        )


def test_a_duplicated_pair_is_rejected_even_when_the_row_count_looks_right():
    """Two FLs by two masses is four rows, and this table has four rows, but
    one pair appears twice and another is absent. A row count check alone
    cannot tell this from a complete grid."""
    with pytest.raises(ValueError, match='unique'):
        Interpolator(
            _table(
                [
                    (100, 50000, 200.0, 10.0, 1.0),
                    (100, 50000, 200.0, 10.0, 1.0),
                    (100, 60000, 200.0, 8.0, 1.2),
                    (200, 50000, 220.0, 6.0, 0.8),
                ]
            )
        )
