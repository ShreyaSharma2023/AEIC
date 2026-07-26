from dataclasses import dataclass

import numpy as np

from AEIC.config import config
from AEIC.missions import Mission
from AEIC.performance.models import LegacyPerformanceModel
from AEIC.performance.models.legacy import ROCDFilter
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


@dataclass
class LegacyOptions:
    """Additional options for the legacy trajectory builder."""

    altitude_step: float = 1000 * FEET_TO_METERS
    """Altitude step to use in climb and descent phases (m)."""

    cruise_step: float = 125 * NAUTICAL_MILES_TO_METERS
    """Ground distance step to use in cruise phase (m)."""

    fuel_LHV: float = 43.8e6
    """Lower heating value of the fuel used (J/kg)."""

    step_climb_min_distance_km: float | None = None
    """If set, missions without an explicit Mission.cruise_profile whose
    ground-track distance is at least this far automatically get a
    synthesized, evenly-spaced step-climb profile instead of a single
    constant cruise altitude. None (default) disables this and preserves the
    original single-FL behavior. This is a simple rule-based comparison tool,
    not a replacement for an empirically-sourced profile."""

    step_climb_interval_km: float = 2000.0 * NAUTICAL_MILES_TO_METERS / 1000.0
    """Approximate ground distance between synthesized step climbs (km).
    Only used when step_climb_min_distance_km is set."""

    step_climb_size_ft: float = 2000.0
    """Altitude gained per synthesized step climb (ft). Only used when
    step_climb_min_distance_km is set."""


def synthesize_step_climb_profile(
    start_fl: float,
    mission_distance_km: float,
    interval_km: float,
    step_size_ft: float,
    ceiling_fl: float,
) -> list[tuple[float, float]]:
    """Build an evenly-spaced synthetic step-climb schedule: one step of
    `step_size_ft` roughly every `interval_km`, starting from `start_fl` and
    never exceeding `ceiling_fl`. Steps are placed at even fractions of the
    mission distance. Returns a Mission.cruise_profile-compatible list.

    This is a rule-based placeholder for exploring/comparing against
    single-FL trajectories -- it does not reflect observed step-climb
    behavior (see Mission.cruise_profile for that)."""

    n_by_distance = max(0, int(mission_distance_km // interval_km))
    n_by_ceiling = max(0, int((ceiling_fl - start_fl) // (step_size_ft / 100.0)))
    n_steps = min(n_by_distance, n_by_ceiling)

    profile = [(0.0, start_fl)]
    for i in range(1, n_steps + 1):
        frac = i / (n_steps + 1)
        fl = start_fl + i * (step_size_ft / 100.0)
        profile.append((frac, fl))
    return profile


class LegacyContext(Context):
    """Context for legacy trajectory builder."""

    def __init__(
        self,
        builder: 'LegacyBuilder',
        ac_performance: LegacyPerformanceModel,
        mission: Mission,
        starting_mass: float | None,
    ):
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

        # Climb defined as starting 3000' above airport.
        self.clm_start_altitude = (
            mission.origin_position.altitude + 3000.0 * FEET_TO_METERS
        )

        # If starting altitude is above operating ceiling, set start altitude
        # to departure airport altitude.
        if self.clm_start_altitude >= ac_performance.maximum_altitude:
            self.clm_start_altitude = mission.origin_position.altitude

        # Use an observed step-climb profile when available (first breakpoint
        # gives the initial cruise altitude); else use a single observed
        # cruise FL when available; else fall back to rule-based estimate.
        self.cruise_profile = mission.cruise_profile
        if self.cruise_profile is not None:
            self.crz_start_altitude = self.cruise_profile[0][1] * 100.0 * FEET_TO_METERS
        elif mission.flight_level is not None:
            self.crz_start_altitude = mission.flight_level * 100.0 * FEET_TO_METERS
        else:
            # Cruise altitude is the operating ceiling - 7000 feet.
            self.crz_start_altitude = (
                ac_performance.maximum_altitude - 7000.0 * FEET_TO_METERS
            )

        # Clamp any borrowed/observed profile altitudes to this aircraft's
        # actual ceiling -- a probabilistically-matched profile may have come
        # from a different (e.g. heavier) variant with a higher service
        # ceiling than the one flying this mission.
        if self.cruise_profile is not None:
            self.cruise_profile = [
                (frac, min(fl, ac_performance.maximum_altitude * METERS_TO_FL))
                for frac, fl in self.cruise_profile
            ]

        # Ensure cruise altitude is above the starting altitude.
        if self.crz_start_altitude < self.clm_start_altitude:
            self.crz_start_altitude = self.clm_start_altitude

        # Prevent flying above aircraft ceiling. (NOTE: this will only trigger
        # due to random variables not currently implemented.)
        if self.crz_start_altitude > ac_performance.maximum_altitude:
            self.crz_start_altitude = ac_performance.maximum_altitude

        # Optionally auto-synthesize a rule-based step-climb profile for
        # sufficiently long missions that don't already carry an observed one.
        # This is a comparison/exploration tool (single fixed step size and
        # spacing) -- NOT a substitute for an empirically-sourced profile via
        # Mission.cruise_profile. Off by default (step_climb_min_distance_km
        # is None), so existing single-FL behavior is unaffected unless
        # explicitly enabled via LegacyOptions.
        if (
            self.cruise_profile is None
            and builder.step_climb_min_distance_km is not None
            and ground_track.total_distance
            >= builder.step_climb_min_distance_km * 1000.0
        ):
            self.cruise_profile = synthesize_step_climb_profile(
                start_fl=self.crz_start_altitude * METERS_TO_FL,
                mission_distance_km=ground_track.total_distance / 1000.0,
                interval_km=builder.step_climb_interval_km,
                step_size_ft=builder.step_climb_size_ft,
                ceiling_fl=ac_performance.maximum_altitude * METERS_TO_FL,
            )

        # Descent starts wherever cruise actually leaves off. With a step-
        # climb profile that is the last (highest) breakpoint, not the
        # initial cruise altitude (crz_start_altitude) -- using the latter
        # understates the real altitude drop, and thus descent_dist_approx
        # below, for any mission that climbs during cruise.
        if self.cruise_profile is not None:
            self.des_start_altitude = (
                max(fl for _, fl in self.cruise_profile) * 100.0 * FEET_TO_METERS
            )
        else:
            self.des_start_altitude = self.crz_start_altitude

        # Set descent altitude based on 3000' above arrival airport altitude;
        # clamp to aircraft operating ceiling if needed.
        self.des_end_altitude = (
            mission.destination_position.altitude + 3000.0 * FEET_TO_METERS
        )
        if self.des_end_altitude >= ac_performance.maximum_altitude:
            self.des_end_altitude = ac_performance.maximum_altitude

        if self.crz_start_altitude < self.clm_start_altitude:
            raise ValueError(
                "Departure airport + 3000ft should not be higher"
                "than start of cruise point"
            )
        if self.des_end_altitude > self.des_start_altitude:
            raise ValueError(
                "Arrival airport + 3000ft should not be higher than end of cruise point"
            )
        self.descent_dist_approx = 18.228347 * (
            self.des_start_altitude - self.des_end_altitude
        )
        if self.descent_dist_approx < 0:
            raise ValueError('Arrival airport should not be above cruise altitude')

        # Initialize weather regridding when requested.
        self.weather: Weather | None = None
        if builder.options.use_weather:
            self.weather = Weather(
                data_dir=config.weather.weather_data_dir,
                file_resolution=config.weather.file_resolution,
                data_resolution=config.weather.data_resolution,
                file_format=config.weather.file_format,
            )

        # Pass information to base context class constructor.
        super().__init__(
            builder,
            ac_performance,
            mission,
            ground_track,
            initial_altitude=self.clm_start_altitude,
            starting_mass=starting_mass,
        )


class LegacyBuilder(Builder):
    """Model for determining flight trajectories using the legacy method
    from AEIC v2.

    Args:
        options (Options): Base options for trajectory building.
        legacy_options (LegactyOptions): Builder-specific options for legacy
            trajectory builder.
    """

    CONTEXT_CLASS = LegacyContext

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

        self.step_climb_min_distance_km = legacy_options.step_climb_min_distance_km
        self.step_climb_interval_km = legacy_options.step_climb_interval_km
        self.step_climb_size_ft = legacy_options.step_climb_size_ft

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

        lowest_cruise_altitude = (
            min(self.ac_performance.performance_table(ROCDFilter.ZERO).fl)
            * FL_TO_METERS
        )

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

        # Reserve fuel (assumed 5%).
        reserve_mass = fuel_mass * 0.05

        # Diversion and hold fuel per AEIC v2.
        if approx_time > 180 * MINUTES_TO_SECONDS:
            divert_dist = 200.0 * NAUTICAL_MILES_TO_METERS
            hold_time = 30 * MINUTES_TO_SECONDS
        else:
            divert_dist = 100.0 * NAUTICAL_MILES_TO_METERS
            hold_time = 45 * MINUTES_TO_SECONDS
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
        traj.n_descent -= 1

    def _fly_level_change(
        self,
        traj: Trajectory,
        flight_phase: FlightPhase,
        flight_rule: SimpleFlightRules,
        final_altitude: float,
        start_pt=None,
    ) -> None:
        """Simulate climb and descent phases.

        Computes state over segments involving level changes using AEIC v2
        methods based on BADA-3 formulas.

        `flight_phase` only controls how the resulting points are labelled
        (and, for the initial climb, whether a fresh start point is created);
        whether this is actually a climb or a descent is determined by
        comparing `final_altitude` to the current altitude, so this can also
        be used for a step climb in the middle of a phase labelled CRUISE
        (via `start_pt`, to continue from the existing in-progress point
        instead of resetting to the flight's origin)."""

        traj.set_phase(flight_phase)

        # Start climb at overall flight start point and start descent at end of
        # cruise point, unless an explicit starting point has been provided
        # (used for a level change in the middle of an existing phase, e.g. a
        # cruise step climb).
        if start_pt is not None:
            pt = start_pt
        elif flight_phase == FlightPhase.CLIMB:
            pt = self._start_point(traj)
            traj.append(pt)
        else:
            pt = traj.make_point(-1)

        climbing = final_altitude >= pt.altitude

        # Flight phase is discretized into constant altitude steps with a
        # possible extra "short step" at the end to reach the target altitude.
        altitudes = np.arange(
            pt.altitude,
            final_altitude,
            self.altitude_step if climbing else -self.altitude_step,
        )
        if (climbing and altitudes[-1] < final_altitude) or (
            not climbing and altitudes[-1] > final_altitude
        ):
            altitudes = np.append(altitudes, final_altitude)

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

            fwd_tas = np.sqrt(perf.true_airspeed**2 - perf.rate_of_climb**2)

            # Time to complete altitude change segment and total fuel burned.
            seg_time = (end_altitude - start_altitude) / perf.rate_of_climb
            seg_fuel = perf.fuel_flow * seg_time

            # Ground speed, including weather effects if required.
            if self.weather is None:
                pt.ground_speed = fwd_tas
            else:
                pt.ground_speed = self.weather.get_ground_speed(
                    time=self.mission.departure,
                    gt_point=self.ground_track.location(pt.ground_distance),
                    altitude=start_altitude,
                    true_airspeed=fwd_tas,
                    azimuth=pt.azimuth,
                )
            pt.heading = pt.azimuth

            pt.altitude = end_altitude
            pt.flight_level = pt.altitude * METERS_TO_FL

            # Calculate distance along route travelled.
            dist = pt.ground_speed * seg_time

            # Take step along great circle route.
            gpt = self.ground_track.step(pt.ground_distance, dist)
            pt.longitude = gpt.location.longitude
            pt.latitude = gpt.location.latitude
            pt.azimuth = gpt.azimuth

            # Calculate fuel required for acceleration. This is keyed off
            # whether this segment is actually climbing (not off
            # `flight_phase`, which only controls point labelling) so that a
            # step climb flown mid-cruise still gets the same acceleration
            # accounting as the initial climb.
            if climbing:
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
                efficiency = 0.15
                accel_fuel = kinetic_energy_chg / self.fuel_LHV / efficiency
                seg_fuel += accel_fuel
            else:
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
        BADA-3 formulas. If the mission carries an observed cruise altitude
        profile (`self.cruise_profile`), cruise is flown as a sequence of
        constant-altitude segments connected by short climbs at the observed
        breakpoints, instead of a single constant altitude for the whole
        phase."""

        traj.set_phase(FlightPhase.CRUISE)

        # Start cruise at end-of-climb position and mass (fuel flow, TAS will
        # be replaced).
        pt = traj.make_point(-1)

        # Cruise at constant altitude (first profile breakpoint, if any).
        pt.altitude = self.crz_start_altitude
        pt.flight_level = self.crz_start_altitude * METERS_TO_FL

        # Cruise end distance based on estimated descent distance.
        end_dist = self.ground_track.total_distance - self.descent_dist_approx
        cruise_start_dist = pt.ground_distance

        # If there is not enough distance to fly before starting descent, skip
        # cruise phase.
        if cruise_start_dist >= end_dist:
            return

        # Top of climb, entering cruise.
        pt.rate_of_climb = 0

        cruise_len = end_dist - cruise_start_dist

        # Build the list of (distance, altitude) breakpoints where cruise
        # should step to a new altitude. With no observed profile this is
        # just the single starting altitude held to the end of cruise
        # (original behaviour, unchanged).
        if self.cruise_profile is not None:
            level_changes = [
                (cruise_start_dist + frac * cruise_len, fl * 100.0 * FEET_TO_METERS)
                for frac, fl in self.cruise_profile[1:]
                # A profile drawn from a longer real flight can specify
                # breakpoints beyond what this mission's cruise distance
                # can reach -- truncate rather than climbing past top of
                # descent.
                if cruise_start_dist + frac * cruise_len < end_dist
            ]
        else:
            level_changes = []

        for target_dist, target_altitude in level_changes:
            self._fly_cruise_segment(traj, pt, target_dist)
            # Observed cruise profiles are step *climbs*; a target at or
            # below the current altitude is not something this builder
            # attempts to fly (real step-downs mid-cruise are rare/data
            # artifacts) -- skip rather than mis-handle it as a climb.
            if target_altitude > pt.altitude:
                self._fly_level_change(
                    traj,
                    FlightPhase.CRUISE,
                    SimpleFlightRules.CLIMB,
                    target_altitude,
                    start_pt=pt,
                )
                pt = traj.make_point(-1)
                pt.rate_of_climb = 0

        self._fly_cruise_segment(traj, pt, end_dist)

    def _fly_cruise_segment(self, traj: Trajectory, pt, end_dist: float) -> None:
        """Fly a single constant-altitude cruise segment from the current
        point up to `end_dist`, appending points to `traj`."""

        # Cruise is discretized into ground distance steps with a possible
        # extra "short step" at the end to reach the target distance.
        distances = np.arange(pt.ground_distance, end_dist, self.cruise_step)
        if len(distances) == 0 or distances[-1] != end_dist:
            distances = np.append(distances, end_dist)

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
                pt.ground_speed = self.weather.get_ground_speed(
                    time=self.mission.departure,
                    gt_point=self.ground_track.location(distance),
                    altitude=pt.altitude,
                    true_airspeed=perf.true_airspeed,
                    azimuth=pt.azimuth,
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
