"""The climb, descent and apex estimates, made aware of the wind along the route."""

import pytest

import AEIC.trajectories.builders as tb
from AEIC.missions import Mission
from AEIC.missions.mission import iso_to_timestamp
from AEIC.performance.types import AircraftState, SimpleFlightRules
from AEIC.trajectories import GroundTrack
from AEIC.trajectories.builders.adjustable_legacy import (
    RouteWind,
    flown_climb_distance,
    flown_descent_distance,
    level_change_altitudes,
    no_cruise_apex_altitude,
)
from AEIC.units import FEET_TO_METERS

STEP = tb.LegacyOptions().altitude_step
CLIMB_START = 3000.0 * FEET_TO_METERS
CRUISE = 11000.0
LANDING = 3000.0 * FEET_TO_METERS


def time_to_change_level(performance, start, end, climbing):
    """Seconds the level change takes, from the performance model's rates."""
    rules = SimpleFlightRules.CLIMB if climbing else SimpleFlightRules.DESCEND
    mass = 'max' if climbing else 'min'
    altitudes = level_change_altitudes(start, end, STEP, climbing)
    total = 0.0
    for altitude, next_altitude in zip(altitudes[:-1], altitudes[1:]):
        perf = performance.evaluate(
            AircraftState(altitude=altitude, aircraft_mass=mass), rules
        )
        total += (next_altitude - altitude) / perf.rate_of_climb
    return total


def uniform_wind(tailwind, route_distance=1.0e6):
    return RouteWind(
        ground_speed=lambda distance, altitude, airspeed: airspeed + tailwind,
        route_distance=route_distance,
    )


def test_no_wind_leaves_the_flown_distances_unchanged(performance_model):
    calm = uniform_wind(0.0)

    assert flown_climb_distance(
        performance_model, CLIMB_START, CRUISE, STEP, wind=calm
    ) == pytest.approx(
        flown_climb_distance(performance_model, CLIMB_START, CRUISE, STEP), rel=1e-12
    )
    assert flown_descent_distance(
        performance_model, CRUISE, LANDING, STEP, wind=calm
    ) == pytest.approx(
        flown_descent_distance(performance_model, CRUISE, LANDING, STEP), rel=1e-12
    )


def test_a_tailwind_adds_its_speed_times_the_time_spent_changing_level(
    performance_model,
):
    tailwind = 25.0

    climb = flown_climb_distance(
        performance_model, CLIMB_START, CRUISE, STEP, wind=uniform_wind(tailwind)
    )
    descent = flown_descent_distance(
        performance_model, CRUISE, LANDING, STEP, wind=uniform_wind(tailwind)
    )

    assert climb == pytest.approx(
        flown_climb_distance(performance_model, CLIMB_START, CRUISE, STEP)
        + tailwind * time_to_change_level(performance_model, CLIMB_START, CRUISE, True),
        rel=1e-9,
    )
    assert descent == pytest.approx(
        flown_descent_distance(performance_model, CRUISE, LANDING, STEP)
        + tailwind * time_to_change_level(performance_model, CRUISE, LANDING, False),
        rel=1e-9,
    )


def descent_starting_at(performance, wind, start_distance):
    """Ground distance of the descent that begins `start_distance` along the
    route, in the wind: the oracle for the fixed point."""
    distance = 0.0
    altitudes = level_change_altitudes(CRUISE, LANDING, STEP, climbing=False)
    for altitude, next_altitude in zip(altitudes[:-1], altitudes[1:]):
        perf = performance.evaluate(
            AircraftState(altitude=altitude, aircraft_mass='min'),
            SimpleFlightRules.DESCEND,
        )
        airspeed = (perf.true_airspeed**2 - perf.rate_of_climb**2) ** 0.5
        position = min(max(start_distance + distance, 0.0), wind.route_distance)
        speed = wind.ground_speed(position, altitude, airspeed)
        distance += speed * (next_altitude - altitude) / perf.rate_of_climb
    return distance


def test_the_descent_is_flown_in_the_wind_where_it_actually_starts(performance_model):
    """The descent ends at the end of the route, so where it starts depends on
    how far it goes, and a wind that only blows near the destination changes
    both. Estimating from the start the calm descent would have is not enough."""
    route = 400.0e3
    calm_descent = flown_descent_distance(performance_model, CRUISE, LANDING, STEP)
    gust_from = route - 0.5 * calm_descent
    wind = RouteWind(
        ground_speed=lambda distance, altitude, airspeed: (
            airspeed
            + 30.0 * min(max((distance - gust_from) / (0.5 * calm_descent), 0.0), 1.0)
        ),
        route_distance=route,
    )

    descent = flown_descent_distance(
        performance_model, CRUISE, LANDING, STEP, wind=wind
    )

    assert descent == pytest.approx(
        descent_starting_at(performance_model, wind, route - descent), abs=1.0
    )
    assert descent > calm_descent + 100.0
    naive = descent_starting_at(performance_model, wind, route - calm_descent)
    assert abs(descent - naive) > 1.0


def test_the_apex_of_a_route_in_a_tailwind_is_lower_and_fits_the_route(
    performance_model,
):
    route = 150.0e3
    tailwind = uniform_wind(25.0, route_distance=route)

    calm_apex = no_cruise_apex_altitude(
        performance_model, route, CLIMB_START, LANDING, STEP
    )
    apex = no_cruise_apex_altitude(
        performance_model, route, CLIMB_START, LANDING, STEP, wind=tailwind
    )

    assert apex < calm_apex
    fitted = flown_climb_distance(
        performance_model, CLIMB_START, apex, STEP, wind=tailwind
    ) + flown_descent_distance(performance_model, apex, LANDING, STEP, wind=tailwind)
    assert fitted == pytest.approx(route, abs=1.0)


# The builder in a real wind. BOS-JFK is inside the weather file's domain.


def weather_mission(origin='BOS', destination='JFK'):
    return Mission(
        origin=origin,
        destination=destination,
        departure=iso_to_timestamp('2024-09-01T12:00:00'),
        arrival=iso_to_timestamp('2024-09-01T13:30:00'),
        aircraft_type='220',
        load_factor=1.0,
    )


def landing_miss(performance, mission, **legacy_options):
    builder = tb.AdjustableLegacyBuilder(
        options=tb.Options(use_weather=True, iterate_mass=False),
        legacy_options=tb.AdjustableLegacyOptions(
            descent_distance_from_model=True, **legacy_options
        ),
    )
    traj = builder.fly(performance, mission)
    route = GroundTrack.great_circle(
        mission.origin_position.location,
        mission.destination_position.location,
        allow_overstep=True,
    ).total_distance
    return float(traj.ground_distance[-1]) - route


@pytest.mark.forked
def test_a_wind_aware_descent_estimate_lands_on_the_destination(performance_model):
    mission = weather_mission()

    unaware = landing_miss(performance_model, mission)
    aware = landing_miss(performance_model, mission, wind_aware_apex=True)

    assert abs(unaware) > 100.0
    assert abs(aware) < 0.1 * abs(unaware)
    assert abs(aware) < 50.0


def test_a_wind_aware_apex_needs_weather(performance_model):
    with pytest.raises(ValueError, match='weather'):
        tb.AdjustableLegacyBuilder(
            options=tb.Options(use_weather=False),
            legacy_options=tb.AdjustableLegacyOptions(wind_aware_apex=True),
        )
