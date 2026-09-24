"""This module implements a performance model built from PIANO aircraft
performance exports.

PIANO cruise data is swept over Mach number as well as flight level and mass, and
every phase carries thrust, drag and trajectory columns.
"""

# TODO: Remove this when we migrate to Python 3.14+.
from __future__ import annotations

from typing import Any, Literal

import pandas as pd
from pydantic import PositiveFloat, PrivateAttr, model_validator

from AEIC.performance.interpolation import Interpolator, MachSweepInterpolator
from AEIC.performance.types import (
    AircraftState,
    Performance,
    SimpleFlightRules,
    SpeedData,
    TableInput,
)
from AEIC.units import FEET_TO_METERS, FL_TO_METERS, METERS_TO_FL
from AEIC.utils.standard_atmosphere import cas_to_tas, speed_of_sound_at_altitude

from .base import BasePerformanceModel, PerformanceTableInput

_FL100_M = 10000 * FEET_TO_METERS
"""Altitude below which the low CAS of a speed schedule is flown [m]."""


def cruise_mach_at_altitude(speeds: SpeedData, altitude: float) -> float:
    """Mach number a cruise speed schedule flies at the given altitude [m].

    The aircraft flies a constant CAS, the low one under FL100 and the high one
    above it, until that CAS would exceed the schedule's design Mach, and the
    design Mach from there up. Constant CAS gives a Mach number that rises with
    altitude, so this is the lower of the design Mach and the Mach number of
    the CAS. Standard atmosphere throughout, which is what PIANO tabulates.

    Raises:
        ValueError: If the schedule lacks either CAS value.
    """
    if speeds.cas_low is None or speeds.cas_high is None:
        raise ValueError('a cruise speed schedule needs both cas_low and cas_high')
    cas = speeds.cas_low if altitude < _FL100_M else speeds.cas_high
    cas_mach = float(cas_to_tas(cas, altitude) / speed_of_sound_at_altitude(altitude))
    return min(speeds.mach, cas_mach)


class PianoPerformanceModel(BasePerformanceModel[SimpleFlightRules]):
    """Performance model built from PIANO climb, cruise and descent exports."""

    model_type: Literal['piano']
    """Performance model type discriminator."""

    climb_flight_performance: PerformanceTableInput
    """Climb performance table."""

    cruise_flight_performance: PerformanceTableInput
    """Cruise performance table, swept over Mach number."""

    descent_flight_performance: PerformanceTableInput
    """Descent performance table."""

    cruise_reference_mach: TableInput | None = None
    """Reference Mach numbers PIANO labels in the cruise sweep."""

    descent_idle_thrust: TableInput | None = None
    """Altitude below which each descent block flies at idle thrust."""

    operating_empty_mass_kg: PositiveFloat
    """Operating empty mass [kg].

    PIANO exports do not contain one, and unlike BADA the sweep gives no basis
    for deriving it from the lowest tabulated mass, so it must be supplied.
    """

    @model_validator(mode='before')
    @classmethod
    def require_operating_empty_mass(cls, data: Any) -> Any:
        """Report a missing operating empty mass in the terms of the file.

        Pydantic would report the field as required, which reads as an error
        in the exporter rather than a value the user has to look up.
        """
        if isinstance(data, dict) and not any(
            key.lower() == 'operating_empty_mass_kg' for key in data
        ):
            raise ValueError(
                'PIANO exports do not contain an operating empty mass, so '
                '"operating_empty_mass_kg" must be set in the performance '
                'model file.'
            )
        return data

    _climb_interpolator: Interpolator = PrivateAttr()
    _cruise_interpolator: MachSweepInterpolator = PrivateAttr()
    _descent_interpolator: Interpolator = PrivateAttr()

    @model_validator(mode='after')
    def build_interpolators(self) -> PianoPerformanceModel:
        """Build the phase interpolators when the model loads, so a table with a
        hole in its (FL, mass) grid fails here, naming the aircraft, rather
        than partway through a run."""
        interpolators = {}
        for phase, interpolator_class in (
            ('climb', Interpolator),
            ('cruise', MachSweepInterpolator),
            ('descent', Interpolator),
        ):
            table = getattr(self, f'{phase}_flight_performance')
            df = pd.DataFrame(
                [row[: len(table.cols)] for row in table.data], columns=table.cols
            )
            try:
                interpolators[phase] = interpolator_class(df)
            except ValueError as exc:
                raise ValueError(
                    f'PIANO {phase} table for {self.aircraft_name}: {exc}'
                ) from exc
        self._climb_interpolator = interpolators['climb']
        self._cruise_interpolator = interpolators['cruise']
        self._descent_interpolator = interpolators['descent']
        return self

    @property
    def empty_mass(self) -> float:
        """Operating empty mass."""
        return self.operating_empty_mass_kg

    @property
    def maximum_mass(self) -> float:
        """Maximum aircraft mass across the three phase tables."""
        return max(
            max(self.climb_flight_performance.column('mass')),
            max(self.cruise_flight_performance.column('mass')),
            max(self.descent_flight_performance.column('mass')),
        )

    @property
    def minimum_tas(self) -> float:
        return min(
            min(self.climb_flight_performance.column('tas')),
            min(self.cruise_flight_performance.column('tas')),
            min(self.descent_flight_performance.column('tas')),
        )

    @property
    def maximum_rocd(self) -> float:
        return max(
            max(self.climb_flight_performance.column('rocd')),
            max(self.cruise_flight_performance.column('rocd')),
            max(self.descent_flight_performance.column('rocd')),
        )

    @property
    def lowest_cruise_altitude(self) -> float:
        return min(self.cruise_flight_performance.column('fl')) * FL_TO_METERS

    def evaluate_impl(
        self, state: AircraftState, rules: SimpleFlightRules
    ) -> Performance:
        """Bilinear interpolation in flight level and aircraft mass within the
        phase table selected by the flight rules.

        Cruise is also swept over Mach number, so it is evaluated at the Mach
        number the cruise speed schedule flies at the aircraft's altitude (see
        `cruise_mach_at_altitude`), which needs ``speeds.cruise``."""
        fl = state.altitude * METERS_TO_FL
        match rules:
            case SimpleFlightRules.CLIMB:
                interpolator = self._climb_interpolator
            case SimpleFlightRules.DESCEND:
                interpolator = self._descent_interpolator
            case SimpleFlightRules.CRUISE:
                interpolator = self._cruise_interpolator

        mass = state.aircraft_mass
        if mass == 'min':
            mass = interpolator.min_mass
        elif mass == 'max':
            mass = interpolator.max_mass

        if rules != SimpleFlightRules.CRUISE:
            return interpolator(fl, mass)

        if self.speeds.cruise is None:
            raise ValueError(
                f'PIANO cruise performance for {self.aircraft_name} needs '
                'speeds.cruise: the cruise export sweeps Mach number and names '
                'no operating speed, so it has to be supplied'
            )
        try:
            mach = cruise_mach_at_altitude(self.speeds.cruise, state.altitude)
            return self._cruise_interpolator(fl, mass, mach)
        except ValueError as exc:
            raise ValueError(
                f'PIANO cruise performance for {self.aircraft_name}: {exc}'
            ) from exc
