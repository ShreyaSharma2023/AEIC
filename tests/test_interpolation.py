"""Tests for `Interpolator`, the (FL, mass) grid interpolator, and
`MachSweepInterpolator`, its counterpart for cruise tables that also sweep Mach.

The tables here are tiny and hand-written; only interpolation mechanics and
input validation are asserted, not physical plausibility.
"""

import itertools

import pandas as pd
import pytest

from AEIC.performance.interpolation import Interpolator, MachSweepInterpolator


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


###########################################
######     Mach-swept cruise tables    ######
###########################################

FLS = (300, 400)
MASSES = (50000.0, 60000.0)
MACHS = (0.70, 0.80, 0.90)


def _linear_fuel_flow(fl, mass, mach):
    """Linear in every axis with a different scale on each, so swapping two
    axes changes the answer."""
    return fl / 100 + mass / 50000 + 10 * mach


def _sweep_table(fuel_flow=_linear_fuel_flow, drop=()):
    rows = [
        (fl, mass, mach, 200.0 + 100 * mach, 0.0, fuel_flow(fl, mass, mach))
        for fl, mass, mach in itertools.product(FLS, MASSES, MACHS)
        if (fl, mass, mach) not in drop
    ]
    return pd.DataFrame(
        rows, columns=['fl', 'mass', 'mach', 'tas', 'rocd', 'fuel_flow']
    )


@pytest.mark.parametrize(
    ('fl', 'mass', 'mach'),
    [(300, 50000.0, 0.70), (350, 55000.0, 0.75), (390, 51000.0, 0.85)],
)
def test_a_function_linear_in_every_axis_is_recovered_exactly(fl, mass, mach):
    perf = MachSweepInterpolator(_sweep_table())(fl, mass, mach)
    assert perf.fuel_flow == pytest.approx(_linear_fuel_flow(fl, mass, mach))
    assert perf.true_airspeed == pytest.approx(200.0 + 100 * mach)
    assert perf.rate_of_climb == 0.0


def test_between_two_tabulated_machs_the_value_is_their_linear_blend():
    """The data is not linear in Mach here, so this pins that the blend is
    between the two neighbouring tabulated Machs and not a fitted curve."""
    table = _sweep_table(fuel_flow=lambda fl, mass, mach: 1.0 + (mach - 0.70) ** 2)
    perf = MachSweepInterpolator(table)(300, 50000.0, 0.75)
    assert perf.fuel_flow == pytest.approx((1.0 + 1.0 + 0.01) / 2)


def test_flight_level_and_mass_are_clipped_at_the_table_edge():
    interpolator = MachSweepInterpolator(_sweep_table())
    beyond = interpolator(999, 1e9, 0.80)
    edge = interpolator(400, 60000.0, 0.80)
    assert beyond.fuel_flow == pytest.approx(edge.fuel_flow)


def test_min_and_max_mass_are_exposed():
    interpolator = MachSweepInterpolator(_sweep_table())
    assert interpolator.min_mass == 50000.0
    assert interpolator.max_mass == 60000.0


def test_a_mach_outside_the_range_of_a_cell_that_is_needed_is_rejected():
    """Rows PIANO prints as '...' (a speed the aircraft cannot sustain there)
    are absent, so a needed cell can stop short of the requested Mach. Its
    nearest value is a different speed, so it must not be used silently."""
    table = _sweep_table(drop={(400, 60000.0, 0.90)})
    with pytest.raises(
        ValueError, match=r'no cruise data at Mach 0\.850.*0\.700-0\.800'
    ):
        MachSweepInterpolator(table)(390, 59000.0, 0.85)


def test_a_cell_with_no_weight_is_not_consulted():
    """At an exact node the neighbouring cells contribute nothing, so a
    neighbour that lacks the Mach must not make the query fail."""
    table = _sweep_table(drop={(400, 60000.0, 0.90)})
    perf = MachSweepInterpolator(table)(300, 50000.0, 0.90)
    assert perf.fuel_flow == pytest.approx(_linear_fuel_flow(300, 50000.0, 0.90))


def test_a_table_that_is_not_rectilinear_in_flight_level_and_mass_is_rejected():
    table = _sweep_table()
    table = table[~((table.fl == 400) & (table.mass == 60000.0))]
    with pytest.raises(ValueError, match='every.*pair'):
        MachSweepInterpolator(table)


def test_a_repeated_mach_within_a_cell_is_rejected():
    table = _sweep_table()
    table = pd.concat([table, table.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match='repeats Mach'):
        MachSweepInterpolator(table)
