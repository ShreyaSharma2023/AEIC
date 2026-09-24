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

from AEIC.performance.interpolation import Interpolator
from AEIC.performance.types import (
    AircraftState,
    Performance,
    SimpleFlightRules,
    TableInput,
)
from AEIC.units import FL_TO_METERS, METERS_TO_FL

from .base import BasePerformanceModel, PerformanceTableInput


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
    _descent_interpolator: Interpolator = PrivateAttr()

    @model_validator(mode='after')
    def build_interpolators(self) -> PianoPerformanceModel:
        """Build the climb and descent interpolators when the model loads, so
        a table with a hole in its (FL, mass) grid fails here, naming the
        aircraft, rather than partway through a run."""
        interpolators = {}
        for phase in ('climb', 'descent'):
            table = getattr(self, f'{phase}_flight_performance')
            df = pd.DataFrame(
                [row[: len(table.cols)] for row in table.data], columns=table.cols
            )
            try:
                interpolators[phase] = Interpolator(df)
            except ValueError as exc:
                raise ValueError(
                    f'PIANO {phase} table for {self.aircraft_name}: {exc}'
                ) from exc
        self._climb_interpolator = interpolators['climb']
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
        phase table selected by the flight rules."""
        fl = state.altitude * METERS_TO_FL
        match rules:
            case SimpleFlightRules.CLIMB:
                interpolator = self._climb_interpolator
            case SimpleFlightRules.DESCEND:
                interpolator = self._descent_interpolator
            case SimpleFlightRules.CRUISE:
                raise NotImplementedError(
                    'PIANO cruise performance evaluation not yet implemented.'
                )

        mass = state.aircraft_mass
        if mass == 'min':
            mass = interpolator.min_mass
        elif mass == 'max':
            mass = interpolator.max_mass
        return interpolator(fl, mass)
