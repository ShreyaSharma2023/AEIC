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


# The payload is recorded in its own field set, so that the base field set, and
# with it every trajectory file written before the payload was recorded, is
# unchanged.


def test_the_payload_is_not_in_the_base_field_set():
    from AEIC.trajectories.trajectory import BASE_FIELDS

    assert 'payload_mass' not in BASE_FIELDS.fields


def test_a_trajectory_without_a_decided_payload_has_no_payload_field(performance_model):
    traj = fly(performance_model, iterate_mass=True)

    assert not hasattr(traj, 'payload_mass')


def test_the_payload_survives_a_trajectory_store(performance_model, tmp_path):
    from AEIC.trajectories import TrajectoryStore

    traj = heavy_flight(performance_model)
    path = tmp_path / 'payload.nc'
    ts = TrajectoryStore.create(base_file=path)
    ts.add(traj)
    ts.close()

    ts = TrajectoryStore.open(base_file=path)
    assert ts[0].payload_mass == pytest.approx(traj.payload_mass)
    ts.close()


# A flight too heavy even with no payload. With `when_overweight`, it flies
# anyway: 'trim' takes the excess off the reserve, diversion and hold fuel;
# 'cap' also flies a flight that is still too heavy with nothing left to trim,
# at the maximum mass (the performance tables stop there).


def overweight_flight(
    performance, destination, when_overweight='error', consistent=True
):
    builder = tb.AdjustableLegacyBuilder(
        options=tb.Options(iterate_mass=True, max_mass_iters=30),
        legacy_options=tb.AdjustableLegacyOptions(
            consistent_starting_mass=consistent, when_overweight=when_overweight
        ),
    )
    flight = mission(destination=destination)
    flight.load_factor = 0.5
    return builder.fly(performance, flight)


CAIRO = 'CAI'  # 1.3 t too heavy at zero payload: less than the reserve trimmed
DOHA = 'DOH'  # 8.3 t too heavy: more than the reserve, diversion and hold


def test_by_default_a_flight_too_heavy_at_zero_payload_is_an_error(performance_model):
    with pytest.raises(ValueError, match='no payload'):
        overweight_flight(performance_model, CAIRO)


def test_trim_flies_it_at_the_maximum_mass_with_less_reserve(performance_model):
    traj = overweight_flight(performance_model, CAIRO, 'trim')

    assert traj.starting_mass == pytest.approx(performance_model.maximum_mass)
    assert traj.payload_mass == 0.0
    assert 0.0 < traj.reserve_shortfall < 3000.0
    assert traj.overweight_mass == 0.0
    # What is left of the reserve, diversion and hold is on board at landing.
    assert traj.aircraft_mass[-1] >= performance_model.empty_mass


def test_trim_refuses_a_flight_that_is_too_heavy_with_nothing_left_to_trim(
    performance_model,
):
    with pytest.raises(ValueError, match='reserve'):
        overweight_flight(performance_model, DOHA, 'trim')


def test_cap_flies_it_at_the_maximum_mass_and_records_the_overweight(performance_model):
    traj = overweight_flight(performance_model, DOHA, 'cap')

    assert traj.starting_mass == pytest.approx(performance_model.maximum_mass)
    assert traj.payload_mass == 0.0
    assert traj.reserve_shortfall > 0.0
    # Everything that could be trimmed was, and the rest is the overweight.
    assert traj.overweight_mass > 0.0
    assert traj.overweight_mass < 8341.0
    assert traj.aircraft_mass[-1] < performance_model.empty_mass + 1.0e3


def test_cap_treats_a_trimmable_flight_as_trim_does(performance_model):
    trimmed = overweight_flight(performance_model, CAIRO, 'trim')
    capped = overweight_flight(performance_model, CAIRO, 'cap')

    assert capped.overweight_mass == 0.0
    assert capped.reserve_shortfall == pytest.approx(trimmed.reserve_shortfall)
    assert capped.total_fuel_mass == pytest.approx(trimmed.total_fuel_mass)


def test_a_flight_that_fits_records_nothing_trimmed(performance_model):
    traj = heavy_flight(performance_model)

    assert traj.reserve_shortfall == 0.0
    assert traj.overweight_mass == 0.0


def test_when_overweight_needs_the_consistent_starting_mass():
    with pytest.raises(ValueError, match='consistent_starting_mass'):
        tb.AdjustableLegacyBuilder(
            legacy_options=tb.AdjustableLegacyOptions(when_overweight='trim')
        )


def test_an_unknown_when_overweight_is_an_error():
    with pytest.raises(ValueError, match='when_overweight'):
        tb.AdjustableLegacyBuilder(
            legacy_options=tb.AdjustableLegacyOptions(
                consistent_starting_mass=True, when_overweight='shrug'
            )
        )
