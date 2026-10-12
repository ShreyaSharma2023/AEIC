# TODO: Remove this when we move to Python 3.14+.
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

import numpy as np

from AEIC.config import config
from AEIC.missions import Mission
from AEIC.performance.models import BasePerformanceModel
from AEIC.performance.types import AircraftState, SimpleFlightRules
from AEIC.storage import FlightPhase
from AEIC.units import (
    FEET_TO_METERS,
    FL_TO_METERS,
    METERS_TO_FL,
    MINUTES_TO_SECONDS,
    NAUTICAL_MILES_TO_METERS,
)
from AEIC.weather import Weather

from .. import GroundTrack, Trajectory
from .base import Builder, Context, Options
from .legacy import LegacyOptions


def level_change_altitudes(
    start_altitude: float,
    final_altitude: float,
    altitude_step: float,
    climbing: bool,
) -> np.ndarray:
    """Altitudes [m] at the ends of the constant-altitude steps of a climb or
    descent from `start_altitude` to `final_altitude`.

    The phase is discretized into steps of `altitude_step`, with a possible
    extra short step at the end to reach the target altitude. The result is
    empty if there is nothing to fly."""
    altitudes = np.arange(
        start_altitude, final_altitude, altitude_step if climbing else -altitude_step
    )
    if len(altitudes) == 0:
        return altitudes
    # Snap floating-point step accumulation to the exact endpoint to avoid a
    # near-zero extra segment.
    if np.isclose(altitudes[-1], final_altitude, rtol=0.0, atol=1e-9):
        altitudes[-1] = final_altitude
    elif (climbing and altitudes[-1] < final_altitude) or (
        not climbing and altitudes[-1] > final_altitude
    ):
        altitudes = np.append(altitudes, final_altitude)
    return altitudes


@dataclass
class AdjustableLegacyOptions(LegacyOptions):
    """Additional options for the adjustable legacy trajectory builder."""

    descent_distance_from_model: bool = False
    """Estimate the descent distance, which decides where cruise ends, by
    flying the descent on the performance model, instead of by the static rule
    of 18.228347 times the altitude drop. The static rule assumes a glide ratio
    that a given aircraft does not have, so without this flights land short of
    (or past) the destination by an amount that depends on the aircraft, the
    cruise altitude and the arrival airport. An explicit `descent_distance`
    adjustment takes precedence."""

    wind_aware_apex: bool = False
    """Size the climb, the descent and the apex of a short route in the wind
    that the flight will meet, instead of with no wind. Without it, a wind
    changes how far the climb and descent go, so a route with no room for
    cruise overshoots (or undershoots) by a large share of its length, and the
    ground-distance iteration then has to correct it by a large amount. Needs
    `use_weather`."""

    consistent_starting_mass: bool = False
    """Iterate the starting mass so that it stays equal to what it is made of:
    the fixed part (empty mass, payload, diversion and hold fuel) plus the trip
    fuel plus the reserve, with the trip fuel set to the fuel the last flight
    burned and the reserve recomputed from it. The default correction instead
    subtracts the shortfall of the trip fuel from the starting mass and leaves
    the reserve at what the first, crude estimate made it. That is only right
    if the starting mass was the sum of its parts, and slow when the estimate is
    about twice the burn, as it is for long flights.

    A flight whose starting mass would then exceed the aircraft's maximum mass
    is weight-limited: it flies at the maximum mass with the payload reduced by
    the excess, which is recorded as the trajectory's `payload_mass`. If even
    no payload would fit, it is an error."""


GroundSpeed = Callable[[float, float, float], float]
"""Ground speed [m/s] of the aircraft at a distance [m] along the route, at an
altitude [m], flying at a horizontal airspeed [m/s]."""


@dataclass(frozen=True)
class RouteWind:
    """The wind along a route, as it acts on the climb and descent estimates.

    `ground_speed` gives the ground speed at a distance along the route, which
    is clamped to [0, `route_distance`] before it is called, since a level
    change near the end of the route can be estimated past it."""

    ground_speed: GroundSpeed
    route_distance: float


def _level_changes(
    performance: BasePerformanceModel,
    start_altitude: float,
    end_altitude: float,
    altitude_step: float,
    climbing: bool,
    aircraft_mass: float | Literal['min', 'max'],
) -> list[tuple[float, float, float]]:
    """(altitude, horizontal airspeed, duration) of each step of a level change,
    from the performance model: everything that does not depend on the wind."""
    altitudes = level_change_altitudes(
        start_altitude, end_altitude, altitude_step, climbing
    )
    rules = SimpleFlightRules.CLIMB if climbing else SimpleFlightRules.DESCEND
    steps = []
    for altitude, next_altitude in zip(altitudes[:-1], altitudes[1:]):
        perf = performance.evaluate(
            AircraftState(altitude=altitude, aircraft_mass=aircraft_mass), rules
        )
        horizontal_airspeed = float(
            np.sqrt(perf.true_airspeed**2 - perf.rate_of_climb**2)
        )
        duration = (next_altitude - altitude) / perf.rate_of_climb
        steps.append((float(altitude), horizontal_airspeed, float(duration)))
    return steps


def _distance_flown(
    steps: list[tuple[float, float, float]],
    wind: RouteWind | None,
    start_position: float,
) -> float:
    """Ground distance of the steps of a level change that starts
    `start_position` along the route."""
    distance = 0.0
    for altitude, airspeed, duration in steps:
        if wind is None:
            speed = airspeed
        else:
            position = min(max(start_position + distance, 0.0), wind.route_distance)
            speed = wind.ground_speed(position, altitude, airspeed)
        distance += speed * duration
    return distance


def flown_descent_distance(
    performance: BasePerformanceModel,
    start_altitude: float,
    end_altitude: float,
    altitude_step: float,
    aircraft_mass: float | Literal['min', 'max'] = 'min',
    wind: RouteWind | None = None,
) -> float:
    """Ground distance [m] covered by descending from `start_altitude` to
    `end_altitude` on the given performance model, with no wind unless `wind`
    is given.

    Mirrors the descent that `AdjustableLegacyBuilder` flies: constant-altitude
    steps, performance evaluated at the start of each, and a step taking
    `altitude change / rate of descent` seconds at the horizontal speed. The
    descent is evaluated at one mass, by default the lightest the model
    tabulates, which is the landing end of the range. It only matters for models
    whose descent depends on mass, since the legacy BADA-derived tables tabulate
    a single mass.

    In wind the descent is taken to end at the end of the route, so where it
    starts, and hence the wind it flies through, depends on how far it goes.
    That distance is the one that is consistent with its own start, found by
    bisection."""
    steps = _level_changes(
        performance, start_altitude, end_altitude, altitude_step, False, aircraft_mass
    )
    if wind is None:
        return _distance_flown(steps, None, 0.0)

    def excess(distance: float) -> float:
        """How far the descent that starts `distance` before the end of the
        route goes beyond `distance`; falls as `distance` grows."""
        return _distance_flown(steps, wind, wind.route_distance - distance) - distance

    if not steps or excess(0.0) <= 0.0:
        return 0.0
    lo, hi = 0.0, _distance_flown(steps, None, 0.0) + 1.0
    while excess(hi) > 0.0:
        lo, hi = hi, 2.0 * hi
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if excess(mid) > 0.0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def flown_climb_distance(
    performance: BasePerformanceModel,
    start_altitude: float,
    end_altitude: float,
    altitude_step: float,
    aircraft_mass: float | Literal['min', 'max'] = 'max',
    wind: RouteWind | None = None,
) -> float:
    """Ground distance [m] covered by climbing from `start_altitude` to
    `end_altitude` on the given performance model, with no wind unless `wind`
    is given.

    Mirrors `flown_descent_distance`, for the climb, which starts at the start
    of the route. Evaluated at one mass, by default the heaviest the model
    tabulates, matching the aircraft's actual mass at the start of a climb."""
    steps = _level_changes(
        performance, start_altitude, end_altitude, altitude_step, True, aircraft_mass
    )
    return _distance_flown(steps, wind, 0.0)


APEX_OVERCORRECTION = 3.0
"""How many times the overshoot the apex's route distance is lowered by when a
flight with no room for cruise overshoots the destination. The flown distance
is not a smooth function of the apex altitude (it has steps and dips, and is
steeper in wind), so correcting by exactly the overshoot oscillates and did not
converge for about 1% of Embraer ERJ-145 flights. Correcting by more makes the
next flight land short, and the cruise that then fits absorbs the difference.
Chosen from a comparison of 1x, 2x, 3x and a damped 0.4x on 500 ERJ-145
flights: the failure counts were 5, 2, 0 and worse than 1x."""


def no_cruise_apex_altitude(
    performance: BasePerformanceModel,
    route_distance: float,
    climb_start_altitude: float,
    descent_end_altitude: float,
    altitude_step: float,
    wind: RouteWind | None = None,
) -> float:
    """The altitude at which a climb from `climb_start_altitude` and a descent
    to `descent_end_altitude`, flown back to back with no cruise in between,
    together cover exactly `route_distance` [m] - the "apex" of a route too
    short to reach the normal cruise altitude and still leave room to cruise.

    The distance to climb to a given altitude increases with altitude, and
    the distance to descend from a given altitude to touchdown also increases
    with altitude, so their sum is monotonically increasing in altitude and
    has exactly one altitude at which it equals `route_distance`; found here
    by bisection. Raises `ValueError` if the route is shorter than the
    distance needed to climb to `climb_start_altitude` and immediately
    descend from there - the shortest climb-and-descent this can fly."""

    def total_distance(altitude: float) -> float:
        return flown_climb_distance(
            performance, climb_start_altitude, altitude, altitude_step, wind=wind
        ) + flown_descent_distance(
            performance, altitude, descent_end_altitude, altitude_step, wind=wind
        )

    shortest_distance = total_distance(climb_start_altitude)
    if route_distance < shortest_distance:
        raise ValueError(
            f'Route is too short to fly: {route_distance:.0f} m is less than '
            f'the {shortest_distance:.0f} m needed to climb to '
            f'{climb_start_altitude:.0f} m and descend from there to '
            f'{descent_end_altitude:.0f} m.'
        )

    lo, hi = climb_start_altitude, performance.maximum_altitude
    for _ in range(50):
        mid = 0.5 * (lo + hi)
        if total_distance(mid) < route_distance:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


class AdjustableLegacyContext(Context):
    """Context for adjustable legacy trajectory builder."""

    class AdjustmentFunction(Protocol):
        """Protocol for context adjustment functions.

        An adjustment function takes in the context, mission, performance model
        possibly additional optional keyword arguments and returns a float
        value."""

        def __call__(
            self,
            context: AdjustableLegacyContext,
            mission: Mission,
            performance: BasePerformanceModel,
            **kwargs,
        ) -> float: ...

    # A context adjustment is either an adjustment function, a fixed float
    # value, or None (in which case no adjustment is applied and the behavior
    # falls back to the standard legacy trajectory builder behavior).
    ContextAdjustment = AdjustmentFunction | float | None

    def __init__(
        self,
        builder: AdjustableLegacyBuilder,
        ac_performance: BasePerformanceModel,
        mission: Mission,
        starting_mass: float | None,
        climb_start_altitude: ContextAdjustment = None,
        cruise_altitude: ContextAdjustment = None,
        descent_end_altitude: ContextAdjustment = None,
        descent_distance: ContextAdjustment = None,
        reserve_fuel: ContextAdjustment = None,
        divert_distance: ContextAdjustment = None,
        hold_time: ContextAdjustment = None,
    ):
        """Adjustment parameters (a "simple" adjustment is one that takes no
        additional arguments beyond the context, mission and performance
        model):

        - `climb_start_altitude`: Simple adjustment for climb start altitude,
          which defaults to 3000' above departure airport altitude if not
          provided.

        - `cruise_altitude`: Simple adjustment for cruise altitude, which
           defaults to 7000' below aircraft operating ceiling if not provided.

        - `descent_end_altitude`: Simple adjustment for descent end altitude,
           which defaults to 3000' above arrival airport altitude if not
           provided.

        - `descent_distance`: Simple adjustment for descent distance, which
           defaults to a value based on the difference between cruise altitude
           and descent end altitude if not provided.

        - `reserve_fuel`: Adjustment for reserve fuel mass, which defaults to
           5% of total fuel mass if not provided (additional parameter:
           `fuel_mass`, total fuel mass consumed based on approximate flight
           time and nominal fuel flow).

        - `divert_distance`: Adjustment for diversion distance, which defaults
          to 200 NM if flight time is over 3 hours and 100 NM if flight time is
          under 3 hours if not provided (additional parameter: `approx_time`,
          approximate flight time based on distance and nominal cruise speed).

        - `hold_time`: Adjustment for hold time, which defaults to 30 minutes
          if flight time is over 3 hours and 45 minutes if flight time is under
          3 hours if not provided (additional parameter: `approx_time`,
          approximate flight time based on distance and nominal cruise speed).

        """

        # The context constructor calculates all of the fixed information used
        # throughout the simulation by the trajectory builder.

        # Number of points in different flight phases. Last point of climb is
        # same as first point of cruise, last point of cruise is same as first
        # point of descent.

        # Generate great circle ground track between departure and arrival
        # locations. Allow the trajectory to step beyond the end of the ground
        # track to account for the fact that we just guess the point at which
        # we need to start the descent - depending on the exact airspeed values
        # returned by the performance model, we will either reach the ground
        # before or after the destination point. If the airspeeds are higher
        # than expected by the descent start point estimate, we will fly past
        # the destination, so will need to step along the ground track beyond the
        # final destination waypoint.
        ground_track = GroundTrack.great_circle(
            mission.origin_position.location,
            mission.destination_position.location,
            allow_overstep=True,
        )

        # Climb defined as starting 3000' above airport (adjustable).
        if climb_start_altitude is None:
            self.clm_start_altitude = (
                mission.origin_position.altitude + 3000.0 * FEET_TO_METERS
            )
        else:
            self.clm_start_altitude = self.apply_adjustment(
                climb_start_altitude, mission, ac_performance
            )

        # If starting altitude is above operating ceiling, set start altitude
        # to departure airport altitude.
        if self.clm_start_altitude >= ac_performance.maximum_altitude:
            self.clm_start_altitude = mission.origin_position.altitude

        # Cruise altitude is the mission's flight level if it has one, else the
        # operating ceiling - 7000 feet (adjustable). The flight level is a
        # target: one above the ceiling is flown at the ceiling, and a route too
        # short to reach it climbs only as high as it can (the apex below). The
        # builder counts both, and the missions with no flight level.
        cruise_altitude_limit: str | None = None
        if cruise_altitude is None and mission.flight_level is not None:
            self.crz_start_altitude = mission.flight_level * FL_TO_METERS
            if self.crz_start_altitude > ac_performance.maximum_altitude:
                self.crz_start_altitude = ac_performance.maximum_altitude
                cruise_altitude_limit = 'ceiling'
        elif cruise_altitude is None:
            cruise_altitude_limit = 'no_flight_level'
            self.crz_start_altitude = (
                ac_performance.maximum_altitude - 7000.0 * FEET_TO_METERS
            )
        else:
            self.crz_start_altitude = self.apply_adjustment(
                cruise_altitude, mission, ac_performance
            )

        # Ensure cruise altitude is above the starting altitude.
        if self.crz_start_altitude < self.clm_start_altitude:
            self.crz_start_altitude = self.clm_start_altitude

        # Prevent flying above aircraft ceiling. (NOTE: this will only trigger
        # due to random variables not currently implemented.)
        if self.crz_start_altitude > ac_performance.maximum_altitude:
            self.crz_start_altitude = ac_performance.maximum_altitude

        # In legacy trajectory, descent start altitude is equal to cruise
        # altitude.
        self.des_start_altitude = self.crz_start_altitude

        # Set descent altitude based on 3000' above arrival airport altitude;
        # clamp to aircraft operating ceiling if needed (adjustable).
        if descent_end_altitude is None:
            self.des_end_altitude = (
                mission.destination_position.altitude + 3000.0 * FEET_TO_METERS
            )
        else:
            self.des_end_altitude = self.apply_adjustment(
                descent_end_altitude, mission, ac_performance
            )

        if self.des_end_altitude >= ac_performance.maximum_altitude:
            self.des_end_altitude = ac_performance.maximum_altitude

        if self.crz_start_altitude < self.clm_start_altitude:
            raise ValueError(
                "Departure airport + 3000ft should not be higher "
                "than start of cruise point"
            )
        if self.des_end_altitude > self.des_start_altitude:
            raise ValueError(
                "Arrival airport + 3000ft should not be higher than end of cruise point"
            )
        # Initialize weather regridding when requested.
        self.weather: Weather | None = None
        if builder.options.use_weather:
            self.weather = Weather(
                data_dir=config.weather.weather_data_dir,
                file_resolution=config.weather.file_resolution,
                data_resolution=config.weather.data_resolution,
                file_format=config.weather.file_format,
            )

        # The wind the climb and descent estimates are made in, if requested.
        self.route_wind: RouteWind | None = None
        if builder.wind_aware_apex:
            assert self.weather is not None
            self.route_wind = self._route_wind(self.weather, mission, ground_track)

        if descent_distance is not None:
            self.descent_dist_approx = self.apply_adjustment(
                descent_distance, mission, ac_performance
            )
        elif builder.descent_distance_from_model:
            self.descent_dist_approx = flown_descent_distance(
                ac_performance,
                self.des_start_altitude,
                self.des_end_altitude,
                builder.altitude_step,
                wind=self.route_wind,
            )
        else:
            self.descent_dist_approx = 18.228347 * (
                self.des_start_altitude - self.des_end_altitude
            )
        if self.descent_dist_approx < 0:
            raise ValueError('Descent distance must be non-negative')

        # The distance the apex is sized to fit, which ground-distance iteration
        # moves when the flight lands past or short of the destination, and
        # whether the apex may be changed at all (see `lower_apex`).
        self.apex_route_distance = ground_track.total_distance
        self.apex_is_adjustable = cruise_altitude is None and descent_distance is None
        # The apex of a short route is never above the cruise altitude the
        # flight would have had with room to cruise.
        self.apex_ceiling = self.crz_start_altitude

        # A route too short to reach the normal cruise altitude and still
        # leave room for any cruise would overshoot the destination: the
        # climb to cruise altitude plus the descent back down already cover
        # more distance than the route. Only kicks in for the default cruise
        # altitude and descent distance; an explicit adjustment for either is
        # left alone.
        if cruise_altitude is None and descent_distance is None:
            climb_distance = flown_climb_distance(
                ac_performance,
                self.clm_start_altitude,
                self.crz_start_altitude,
                builder.altitude_step,
                wind=self.route_wind,
            )
            if climb_distance + self.descent_dist_approx > ground_track.total_distance:
                self.crz_start_altitude = no_cruise_apex_altitude(
                    ac_performance,
                    ground_track.total_distance,
                    self.clm_start_altitude,
                    self.des_end_altitude,
                    builder.altitude_step,
                    wind=self.route_wind,
                )
                self.des_start_altitude = self.crz_start_altitude
                self.descent_dist_approx = flown_descent_distance(
                    ac_performance,
                    self.crz_start_altitude,
                    self.des_end_altitude,
                    builder.altitude_step,
                    wind=self.route_wind,
                )
                cruise_altitude_limit = 'route'

        if cruise_altitude_limit is not None:
            builder.cruise_altitude_limits[cruise_altitude_limit] += 1

        # The part of the starting mass that does not depend on the trip fuel,
        # set when the starting mass is calculated.
        self.fixed_mass: float | None = None
        self.start_is_fitted = False
        self.fuel_burned: float | None = None
        self.payload_capacity: float | None = None
        self.payload_mass = None

        # Save reserve fuel, divert distance and hold time adjustments for use
        # in starting mass calculation.
        self.reserve_fuel = reserve_fuel
        self.divert_distance = divert_distance
        self.hold_time = hold_time

        # Pass information to base context class constructor.
        super().__init__(
            builder,
            ac_performance,
            mission,
            ground_track,
            initial_altitude=self.clm_start_altitude,
            starting_mass=starting_mass,
        )

    @staticmethod
    def _route_wind(
        weather: Weather, mission: Mission, ground_track: GroundTrack
    ) -> RouteWind:
        """The wind along the route at the departure time, as the flight meets
        it: the ground speed that `Weather.get_track_vector` gives along the
        ground track."""

        def ground_speed(
            distance: float, altitude: float, horizontal_airspeed: float
        ) -> float:
            return weather.get_track_vector(
                time=mission.departure,
                gt_point=ground_track.step(distance, 0.0),
                altitude=altitude,
                horizontal_airspeed=horizontal_airspeed,
            ).ground_speed

        return RouteWind(ground_speed, ground_track.total_distance)

    def lower_apex(self, overshoot: float) -> bool:
        """Size the apex for a route shorter (or longer) by `overshoot` [m].

        The apex altitude is chosen from estimates made with no wind and at
        fixed masses, so the flown climb and descent can cover more or less
        ground than it expected. Sizing it for a route `APEX_OVERCORRECTION`
        times `overshoot` shorter makes the next flight land short, and the
        cruise that then fits brings it onto the destination. Does nothing, returning
        False, if the cruise altitude or descent distance was set explicitly,
        as the apex is then not this context's to change, or if the new apex is
        at the altitude it already has (as when it is at its ceiling), so that
        the flight would be the same again: the caller then feeds the measured
        descent back instead.

        Raises:
            ValueError: If the route is too short for any climb and descent, as
                for `no_cruise_apex_altitude`."""
        if not self.apex_is_adjustable:
            return False
        apex_route_distance = self.apex_route_distance - overshoot * APEX_OVERCORRECTION
        apex_altitude = min(
            no_cruise_apex_altitude(
                self.ac_performance,
                apex_route_distance,
                self.clm_start_altitude,
                self.des_end_altitude,
                self.builder.altitude_step,
                wind=self.route_wind,
            ),
            self.apex_ceiling,
        )
        if apex_altitude == self.crz_start_altitude:
            return False
        self.apex_route_distance = apex_route_distance
        self.crz_start_altitude = apex_altitude
        self.des_start_altitude = self.crz_start_altitude
        self.descent_dist_approx = flown_descent_distance(
            self.ac_performance,
            self.crz_start_altitude,
            self.des_end_altitude,
            self.builder.altitude_step,
            wind=self.route_wind,
        )
        return True

    def apply_adjustment(
        self,
        adjustment: ContextAdjustment,
        mission: Mission,
        performance: BasePerformanceModel,
        **kwargs,
    ) -> float:
        """Helper function to apply a context adjustment."""
        if adjustment is None:
            raise RuntimeError('Attempting to apply an empty ContextAdjustment.')
        elif isinstance(adjustment, Callable):
            return adjustment(self, mission, performance, **kwargs)
        else:
            return adjustment


class AdjustableLegacyBuilder(Builder):
    """Model for determining flight trajectories using the legacy method
    from AEIC v2.

    Args:
        options (Options): Base options for trajectory building.
        legacy_options (LegacyOptions): Builder-specific options for legacy
            trajectory builder.
    """

    CONTEXT_CLASS = AdjustableLegacyContext

    def __init__(
        self,
        options: Options = Options(),
        legacy_options: LegacyOptions = LegacyOptions(),
    ):
        super().__init__(options)

        # Altitude step to use in climb and descent phases of flight.
        self.altitude_step = legacy_options.altitude_step

        # Ground distance step to use in cruise phase.
        self.cruise_step = legacy_options.cruise_step

        self.fuel_LHV = legacy_options.fuel_LHV

        # Only AdjustableLegacyOptions has this; a plain LegacyOptions is fine.
        self.descent_distance_from_model = getattr(
            legacy_options, 'descent_distance_from_model', False
        )
        self.consistent_starting_mass = getattr(
            legacy_options, 'consistent_starting_mass', False
        )
        self.wind_aware_apex = getattr(legacy_options, 'wind_aware_apex', False)
        if self.wind_aware_apex and not self.options.use_weather:
            raise ValueError(
                'wind_aware_apex needs use_weather, as it uses the weather'
            )
        # How many flights did not cruise at their mission's flight level, by
        # reason: 'ceiling' (level above the model's ceiling), 'route' (too
        # short to reach it) and 'no_flight_level' (the mission has none, so
        # the ceiling less 7000 ft was used).
        self.cruise_altitude_limits: Counter[str] = Counter()

    def reserve_mass(self, fuel_mass: float) -> float:
        """Reserve fuel [kg] carried for a flight with `fuel_mass` of trip
        fuel: 5% of it unless the context adjusts it."""
        if self.reserve_fuel is None:
            return fuel_mass * 0.05
        return self.apply_adjustment(
            self.reserve_fuel,
            self.mission,
            self.ac_performance,
            fuel_mass=fuel_mass,
        )

    def _fly_iteration(self) -> tuple[Trajectory, float]:
        traj, mass_residual = super()._fly_iteration()
        self.fuel_burned = float(self.starting_mass - traj.aircraft_mass[-1])
        if (
            self.consistent_starting_mass
            and self.fixed_mass is not None
            and not self.start_is_fitted
        ):
            # The first estimate is capped at the maximum mass and carries the
            # full payload, so it is not consistent with its own burn even if
            # the burn happens to be within tolerance: report at least the
            # tolerance so that it is corrected once.
            tolerance = self.options.mass_iter_reltol
            if abs(mass_residual) < tolerance:
                mass_residual = tolerance if mass_residual >= 0 else -tolerance
        return traj, mass_residual

    def correct_starting_mass(self, mass_residual: float) -> None:
        """Correct the starting mass after a flight, as the base builder does
        unless `consistent_starting_mass` is set and the starting mass was
        calculated from its parts."""
        if not self.consistent_starting_mass or self.fixed_mass is None:
            super().correct_starting_mass(mass_residual)
            return
        assert self.payload_capacity is not None
        self.start_is_fitted = True
        fuel_burned = self.fuel_burned
        self.total_fuel_mass = fuel_burned
        required = self.fixed_mass + fuel_burned + self.reserve_mass(fuel_burned)
        excess = required - self.ac_performance.maximum_mass
        if excess <= 0.0:
            self.starting_mass = required
            self.payload_mass = self.payload_capacity
            return
        # Weight-limited: fly at the maximum mass and carry less payload.
        if excess > self.payload_capacity:
            raise ValueError(
                'Cannot fly this route even with no payload: the fuel it needs '
                f'({fuel_burned:.0f} kg, plus reserve, diversion and hold) takes '
                f'the aircraft {excess - self.payload_capacity:.0f} kg over its '
                f'maximum mass of {self.ac_performance.maximum_mass:.0f} kg.'
            )
        self.starting_mass = self.ac_performance.maximum_mass
        self.payload_mass = self.payload_capacity - excess

    def calc_starting_mass(self) -> float:
        """Calculates the starting mass using AEIC v2 methods.
        Sets both starting mass and non-reserve/hold/divert fuel mass."""

        perf = self.ac_performance.evaluate(
            AircraftState(
                altitude=self.crz_start_altitude,
                aircraft_mass='max',
            ),
            SimpleFlightRules.CRUISE,
        )

        lowest_cruise_altitude = self.ac_performance.lowest_cruise_altitude

        perf_low = self.ac_performance.evaluate(
            AircraftState(
                altitude=lowest_cruise_altitude,
                aircraft_mass='min',
            ),
            SimpleFlightRules.CRUISE,
        )

        # Figure out startingMass components per AEIC v2:
        #
        #      |   empty weight
        #      | + payload weight
        #      | + fuel weight
        #      | + fuel reserves weight
        #      | + fuel divert weight
        #      | + fuel hold weight
        #      | _______________________
        #        = Take-off weight

        # Payload.
        payload_mass = self.ac_performance.maximum_payload * self.mission.load_factor

        # Fuel needed (distance / velocity * fuel flow rate).
        approx_time = self.ground_track.total_distance / perf.true_airspeed
        fuel_mass = approx_time * perf.fuel_flow

        reserve_mass = self.reserve_mass(fuel_mass)

        # Diversion and hold fuel per AEIC v2.
        # TODO: ADJUSTMENT POINT
        if self.divert_distance is None:
            if approx_time > 180 * MINUTES_TO_SECONDS:
                divert_dist = 200.0 * NAUTICAL_MILES_TO_METERS
            else:
                divert_dist = 100.0 * NAUTICAL_MILES_TO_METERS
        else:
            divert_dist = self.apply_adjustment(
                self.divert_distance,
                self.mission,
                self.ac_performance,
                approx_time=approx_time,
            )
        if self.hold_time is None:
            if approx_time > 180 * MINUTES_TO_SECONDS:
                hold_time = 30 * MINUTES_TO_SECONDS
            else:
                hold_time = 45 * MINUTES_TO_SECONDS
        else:
            hold_time = self.apply_adjustment(
                self.hold_time,
                self.mission,
                self.ac_performance,
                approx_time=approx_time,
            )
        divert_mass = divert_dist / perf.true_airspeed * perf.fuel_flow
        hold_mass = hold_time * perf_low.fuel_flow

        starting_mass = (
            self.ac_performance.empty_mass
            + payload_mass
            + fuel_mass
            + reserve_mass
            + divert_mass
            + hold_mass
        )

        # The rest of the starting mass, which the trip fuel does not change.
        self.fixed_mass = (
            self.ac_performance.empty_mass + payload_mass + divert_mass + hold_mass
        )

        if self.consistent_starting_mass:
            self.payload_capacity = payload_mass
            self.payload_mass = payload_mass

        # Limit to MTOM if overweight.
        if starting_mass > self.ac_performance.maximum_mass:
            starting_mass = self.ac_performance.maximum_mass

        # Set fuel mass (for weight residual calculation).
        self.total_fuel_mass = fuel_mass

        return starting_mass

    def fly_climb(self, traj: Trajectory) -> None:
        """Simulate the climb phase of a trajectory.

        Delegates to :meth:`_fly_level_change` with climb flight rules, integrating
        from the end of the previous phase up to the cruise-start altitude.
        """
        self._fly_level_change(
            traj,
            FlightPhase.CLIMB,
            SimpleFlightRules.CLIMB,
            self.crz_start_altitude,
        )

    def fly_descent(self, traj: Trajectory) -> None:
        """Simulate the descent phase of a trajectory.

        Delegates to :meth:`_fly_level_change` with descent flight rules,
        integrating from the cruise altitude down to the descent-end altitude.
        The final per-phase point count is decremented by one to account for
        the shared boundary point with the next phase.
        """
        self._fly_level_change(
            traj,
            FlightPhase.DESCENT,
            SimpleFlightRules.DESCEND,
            self.des_end_altitude,
        )
        if traj.n_descent > 0:
            traj.n_descent -= 1

    def _fly_level_change(
        self,
        traj: Trajectory,
        flight_phase: FlightPhase,
        flight_rule: SimpleFlightRules,
        final_altitude: float,
    ) -> None:
        """Simulate climb and descent phases.

        Computes state over segments involving level changes using AEIC v2
        methods based on BADA-3 formulas."""

        traj.set_phase(flight_phase)

        # Start climb at overall flight start point and start descent at end of
        # cruise point.
        if flight_phase == FlightPhase.CLIMB:
            pt = self._start_point(traj)
            traj.append(pt)
        else:
            pt = traj.make_point(-1)

        altitudes = level_change_altitudes(
            pt.altitude,
            final_altitude,
            self.altitude_step,
            climbing=flight_phase == FlightPhase.CLIMB,
        )
        if len(altitudes) == 0:
            return

        # Loop over altitude change segments.
        for start_altitude, end_altitude in zip(altitudes[:-1], altitudes[1:]):
            # Determine aircraft performance at start of segment.
            perf = self.ac_performance.evaluate(
                AircraftState(
                    altitude=start_altitude,  # type: ignore
                    true_airspeed=pt.true_airspeed,  # type: ignore
                    rate_of_climb=pt.rate_of_climb,  # type: ignore
                    aircraft_mass=pt.aircraft_mass,  # type: ignore
                ),
                flight_rule,
            )

            horizontal_airspeed = np.sqrt(perf.true_airspeed**2 - perf.rate_of_climb**2)

            # Time to complete altitude change segment and total fuel burned.
            seg_time = (end_altitude - start_altitude) / perf.rate_of_climb
            seg_fuel = perf.fuel_flow * seg_time

            # Ground speed, including weather effects if required.
            if self.weather is None:
                pt.ground_speed = horizontal_airspeed
                pt.heading = pt.azimuth
            else:
                track_vector = self.weather.get_track_vector(
                    time=self.mission.departure,
                    gt_point=self.ground_track.step(pt.ground_distance, 0.0),
                    altitude=start_altitude,
                    horizontal_airspeed=horizontal_airspeed,
                    track_azimuth=pt.azimuth,
                )
                pt.ground_speed, pt.heading = (
                    track_vector.ground_speed,
                    track_vector.heading,
                )

            pt.altitude = end_altitude
            pt.flight_level = pt.altitude * METERS_TO_FL

            # Calculate distance along route travelled.
            dist = pt.ground_speed * seg_time

            # Take step along great circle route.
            gpt = self.ground_track.step(pt.ground_distance, dist)
            pt.longitude = gpt.location.longitude
            pt.latitude = gpt.location.latitude
            pt.azimuth = gpt.azimuth

            # Calculate fuel required for acceleration.
            match flight_phase:
                case FlightPhase.CLIMB:
                    # Account for acceleration/deceleration over the segment
                    # using end-of-segment tas approximated using start of
                    # segment TAS, ROCD and mass and end-of-segment altitude.
                    perf_end = self.ac_performance.evaluate(
                        AircraftState(
                            altitude=end_altitude,  # type: ignore
                            true_airspeed=pt.true_airspeed,  # type: ignore
                            rate_of_climb=pt.rate_of_climb,  # type: ignore
                            aircraft_mass=pt.aircraft_mass,  # type: ignore
                        ),
                        flight_rule,
                    )

                    kinetic_energy_chg = (
                        0.5
                        * pt.aircraft_mass
                        * (perf_end.true_airspeed**2 - perf.true_airspeed**2)
                    )

                    # NOTE: I have no idea where AEIC v2 got the efficiency of
                    # 0.15 from.
                    # TODO: ADJUSTMENT POINT
                    efficiency = 0.15
                    accel_fuel = kinetic_energy_chg / self.fuel_LHV / efficiency
                    seg_fuel += accel_fuel
                case FlightPhase.DESCENT:
                    # For descent, assumed fuel flow is essentially just engine
                    # at idle.
                    pass

            # We cannot gain fuel by decelerating in a conventional fuel
            # aircraft.
            if seg_fuel < 0:
                seg_fuel = 0

            # Update the state vector.
            pt.fuel_mass -= seg_fuel
            pt.aircraft_mass -= seg_fuel
            pt.ground_distance += dist

            pt.flight_time += seg_time

            traj.fuel_flow[-1] = perf.fuel_flow
            traj.true_airspeed[-1] = perf.true_airspeed
            traj.rate_of_climb[-1] = perf.rate_of_climb

            traj.append(pt)

    def fly_cruise(self, traj: Trajectory):
        """Simulate cruise phase.

        Computes state over cruise segment using AEIC v2 methods based on
        BADA-3 formulas."""

        traj.set_phase(FlightPhase.CRUISE)

        # Start cruise at end-of-climb position and mass (fuel flow, TAS will
        # be replaced).
        pt = traj.make_point(-1)

        # Cruise at constant altitude.
        pt.altitude = self.crz_start_altitude
        pt.flight_level = self.crz_start_altitude * METERS_TO_FL

        # Cruise end distance based on estimated descent distance.
        end_dist = self.ground_track.total_distance - self.descent_dist_approx

        # If there is not enough distance to fly before starting descent, skip
        # cruise phase.
        if pt.ground_distance >= end_dist:
            return

        # Cruise is discretized into ground distance steps with a possible
        # extra "short step" at the end to reach the target distance.
        distances = np.arange(pt.ground_distance, end_dist, self.cruise_step)
        if distances[-1] != end_dist:
            distances = np.append(distances, end_dist)

        # Top of climb, entering cruise.
        pt.rate_of_climb = 0

        # Get fuel flow, ground speed, etc. for cruise segments.
        for distance in distances[1:]:
            # Get fuel flow rate from performance model.
            perf = self.ac_performance.evaluate(
                AircraftState(
                    altitude=pt.altitude,
                    true_airspeed=pt.true_airspeed,  # type: ignore
                    rate_of_climb=0,
                    aircraft_mass=pt.aircraft_mass,  # type: ignore
                ),
                SimpleFlightRules.CRUISE,
            )

            if self.weather is not None:
                track_vector = self.weather.get_track_vector(
                    time=self.mission.departure,
                    gt_point=self.ground_track.location(distance),
                    altitude=pt.altitude,
                    horizontal_airspeed=perf.true_airspeed,
                    track_azimuth=pt.azimuth,
                )
                pt.ground_speed, pt.heading = (
                    track_vector.ground_speed,
                    track_vector.heading,
                )
            else:
                pt.ground_speed = perf.true_airspeed
                pt.heading = pt.azimuth

            # Take step along great circle route.
            gpt = self.ground_track.location(distance)
            pt.longitude = gpt.location.longitude
            pt.latitude = gpt.location.latitude
            pt.azimuth = gpt.azimuth

            # Calculate time required to fly the segment.
            ground_distance_step = distance - pt.ground_distance
            pt.ground_distance = distance
            seg_time = ground_distance_step / pt.ground_speed

            # Calculate fuel burn in [kg] over the segment.
            seg_fuel = perf.fuel_flow * seg_time

            # Set aircraft state values.
            pt.fuel_mass -= seg_fuel
            pt.aircraft_mass -= seg_fuel
            pt.flight_time += seg_time

            # Store performance data for point at beginning of this step.
            traj.true_airspeed[-1] = perf.true_airspeed
            traj.rate_of_climb[-1] = perf.rate_of_climb
            traj.fuel_flow[-1] = perf.fuel_flow

            traj.append(pt)
