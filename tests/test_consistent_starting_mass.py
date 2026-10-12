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


# A flight that cannot carry its full payload: the heaviest the aircraft is
# tabulated at is reached before the payload, the fuel, the reserve, the
# diversion and the hold fit.


def heavy_flight(performance, load_factor=1.0, destination='LAX', **kwargs):
    builder = tb.AdjustableLegacyBuilder(
        options=tb.Options(iterate_mass=True, max_mass_iters=30),
        legacy_options=tb.AdjustableLegacyOptions(consistent_starting_mass=True),
    )
    flight = mission(destination=destination)
    flight.load_factor = load_factor
    return builder.fly(performance, flight, **kwargs)


def diversion_and_hold_mass(performance):
    """Everything in the starting mass but the empty mass, the payload, the trip
    fuel and the reserve, found from the uniterated estimate of an empty
    aircraft."""
    builder = tb.AdjustableLegacyBuilder(
        options=tb.Options(iterate_mass=False),
        legacy_options=tb.AdjustableLegacyOptions(consistent_starting_mass=True),
    )
    flight = mission(destination='LAX')
    flight.load_factor = 0.0
    traj = builder.fly(performance, flight)
    return traj.starting_mass - 1.05 * traj.total_fuel_mass - performance.empty_mass


def test_a_flight_too_heavy_for_its_payload_flies_at_the_maximum_mass(
    performance_model,
):
    traj = heavy_flight(performance_model)

    assert traj.starting_mass == pytest.approx(performance_model.maximum_mass)
    assert 0.0 < traj.payload_mass < performance_model.maximum_payload


def test_the_payload_carried_is_what_the_maximum_mass_leaves_after_the_fuel(
    performance_model,
):
    traj = heavy_flight(performance_model)

    expected = (
        traj.starting_mass
        - performance_model.empty_mass
        - 1.05 * traj.total_fuel_mass
        - diversion_and_hold_mass(performance_model)
    )
    assert traj.payload_mass == pytest.approx(expected, abs=1.0)
    assert traj.total_fuel_mass == pytest.approx(burned(traj), rel=1e-2)


def test_a_flight_lands_no_lighter_than_its_empty_mass_and_payload(performance_model):
    traj = heavy_flight(performance_model)

    assert traj.aircraft_mass[-1] >= performance_model.empty_mass + traj.payload_mass


def test_a_flight_that_fits_carries_the_payload_its_load_factor_asks_for(
    performance_model,
):
    traj = heavy_flight(performance_model, load_factor=0.8, destination='ORD')

    assert traj.payload_mass == pytest.approx(0.8 * performance_model.maximum_payload)
    assert traj.starting_mass < performance_model.maximum_mass


def test_a_flight_that_cannot_fly_even_with_no_payload_is_an_error(performance_model):
    with pytest.raises(ValueError, match='payload'):
        heavy_flight(performance_model, load_factor=0.0, destination='SIN')


def test_the_first_estimate_is_never_accepted_as_it_is(performance_model):
    """The estimate that starts the iteration is capped at the maximum mass but
    carries the full payload. Even if its burn happens to be within tolerance
    of its trip fuel, that is not a consistent flight, so it is corrected
    anyway (a loose tolerance makes every first flight look converged)."""
    builder = tb.AdjustableLegacyBuilder(
        options=tb.Options(iterate_mass=True, mass_iter_reltol=10.0),
        legacy_options=tb.AdjustableLegacyOptions(consistent_starting_mass=True),
    )
    flight = mission(destination='LAX')
    flight.load_factor = 1.0

    traj = builder.fly(performance_model, flight)

    assert traj.starting_mass == pytest.approx(performance_model.maximum_mass)
    assert traj.payload_mass < performance_model.maximum_payload
