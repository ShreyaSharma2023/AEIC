import dataclasses

import numpy as np
import pytest

import AEIC.trajectories.builders as tb
from AEIC.missions import Mission
from AEIC.missions.mission import iso_to_timestamp
from AEIC.performance.models.legacy import ROCDFilter
from AEIC.performance.types import AircraftState, SimpleFlightRules
from AEIC.trajectories import GroundTrack
from AEIC.trajectories.builders.adjustable_legacy import (
    APEX_OVERCORRECTION,
    AdjustableLegacyContext,
    flown_descent_distance,
    no_cruise_apex_altitude,
)
from AEIC.units import FEET_TO_METERS, FL_TO_METERS


def _ground_track(mission):
    return GroundTrack.great_circle(
        mission.origin_position.location,
        mission.destination_position.location,
        allow_overstep=True,
    )


def _expected_starting_mass(
    performance_model,
    mission,
    cruise_altitude,
    reserve_fuel,
    divert_distance,
    hold_time,
):
    perf = performance_model.evaluate(
        AircraftState(altitude=cruise_altitude, aircraft_mass='max'),
        SimpleFlightRules.CRUISE,
    )
    lowest_cruise_altitude = (
        min(performance_model.performance_table(ROCDFilter.ZERO).fl) * FL_TO_METERS
    )
    perf_low = performance_model.evaluate(
        AircraftState(altitude=lowest_cruise_altitude, aircraft_mass='min'),
        SimpleFlightRules.CRUISE,
    )

    approx_time = _ground_track(mission).total_distance / perf.true_airspeed
    fuel_mass = approx_time * perf.fuel_flow
    payload_mass = performance_model.maximum_payload * mission.load_factor
    divert_mass = divert_distance / perf.true_airspeed * perf.fuel_flow
    hold_mass = hold_time * perf_low.fuel_flow

    return min(
        performance_model.empty_mass
        + payload_mass
        + fuel_mass
        + reserve_fuel
        + divert_mass
        + hold_mass,
        performance_model.maximum_mass,
    )


def test_adjustable_legacy_without_adjustments_matches_legacy(
    sample_missions, performance_model
):
    mission = sample_missions[0]
    options = tb.Options(iterate_mass=False)

    legacy_traj = tb.LegacyBuilder(options=options).fly(performance_model, mission)
    adjustable_traj = tb.AdjustableLegacyBuilder(options=options).fly(
        performance_model, mission
    )

    assert adjustable_traj.approx_eq(legacy_traj)
    assert adjustable_traj.starting_mass == pytest.approx(legacy_traj.starting_mass)
    assert adjustable_traj.total_fuel_mass == pytest.approx(legacy_traj.total_fuel_mass)
    assert adjustable_traj.n_climb == legacy_traj.n_climb
    assert adjustable_traj.n_cruise == legacy_traj.n_cruise
    assert adjustable_traj.n_descent == legacy_traj.n_descent


def test_fixed_geometry_adjustments_shape_trajectory(
    sample_missions, performance_model
):
    mission = sample_missions[0]
    descent_distance = 200_000.0

    traj = tb.AdjustableLegacyBuilder(options=tb.Options(iterate_mass=False)).fly(
        performance_model,
        mission,
        climb_start_altitude=1500.0,
        cruise_altitude=9000.0,
        descent_end_altitude=1200.0,
        descent_distance=descent_distance,
    )

    assert traj.altitude[0] == pytest.approx(1500.0)
    assert np.max(traj.altitude) == pytest.approx(9000.0)
    assert traj.altitude[-1] == pytest.approx(1200.0)

    descent_start_idx = np.flatnonzero(np.diff(traj.altitude) < 0)[0]
    expected_descent_start = _ground_track(mission).total_distance - descent_distance
    assert traj.ground_distance[descent_start_idx] == pytest.approx(
        expected_descent_start
    )


def test_fixed_fuel_policy_adjustments_set_starting_mass(
    sample_missions, performance_model
):
    mission = sample_missions[0]
    cruise_altitude = 9000.0
    reserve_fuel = 1234.0
    divert_distance = 150_000.0
    hold_time = 1200.0

    traj = tb.AdjustableLegacyBuilder(options=tb.Options(iterate_mass=False)).fly(
        performance_model,
        mission,
        cruise_altitude=cruise_altitude,
        reserve_fuel=reserve_fuel,
        divert_distance=divert_distance,
        hold_time=hold_time,
    )

    assert traj.starting_mass == pytest.approx(
        _expected_starting_mass(
            performance_model,
            mission,
            cruise_altitude,
            reserve_fuel,
            divert_distance,
            hold_time,
        )
    )


def test_callable_adjustments_receive_context_and_kwargs(
    sample_missions, performance_model
):
    mission = sample_missions[0]
    calls = []

    def adjustment(name, value):
        def _adjust(context, mission_arg, performance_arg, **kwargs):
            calls.append((name, context, mission_arg, performance_arg, kwargs))
            return value

        return _adjust

    reserve_fuel = 1234.0
    divert_distance = 150_000.0
    hold_time = 1200.0
    cruise_altitude = 9000.0

    traj = tb.AdjustableLegacyBuilder(options=tb.Options(iterate_mass=False)).fly(
        performance_model,
        mission,
        climb_start_altitude=adjustment('climb_start_altitude', 1500.0),
        cruise_altitude=adjustment('cruise_altitude', cruise_altitude),
        descent_end_altitude=adjustment('descent_end_altitude', 1200.0),
        descent_distance=adjustment('descent_distance', 200_000.0),
        reserve_fuel=adjustment('reserve_fuel', reserve_fuel),
        divert_distance=adjustment('divert_distance', divert_distance),
        hold_time=adjustment('hold_time', hold_time),
    )

    call_by_name = {name: call for name, *call in calls}
    assert set(call_by_name) == {
        'climb_start_altitude',
        'cruise_altitude',
        'descent_end_altitude',
        'descent_distance',
        'reserve_fuel',
        'divert_distance',
        'hold_time',
    }

    for context, mission_arg, performance_arg, _ in call_by_name.values():
        assert isinstance(context, AdjustableLegacyContext)
        assert mission_arg is mission
        assert performance_arg is performance_model

    assert call_by_name['climb_start_altitude'][3] == {}
    assert call_by_name['cruise_altitude'][3] == {}
    assert call_by_name['descent_end_altitude'][3] == {}
    assert call_by_name['descent_distance'][3] == {}
    assert set(call_by_name['reserve_fuel'][3]) == {'fuel_mass'}
    assert set(call_by_name['divert_distance'][3]) == {'approx_time'}
    assert set(call_by_name['hold_time'][3]) == {'approx_time'}

    assert traj.altitude[0] == pytest.approx(1500.0)
    assert traj.starting_mass == pytest.approx(
        _expected_starting_mass(
            performance_model,
            mission,
            cruise_altitude,
            reserve_fuel,
            divert_distance,
            hold_time,
        )
    )


@pytest.mark.parametrize(
    'kwargs,expected_altitude',
    [
        pytest.param(
            {'climb_start_altitude': 2000.0, 'cruise_altitude': 1000.0},
            2000.0,
            id='cruise_below_climb_clamps_to_climb',
        ),
        pytest.param(
            {'cruise_altitude': 20_000.0},
            None,
            id='cruise_above_ceiling_clamps_to_ceiling',
        ),
    ],
)
def test_adjustment_altitude_clamps(
    sample_missions, performance_model, kwargs, expected_altitude
):
    mission = sample_missions[0]
    if expected_altitude is None:
        expected_altitude = performance_model.maximum_altitude

    traj = tb.AdjustableLegacyBuilder(options=tb.Options(iterate_mass=False)).fly(
        performance_model, mission, **kwargs
    )

    assert np.max(traj.altitude) == pytest.approx(expected_altitude)


@pytest.mark.parametrize(
    'kwargs,match',
    [
        pytest.param(
            {'cruise_altitude': 9000.0, 'descent_end_altitude': 10_000.0},
            'Arrival airport \\+ 3000ft',
            id='descent_end_above_descent_start_raises',
        ),
        pytest.param(
            {'descent_distance': -1.0},
            'Descent distance must be non-negative',
            id='negative_descent_distance_raises',
        ),
    ],
)
def test_invalid_adjustments_raise_value_error(
    sample_missions, performance_model, kwargs, match
):
    with pytest.raises(ValueError, match=match):
        tb.AdjustableLegacyBuilder(options=tb.Options(iterate_mass=False)).fly(
            performance_model, sample_missions[0], **kwargs
        )


def test_adjustable_legacy_ground_distance_iter(sample_missions, performance_model):
    """Ground-distance iteration is implemented once, in `Builder`, against
    `descent_dist_approx`; it must also converge for the adjustable builder,
    whose context defines that attribute too."""
    mission = sample_missions[0]

    builder = tb.AdjustableLegacyBuilder(
        options=tb.Options(iterate_mass=False, iterate_ground_distance=True)
    )
    traj = builder.fly(performance_model, mission)

    assert float(traj.ground_distance[-1]) == pytest.approx(
        _ground_track(mission).total_distance, abs=1.0
    )


###########################################
######   Descent distance from model  ######
###########################################


def _mission(origin, destination):
    return Mission(
        origin=origin,
        destination=destination,
        departure=iso_to_timestamp('2024-09-01T12:00:00'),
        arrival=iso_to_timestamp('2024-09-01T18:00:00'),
        aircraft_type='738',
        load_factor=1.0,
    )


def _descent_end_altitude(mission):
    """The default descent end: 3000 ft above the arrival airport."""
    return mission.destination_position.altitude + 3000.0 * FEET_TO_METERS


def _miss(traj, mission):
    """Ground distance flown minus the route distance [m]."""
    return float(traj.ground_distance[-1]) - _ground_track(mission).total_distance


def test_flown_descent_distance_matches_the_descent_the_builder_flies(
    performance_model,
):
    """The estimate must equal the descent that is actually flown, or the two
    would drift apart the next time the level-change loop changes. Read the
    flown descent off a real flight: it runs from the last point at the peak
    altitude to the end."""
    mission = _mission('BOS', 'LAX')
    traj = tb.AdjustableLegacyBuilder(options=tb.Options(iterate_mass=False)).fly(
        performance_model, mission
    )

    altitude = np.asarray(traj.altitude)
    ground_distance = np.asarray(traj.ground_distance)
    top_of_descent = np.where(altitude >= altitude.max() - 1e-6)[0][-1]
    flown = ground_distance[-1] - ground_distance[top_of_descent]

    assert flown_descent_distance(
        performance_model,
        altitude.max(),
        _descent_end_altitude(mission),
        tb.LegacyOptions().altitude_step,
    ) == pytest.approx(flown, rel=1e-9)


def test_there_is_no_descent_distance_when_there_is_nothing_to_descend(
    performance_model,
):
    assert flown_descent_distance(performance_model, 3000.0, 3000.0, 304.8) == 0.0


@pytest.mark.parametrize('destination', ['LAX', 'DEN'])
@pytest.mark.parametrize('flight_level', [200, 340])
def test_estimating_the_descent_from_the_model_lands_flights_on_the_destination(
    performance_model, destination, flight_level
):
    """The static rule (18.228347 times the altitude drop) assumes a glide ratio
    the performance model does not have, so with it flights land 10 to 22 km
    short of the destination, more for a higher cruise altitude or a lower
    airport. Flying the descent on the model itself removes the error, since
    there is no wind here and the descent does not depend on the route."""
    mission = _mission('BOS', destination)
    cruise_altitude = flight_level * 100 * FEET_TO_METERS

    def fly(**legacy_options):
        return tb.AdjustableLegacyBuilder(
            options=tb.Options(iterate_mass=False),
            legacy_options=tb.AdjustableLegacyOptions(**legacy_options),
        ).fly(performance_model, mission, cruise_altitude=cruise_altitude)

    assert abs(_miss(fly(), mission)) > 1000.0
    assert _miss(fly(descent_distance_from_model=True), mission) == pytest.approx(
        0.0, abs=1.0
    )


def test_an_explicit_descent_distance_wins_over_the_model_estimate(
    sample_missions, performance_model
):
    mission = sample_missions[0]
    options = tb.Options(iterate_mass=False)

    explicit = tb.AdjustableLegacyBuilder(options=options).fly(
        performance_model, mission, descent_distance=120_000.0
    )
    with_option = tb.AdjustableLegacyBuilder(
        options=options,
        legacy_options=tb.AdjustableLegacyOptions(descent_distance_from_model=True),
    ).fly(performance_model, mission, descent_distance=120_000.0)

    assert with_option.approx_eq(explicit)


###########################################
######   No-cruise apex for short routes  ######
###########################################

# A route too short to reach the normal cruise altitude and still leave room
# for any cruise: BOS-PVD and JFK-PHL both overshoot the destination by
# 50-130 km if flown to the normal cruise altitude, since the climb and
# descent alone cover more than the route.


@pytest.mark.parametrize('destination', ['PVD', 'BDL'])
def test_a_route_too_short_for_cruise_lands_on_the_destination(
    performance_model, destination
):
    """Without an apex, these routes overshoot the destination because the
    climb to the normal cruise altitude plus the descent back down already
    cover more distance than the route. The fix finds the altitude at which
    the climb meets a descent shifted back to end at the destination, ends
    the climb there, and descends immediately - no cruise segment."""
    mission = _mission('BOS', destination)

    traj = tb.AdjustableLegacyBuilder(options=tb.Options(iterate_mass=False)).fly(
        performance_model, mission
    )

    assert _miss(traj, mission) == pytest.approx(0.0, abs=1.0)
    assert traj.n_cruise <= 1


def test_a_route_too_short_for_cruise_still_lands_with_mass_iteration(
    performance_model,
):
    """Mass iteration changes the starting mass by well under 1% on routes
    this short, which barely moves the apex, so it must not break landing on
    the destination."""
    mission = _mission('BOS', 'PVD')

    traj = tb.AdjustableLegacyBuilder(options=tb.Options(iterate_mass=True)).fly(
        performance_model, mission
    )

    assert _miss(traj, mission) == pytest.approx(0.0, abs=100.0)


def test_no_cruise_apex_altitude_raises_when_even_a_bare_climb_and_descent_do_not_fit(
    performance_model,
):
    """A route shorter than the distance needed to climb to the (normal)
    climb-start altitude and immediately descend from it has no altitude that
    works; this must fail loudly rather than silently fly some other route."""
    with pytest.raises(ValueError, match='too short'):
        no_cruise_apex_altitude(
            performance_model,
            route_distance=1.0,
            climb_start_altitude=3000.0 * FEET_TO_METERS,
            descent_end_altitude=0.0,
            altitude_step=tb.LegacyOptions().altitude_step,
        )


###########################################
######   Ground-distance iteration   ######
######   on a route with no cruise   ######
###########################################


class SlowerRealClimb:
    """A performance model whose flown climb is slower than the climb the
    builder estimates beforehand.

    The estimates (`flown_climb_distance` and `flown_descent_distance`) ask for
    the model at a named mass, 'max' or 'min', while the flight asks at its own
    mass in kilograms. PIANO models, and any flight in wind, differ between the
    two, so the real climb covers more ground than the estimate said."""

    def __init__(self, inner, rate_of_climb_factor):
        self._inner = inner
        self._factor = rate_of_climb_factor

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def evaluate(self, state, rules):
        perf = self._inner.evaluate(state, rules)
        if rules == SimpleFlightRules.CLIMB and not isinstance(
            state.aircraft_mass, str
        ):
            perf = dataclasses.replace(
                perf, rate_of_climb=perf.rate_of_climb * self._factor
            )
        return perf


@pytest.mark.parametrize('destination', ['PVD', 'BDL'])
def test_iterating_on_the_ground_distance_lands_a_route_with_no_cruise(
    performance_model, destination
):
    """The route is too short to cruise, so the descent estimate no longer says
    where cruise ends and feeding the flown descent back in changes nothing: the
    flight overshoots by the same distance every iteration and the loop used to
    give up. The apex altitude has to come down instead."""
    mission = _mission('BOS', destination)
    slower = SlowerRealClimb(performance_model, 0.7)

    traj = tb.AdjustableLegacyBuilder(
        options=tb.Options(iterate_mass=False, iterate_ground_distance=True)
    ).fly(slower, mission)

    assert traj.n_cruise <= 1
    assert abs(_miss(traj, mission)) < 1000.0


def test_an_explicit_cruise_altitude_is_left_alone_by_the_no_cruise_correction(
    performance_model,
):
    """An explicit cruise altitude was asked for, so ground-distance iteration
    must not quietly replace it with an apex; the flight fails to converge as it
    always did."""
    mission = _mission('BOS', 'PVD')
    slower = SlowerRealClimb(performance_model, 0.7)
    builder = tb.AdjustableLegacyBuilder(
        options=tb.Options(iterate_mass=False, iterate_ground_distance=True)
    )

    with pytest.raises(RuntimeError, match='Ground-distance iteration failed'):
        builder.fly(slower, mission, cruise_altitude=9000.0)


def test_a_route_with_cruise_is_not_changed_by_the_no_cruise_correction(
    performance_model,
):
    """A long route has cruise, so the descent estimate keeps its meaning and
    the same iteration as before must run."""
    mission = _mission('BOS', 'ORD')
    slower = SlowerRealClimb(performance_model, 0.7)

    traj = tb.AdjustableLegacyBuilder(
        options=tb.Options(iterate_mass=False, iterate_ground_distance=True)
    ).fly(slower, mission)

    assert traj.n_cruise > 1
    assert abs(_miss(traj, mission)) < 1000.0


def _apex_context(performance_model, route_distance):
    """Just the attributes `lower_apex` reads and writes."""
    from types import SimpleNamespace

    altitude_step = tb.LegacyOptions().altitude_step
    return SimpleNamespace(
        apex_is_adjustable=True,
        apex_route_distance=route_distance,
        ac_performance=performance_model,
        clm_start_altitude=3000.0 * FEET_TO_METERS,
        des_end_altitude=0.0,
        builder=SimpleNamespace(altitude_step=altitude_step),
        crz_start_altitude=None,
        des_start_altitude=None,
        descent_dist_approx=None,
    )


def test_the_apex_is_sized_for_a_route_well_short_of_the_overshoot(performance_model):
    """The flown distance is not a smooth function of the apex altitude (it has
    steps and dips, and is steeper in wind), so correcting by exactly the
    overshoot oscillates past the destination. Correcting by more makes the
    next flight land short instead, and the cruise that then fits absorbs the
    difference, which a change of apex altitude cannot do finely."""
    ctx = _apex_context(performance_model, route_distance=200_000.0)

    lowered = AdjustableLegacyContext.lower_apex(ctx, 4_000.0)

    assert lowered
    assert ctx.apex_route_distance == pytest.approx(
        200_000.0 - 4_000.0 * APEX_OVERCORRECTION
    )
    assert APEX_OVERCORRECTION > 1.0


def test_a_larger_overshoot_lowers_the_apex_more(performance_model):
    small = _apex_context(performance_model, 200_000.0)
    large = _apex_context(performance_model, 200_000.0)

    AdjustableLegacyContext.lower_apex(small, 1_000.0)
    AdjustableLegacyContext.lower_apex(large, 6_000.0)

    assert large.crz_start_altitude < small.crz_start_altitude
