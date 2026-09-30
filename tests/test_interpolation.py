"""Tests for `Interpolator`, the (FL, mass) grid interpolator, and
`MachSweepInterpolator`, its counterpart for cruise tables that also sweep Mach.

The tables here are tiny and hand-written; only interpolation mechanics and
input validation are asserted, not physical plausibility.
"""

import itertools

import pandas as pd
import pytest

from AEIC.performance.interpolation import (
    Interpolator,
    MachSweepInterpolator,
    grid_climb_trajectories,
    grid_cruise_levels,
    grid_descent_trajectories,
)


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


def test_a_mach_well_outside_the_range_of_a_cell_that_is_needed_is_rejected():
    """Rows PIANO prints as '...' (a speed the aircraft cannot sustain there)
    are absent, so a needed cell can stop short of the requested Mach. Its
    nearest value is then a different speed, and must not be used silently."""
    table = _sweep_table(drop={(400, 60000.0, 0.90)})
    with pytest.raises(
        ValueError, match=r'no cruise data at Mach 0\.850.*0\.700-0\.800'
    ):
        MachSweepInterpolator(table)(390, 59000.0, 0.85)


@pytest.mark.parametrize(
    ('dropped', 'mach', 'edge'), [(0.70, 0.795, 0.80), (0.90, 0.805, 0.80)]
)
def test_a_mach_just_outside_a_cells_range_uses_the_nearest_tabulated_mach(
    dropped, mach, edge
):
    """A heavy aircraft at a low flight level cannot fly slower than the lowest
    speed PIANO tabulates, and the flight's speed schedule can ask for a Mach
    a hair below it. The nearest tabulated speed is then the right answer, not
    an error."""
    table = _sweep_table(drop={(400, 60000.0, dropped)})
    interpolator = MachSweepInterpolator(table)

    perf = interpolator(400, 60000.0, mach)
    at_edge = interpolator(400, 60000.0, edge)

    assert perf.fuel_flow == pytest.approx(at_edge.fuel_flow)
    assert perf.true_airspeed == pytest.approx(at_edge.true_airspeed)


def test_using_the_nearest_mach_is_logged_once_per_cell(caplog):
    table = _sweep_table(drop={(400, 60000.0, 0.70)})
    interpolator = MachSweepInterpolator(table)

    with caplog.at_level('WARNING'):
        for _ in range(5):
            interpolator(400, 60000.0, 0.79)

    messages = [
        r.message for r in caplog.records if 'nearest tabulated Mach' in r.message
    ]
    assert len(messages) == 1
    assert 'FL 400' in messages[0] and '0.800' in messages[0]


def test_the_largest_gap_that_is_bridged_can_be_set():
    table = _sweep_table(drop={(400, 60000.0, 0.90)})

    wide = MachSweepInterpolator(table, max_mach_gap=0.06)(400, 60000.0, 0.85)
    assert wide.true_airspeed == pytest.approx(200.0 + 100 * 0.80)

    with pytest.raises(ValueError, match='no cruise data at Mach 0.850'):
        MachSweepInterpolator(table, max_mach_gap=0.0)(400, 60000.0, 0.85)


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


###########################################
######   Trajectory blocks -> grid   ######
###########################################

# PIANO writes each climb or descent as one trajectory per mass, sampled at its
# own flight levels and ending at its own ceiling, so the blocks together are
# not a grid. The interpolator needs a grid.

LIGHT = 50000
HEAVY = 60000


def _ragged_climb():
    """Light block reaches FL300, heavy block only FL250."""
    return _table(
        [
            (0, LIGHT, 200.0, 10.0, 1.0),
            (100, LIGHT, 220.0, 8.0, 0.8),
            (200, LIGHT, 240.0, 5.0, 0.6),
            (300, LIGHT, 260.0, 2.0, 0.4),
            (0, HEAVY, 200.0, 8.0, 1.2),
            (120, HEAVY, 224.0, 6.0, 1.0),
            (250, HEAVY, 250.0, 3.0, 0.7),
        ]
    )


def test_a_climb_is_gridded_onto_the_flight_levels_of_the_highest_block():
    gridded = grid_climb_trajectories(_ragged_climb())

    # FL0 is the start of the climb, not a level to grid onto.
    assert sorted(gridded.fl.unique()) == [100, 200, 300]
    assert len(gridded) == 6


def test_a_blocks_values_are_interpolated_between_its_own_samples():
    gridded = grid_climb_trajectories(_ragged_climb()).set_index(['fl', 'mass'])

    # Heavy block, FL100: 100/120 of the way from (0, 8.0) to (120, 6.0).
    assert gridded.loc[(100, HEAVY), 'rocd'] == pytest.approx(8.0 - 2.0 * 100 / 120)
    # ... and of the way from tas 200 to 224, fuel flow 1.2 to 1.0.
    assert gridded.loc[(100, HEAVY), 'tas'] == pytest.approx(200.0 + 24.0 * 100 / 120)
    assert gridded.loc[(100, HEAVY), 'fuel_flow'] == pytest.approx(
        1.2 - 0.2 * 100 / 120
    )
    # Heavy block, FL200: 80/130 of the way from (120, 6.0) to (250, 3.0).
    assert gridded.loc[(200, HEAVY), 'rocd'] == pytest.approx(6.0 - 3.0 * 80 / 130)
    # The light block already has a sample at each grid level.
    assert gridded.loc[(200, LIGHT), 'rocd'] == pytest.approx(5.0)


def test_above_a_blocks_ceiling_its_last_values_are_held():
    """The heavy block stops at FL250 but the grid runs to FL300. This keeps the
    grid rectangular without truncating the light block's higher levels."""
    gridded = grid_climb_trajectories(_ragged_climb()).set_index(['fl', 'mass'])

    assert gridded.loc[(300, HEAVY), 'rocd'] == pytest.approx(3.0)
    assert gridded.loc[(300, HEAVY), 'fuel_flow'] == pytest.approx(0.7)


def test_climb_levels_where_any_mass_cannot_climb_are_dropped():
    rows = _ragged_climb()
    rows.loc[rows.index[-1], 'rocd'] = 0.0  # heavy block's ceiling: no climb left

    gridded = grid_climb_trajectories(rows)

    # FL300 holds that zero, so it is not a level the aircraft can climb at.
    # FL200 is interpolated towards it but still positive.
    assert sorted(gridded.fl.unique()) == [100, 200]


def test_a_climb_that_is_already_a_grid_is_left_alone():
    dense = _table(
        [
            (100, LIGHT, 200.0, 10.0, 1.0),
            (100, HEAVY, 200.0, 8.0, 1.2),
            (200, LIGHT, 220.0, 6.0, 0.8),
            (200, HEAVY, 220.0, 4.0, 1.0),
        ]
    )

    pd.testing.assert_frame_equal(grid_climb_trajectories(dense), dense)


def test_a_descent_is_gridded_onto_the_first_blocks_flight_levels():
    descent = pd.DataFrame(
        [
            (300, LIGHT, 250.0, -10.0, 0.2),
            (150, LIGHT, 240.0, -8.0, 0.2),
            (0, LIGHT, 200.0, -6.0, 0.3),
            (250, HEAVY, 245.0, -11.0, 0.25),
            (0, HEAVY, 205.0, -7.0, 0.35),
        ],
        columns=['fl', 'mass', 'tas', 'rocd', 'fuel_flow'],
    )

    gridded = grid_descent_trajectories(descent).set_index(['fl', 'mass'])

    assert sorted(gridded.index.get_level_values('fl').unique()) == [0, 150, 300]
    # Heavy block at FL150: 150/250 of the way from (0, -7.0) to (250, -11.0).
    assert gridded.loc[(150, HEAVY), 'rocd'] == pytest.approx(-7.0 - 4.0 * 150 / 250)
    # Above its own top (FL250) the heavy block holds its last values.
    assert gridded.loc[(300, HEAVY), 'rocd'] == pytest.approx(-11.0)
    # No level is dropped for lack of climb: a descent has none to lose.
    assert len(gridded) == 6


def test_cruise_levels_that_a_mass_cannot_fly_are_dropped_for_every_mass():
    """A heavy aircraft has no cruise rows at the top flight levels, since it
    cannot fly there. Keeping those levels for the lighter masses would leave
    holes in the (FL, mass) grid, so only levels every mass has are kept."""
    cruise = pd.DataFrame(
        [
            (300, LIGHT, 0.78, 230.0, 0.0, 1.0),
            (310, LIGHT, 0.78, 232.0, 0.0, 0.9),
            (320, LIGHT, 0.78, 234.0, 0.0, 0.8),
            (300, HEAVY, 0.78, 230.0, 0.0, 1.2),
            (310, HEAVY, 0.78, 232.0, 0.0, 1.1),
        ],
        columns=['fl', 'mass', 'mach', 'tas', 'rocd', 'fuel_flow'],
    )

    gridded = grid_cruise_levels(cruise)

    assert sorted(gridded.fl.unique()) == [300, 310]
    assert len(gridded) == 4


def test_a_cruise_table_with_every_level_for_every_mass_is_left_alone():
    cruise = pd.DataFrame(
        [(300, LIGHT, 0.78, 1.0), (300, HEAVY, 0.78, 1.2)],
        columns=['fl', 'mass', 'mach', 'fuel_flow'],
    )

    pd.testing.assert_frame_equal(grid_cruise_levels(cruise), cruise)
