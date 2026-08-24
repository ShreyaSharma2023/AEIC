"""This module implements a performance model for PIANO-derived performance
data.

The table shape is the same as the legacy (BADA-derived) performance model
(see :mod:`AEIC.performance.models.legacy`): performance data is split into
climb, cruise and descent sections, and evaluation is done by bilinear
interpolation in flight level and aircraft mass. This is a separate model
type rather than reusing the legacy model because PIANO can offer richer
data than BADA PTF files provide:

- BADA PTF files always report exactly 2 or 3 masses (low/nominal/high) for
  climb/cruise and exactly 1 (nominal) for descent; PIANO can report more.
- BADA PTF files only ever report a single nominal-mass fuel_flow/ROCD value
  for climb/descent; PIANO can report genuine per-mass variation (a heavier
  aircraft burns more fuel climbing to the same altitude).

The legacy model's validation is intentionally strict about both of these,
matching what BADA PTF data actually looks like. This model relaxes both,
since a PIANO-derived table can legitimately have more resolution."""

# TODO: Remove this when we migrate to Python 3.14+.
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Literal, Self

import pandas as pd
from pydantic import PrivateAttr, model_validator

from AEIC.performance.models.legacy import (
    Interpolator,
    PerformanceTableInput,
    ROCDFilter,
)
from AEIC.performance.types import AircraftState, Performance, SimpleFlightRules
from AEIC.units import FL_TO_METERS, METERS_TO_FL

from .base import BasePerformanceModel

STEP_CLIMB_EXTRA_COLS = ('drag', 'sfc', 'mcl_avail', 'rocd_mcl_fixmach')
"""Extra cruise-table columns used for step-climb modelling (see
FlightPerformanceByPhase's docstring in AEIC.parsers.piano_reader). Present
only on the cruise table, and only for tables built from real PIANO data new
enough to carry them -- absent on older-format cruise tables."""


@dataclass
class StepClimbPerformance:
    """Cruise-table data needed to model a mid-cruise step climb at 100% Max
    Climb thrust: drag/SFC describe the level-cruise baseline being climbed
    away from, mcl_avail/rocd_mcl_fixmach describe the climb itself."""

    drag_n: float
    """Total aircraft drag [N] in level cruise at this (altitude, mass)."""

    sfc_si: float
    """Thrust-specific fuel consumption [kg/(N*s)] at this (altitude, mass)."""

    mcl_avail_n: float
    """Available Maximum Climb thrust, all engines [N], at this altitude."""

    rocd_mcl_fixmach_ms: float
    """PIANO's own computed ROCD [m/s] at 100% Max Climb thrust, holding
    Mach constant -- the rate a same-Mach mid-cruise step climb actually
    achieves."""


@dataclass
class PianoPerformanceTable:
    """Aircraft performance data table for a PIANO-derived performance
    model.

    Structurally identical to :class:`AEIC.performance.models.legacy.
    PerformanceTable` (climb/cruise/descent split, bilinear interpolation in
    FL and mass via the shared ``Interpolator``), but without the mass-count
    and FL-only-fuel_flow/ROCD restrictions that only hold for BADA PTF
    data. TAS is still required to be FL-only in every phase: it comes from
    a CAS/Mach speed schedule that doesn't depend on aircraft mass,
    regardless of data source.
    """

    df: pd.DataFrame
    """Performance table data."""

    fl: list[float]
    """Sorted list of unique flight levels in the table."""

    tas: list[float]
    """Sorted list of unique airspeed values in the table."""

    rocd: list[float]
    """Sorted list of unique ROCD values in the table."""

    mass: list[float]
    """Sorted list of unique mass values in the table."""

    rocd_filter: ROCDFilter
    """ROCD filter for this performance table segment."""

    _interpolator: Interpolator | None = field(default=None, repr=False, compare=False)
    """Interpolator for single flight phase table segment."""

    ZERO_ROCD_TOL: ClassVar[float] = 1.0e-6
    """Tolerance for zero rate of climb/descent comparisons."""

    def __post_init__(self):
        match self.rocd_filter:
            case ROCDFilter.NEGATIVE:
                phase = 'descent'
                if not all(v < -self.ZERO_ROCD_TOL for v in self.rocd):
                    raise ValueError(
                        'ROCD values in descent performance table are not all negative'
                    )
            case ROCDFilter.ZERO:
                phase = 'cruise'
                if not all(abs(v) <= self.ZERO_ROCD_TOL for v in self.rocd):
                    raise ValueError(
                        'ROCD values in cruise performance table are not all zero'
                    )
            case ROCDFilter.POSITIVE:
                phase = 'climb'
                # Condition is different here because climb ROCD values can be
                # zero near the operating ceiling of an aircraft.
                if not all(v >= 0.0 for v in self.rocd):
                    raise ValueError(
                        'some ROCD values in climb performance table are negative'
                    )

        # At least one mass for descent (bilinear interpolation degrades to
        # FL-only there), at least two for climb/cruise. No upper bound --
        # unlike the legacy (BADA) model, a PIANO-derived table isn't capped
        # at 2-3 masses.
        min_masses = 1 if self.rocd_filter == ROCDFilter.NEGATIVE else 2
        if len(self.mass) < min_masses:
            raise ValueError(
                f'Piano performance table ({phase}) has too few mass values '
                f'({len(self.mass)}, need at least {min_masses})'
            )

        # For each of positive, zero, and negative ROCD, it should be the case
        # that the input data is dense in (FL, mass) values, in the sense that
        # #rows = #FL × #mass.
        if len(self.df.fl.unique()) * len(self.df.mass.unique()) != len(self.df):
            raise ValueError(
                f'Performance data for {phase} does not have full coverage'
            )

        def check_fl_only(var: str):
            if len(self.df.drop_duplicates(subset=['fl', var])) != len(
                self.df.fl.unique()
            ):
                raise ValueError(
                    f'{var} for {phase} phase depends on variables other than FL'
                )

        # Only TAS is required to be FL-only. fuel_flow (climb, descent) and
        # ROCD (descent) may vary by mass, since PIANO can report genuine
        # per-mass variation there.
        check_fl_only('tas')

    @classmethod
    def from_input(cls, ptin: PerformanceTableInput, rocd_type: ROCDFilter) -> Self:
        """Convert performance table data from input format.

        This class holds performance table data in the form needed for
        trajectory and emissions calculations. The constructor converts from
        the input format from the performance model TOML file."""

        # Convert to Pandas DataFrame for easier handling.
        df = pd.DataFrame(
            [row[: len(ptin.cols)] for row in ptin.data], columns=ptin.cols
        )

        # Extract column unique values for searching.
        fl = sorted(df.fl.unique().tolist())
        tas = sorted(df.tas.unique().tolist())
        rocd = sorted(df.rocd.unique().tolist())
        mass = sorted(df.mass.unique().tolist())

        return cls(df=df, fl=fl, tas=tas, rocd=rocd, mass=mass, rocd_filter=rocd_type)

    def __len__(self) -> int:
        return len(self.df)

    @property
    def has_step_climb_data(self) -> bool:
        """True if this table's cruise data carries the extra step-climb
        columns (drag/sfc/mcl_avail/rocd_mcl_fixmach) -- only ever true for
        the cruise table, and only for TOMLs generated after that data
        started being kept (see FlightPerformanceByPhase's docstring in
        AEIC.parsers.piano_reader)."""
        return all(col in self.df.columns for col in STEP_CLIMB_EXTRA_COLS)

    def _get_interpolator(self) -> Interpolator:
        # Lazily create interpolator for flight phase segment.
        if self._interpolator is None:
            extra_cols = (
                list(STEP_CLIMB_EXTRA_COLS) if self.has_step_climb_data else None
            )
            self._interpolator = Interpolator(self.df, extra_cols=extra_cols)
        return self._interpolator

    def interpolate(self, state: AircraftState) -> Performance:
        """Perform bilinear interpolation in flight level and aircraft mass."""

        fl = state.altitude * METERS_TO_FL
        mass = state.aircraft_mass
        if mass == 'min':
            mass = min(self.mass)
        elif mass == 'max':
            mass = max(self.mass)

        self._interpolator = self._get_interpolator()

        return self._interpolator(fl, mass)

    def interpolate_step_climb(
        self, state: AircraftState
    ) -> StepClimbPerformance | None:
        """Bilinear-interpolate the step-climb fields (drag/sfc/mcl_avail/
        rocd_mcl_fixmach) at the given flight level and aircraft mass. None
        if this table doesn't carry them (see ``has_step_climb_data``)."""
        if not self.has_step_climb_data:
            return None

        fl = state.altitude * METERS_TO_FL
        mass = state.aircraft_mass
        if mass == 'min':
            mass = min(self.mass)
        elif mass == 'max':
            mass = max(self.mass)

        self._interpolator = self._get_interpolator()
        extra = self._interpolator.interpolate_extra(fl, mass)
        return StepClimbPerformance(
            drag_n=extra['drag'],
            sfc_si=extra['sfc'],
            mcl_avail_n=extra['mcl_avail'],
            rocd_mcl_fixmach_ms=extra['rocd_mcl_fixmach'],
        )


class PianoPerformanceModel(BasePerformanceModel[SimpleFlightRules]):
    """PIANO-derived table-based performance model."""

    model_type: Literal['piano']
    """Model type identifier for TOML input files."""

    climb_flight_performance: PerformanceTableInput
    """Input data for flight performance table in climb phase."""

    cruise_flight_performance: PerformanceTableInput
    """Input data for flight performance table in cruise phase."""

    descent_flight_performance: PerformanceTableInput
    """Input data for flight performance table in descent phase."""

    _climb_performance_table: PianoPerformanceTable = PrivateAttr()
    _cruise_performance_table: PianoPerformanceTable = PrivateAttr()
    _descent_performance_table: PianoPerformanceTable = PrivateAttr()

    @model_validator(mode='after')
    def validate_pm(self, info):
        """Validate performance model after creation."""

        self._climb_performance_table = PianoPerformanceTable.from_input(
            self.climb_flight_performance, ROCDFilter.POSITIVE
        )
        self._cruise_performance_table = PianoPerformanceTable.from_input(
            self.cruise_flight_performance, ROCDFilter.ZERO
        )
        self._descent_performance_table = PianoPerformanceTable.from_input(
            self.descent_flight_performance, ROCDFilter.NEGATIVE
        )

        return self

    @property
    def empty_mass(self) -> float:
        """Empty aircraft mass.

        Empty mass per BADA-3 is lowest mass in performance table / 1.2."""
        return (
            min(
                min(self._climb_performance_table.mass),
                min(self._cruise_performance_table.mass),
                min(self._descent_performance_table.mass),
            )
            / 1.2
        )

    @property
    def maximum_mass(self) -> float:
        """Maximum aircraft mass from performance table."""
        return max(
            max(self._climb_performance_table.mass),
            max(self._cruise_performance_table.mass),
            max(self._descent_performance_table.mass),
        )

    @property
    def minimum_tas(self) -> float:
        return min(
            min(self._climb_performance_table.tas),
            min(self._cruise_performance_table.tas),
            min(self._descent_performance_table.tas),
        )

    @property
    def maximum_rocd(self) -> float:
        return max(
            max(self._climb_performance_table.rocd),
            max(self._cruise_performance_table.rocd),
            max(self._descent_performance_table.rocd),
        )

    @property
    def lowest_cruise_altitude(self) -> float:
        """Lowest altitude [m] at which this model has cruise performance
        data."""
        return min(self._cruise_performance_table.fl) * FL_TO_METERS

    def evaluate_impl(
        self, state: AircraftState, rules: SimpleFlightRules
    ) -> Performance:
        """Implementation of performance evaluation for the PIANO-derived
        performance model.

        The performance table is separated into climb, cruise and descent
        segments. The performance evaluation implementation uses bilinear
        interpolation in flight level and aircraft mass in the relevant
        segment of the performance table (selected by the flight rule) to
        get performance values."""

        match rules:
            case SimpleFlightRules.CLIMB:
                return self._climb_performance_table.interpolate(state)
            case SimpleFlightRules.CRUISE:
                return self._cruise_performance_table.interpolate(state)
            case SimpleFlightRules.DESCEND:
                return self._descent_performance_table.interpolate(state)

    def step_climb_performance(
        self, state: AircraftState
    ) -> StepClimbPerformance | None:
        """Drag/SFC/available-climb-thrust/step-climb-ROCD at the given
        altitude and mass, for modelling a mid-cruise step climb at 100% Max
        Climb thrust. None if this model's cruise table predates these
        fields (see PianoPerformanceTable.has_step_climb_data). Only
        ``state.altitude`` and ``state.aircraft_mass`` are used; Mach is not
        a parameter here because the cruise table already carries whichever
        Mach the aircraft actually flies at each altitude (design Mach above
        the CAS/Mach crossover, CAS-equivalent Mach below it -- see
        ``speeds.cruise.crossover_altitude_m``)."""
        return self._cruise_performance_table.interpolate_step_climb(state)
