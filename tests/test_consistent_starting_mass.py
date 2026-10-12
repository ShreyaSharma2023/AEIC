"""Mass iteration that keeps the starting mass equal to what it is made of."""

import pytest

import AEIC.trajectories.builders as tb
from AEIC.missions import Mission
from AEIC.missions.mission import iso_to_timestamp


def mission(origin='BOS', destination='ORD'):
    return Mission(
        origin=origin,
        destination=destination,
        departure=iso_to_timestamp('2024-09-01T12:00:00'),
        arrival=iso_to_timestamp('2024-09-01T20:00:00'),
        aircraft_type='738',
        load_factor=0.8,
    )


def fly(performance, iterate_mass, consistent=False, **kwargs):
    builder = tb.AdjustableLegacyBuilder(
        options=tb.Options(iterate_mass=iterate_mass, max_mass_iters=30),
        legacy_options=tb.AdjustableLegacyOptions(consistent_starting_mass=consistent),
    )
    return builder.fly(performance, mission(), **kwargs)


def burned(traj):
    return float(traj.starting_mass - traj.aircraft_mass[-1])


def test_the_trip_fuel_is_the_fuel_burned_and_the_reserve_follows_it(
    performance_model,
):
    """Everything in the starting mass but the trip fuel and the reserve (5% of
    it) is fixed by the flight, so a starting mass that is consistent with its
    own burn has trip fuel = burn, and the reserve is 5% of the burn."""
    first = fly(performance_model, iterate_mass=False)
    fixed = first.starting_mass - 1.05 * first.total_fuel_mass

    traj = fly(performance_model, iterate_mass=True, consistent=True)

    assert traj.total_fuel_mass == pytest.approx(burned(traj), rel=1e-2)
    assert traj.starting_mass == pytest.approx(
        fixed + 1.05 * traj.total_fuel_mass, abs=1.0
    )


def test_without_the_option_the_reserve_stays_what_the_first_estimate_made_it(
    performance_model,
):
    first = fly(performance_model, iterate_mass=False)
    unreserved = first.starting_mass - first.total_fuel_mass

    traj = fly(performance_model, iterate_mass=True)

    assert traj.starting_mass - traj.total_fuel_mass == pytest.approx(
        unreserved, abs=1.0
    )


def test_the_reserve_adjustment_is_given_the_trip_fuel_of_each_iteration(
    performance_model,
):
    def reserve(context, mission, performance, fuel_mass, **kwargs):
        return 1000.0 + 0.1 * fuel_mass

    first = fly(performance_model, iterate_mass=False, reserve_fuel=reserve)
    fixed = (
        first.starting_mass
        - first.total_fuel_mass
        - reserve(None, None, None, first.total_fuel_mass)
    )

    traj = fly(
        performance_model, iterate_mass=True, consistent=True, reserve_fuel=reserve
    )

    assert traj.starting_mass == pytest.approx(
        fixed + traj.total_fuel_mass + reserve(None, None, None, traj.total_fuel_mass),
        abs=1.0,
    )
