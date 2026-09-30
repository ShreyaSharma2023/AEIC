from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Protocol

import numpy as np
from pydantic import Field

from AEIC.types import SpeciesValues
from AEIC.utils.geos_levels import N_LEVELS, edge_pressures_hpa, mid_pressures_hpa
from AEIC.utils.models import CIBaseModel


def _edges_from_levels(levels: np.ndarray) -> np.ndarray:
    """Synthesize N+1 bin edges from N level centers.

    Interior edges are midpoints between adjacent levels. The outer edges are
    extended symmetrically (i.e. each outermost level sits at the center of its
    bin).
    """
    midpoints = 0.5 * (levels[:-1] + levels[1:])
    lower = 2 * levels[0] - midpoints[0]
    upper = 2 * levels[-1] - midpoints[-1]
    return np.concatenate(([lower], midpoints, [upper]))


class HorizontalGrid(CIBaseModel):
    resolution: float
    range: tuple[float, float]

    @property
    def bins(self) -> int:
        return int((self.range[1] - self.range[0]) / self.resolution)

    def index(self, value: float) -> int:
        """Get the bin index for a given value."""
        if not self.range[0] <= value <= self.range[1]:
            raise ValueError(f'Value {value} out of range {self.range}')
        return int((value - self.range[0]) / self.resolution)


class LatitudeGrid(HorizontalGrid):
    range: tuple[float, float] = Field(default=(-90.0, 90.0))


class LongitudeGrid(HorizontalGrid):
    range: tuple[float, float] = Field(default=(-180.0, 180.0))


class HeightGrid(CIBaseModel):
    mode: Literal['height']

    resolution: float
    range: tuple[float, float]

    @property
    def bins(self) -> int:
        return int((self.range[1] - self.range[0]) / self.resolution)

    @property
    def bottom(self) -> float:
        return self.range[0]

    @property
    def top(self) -> float:
        return self.range[1]

    @property
    def levels(self) -> np.ndarray:
        """Bin center values (meters)."""
        return self.range[0] + (np.arange(self.bins) + 0.5) * self.resolution

    @property
    def edges(self) -> np.ndarray:
        """N+1 bin edge values synthesized from level midpoints, with outer
        boundaries extended symmetrically."""
        return _edges_from_levels(self.levels)


class ISAPressureGrid(CIBaseModel):
    mode: Literal['isa_pressure']

    levels: list[float]

    @property
    def bins(self) -> int:
        return len(self.levels)

    @property
    def bottom(self) -> float:
        return max(self.levels)

    @property
    def top(self) -> float:
        return min(self.levels)

    @property
    def edges(self) -> np.ndarray:
        """N+1 bin edge values in ascending pressure order, synthesized from
        level midpoints with outer boundaries extended symmetrically."""
        return _edges_from_levels(np.sort(self.levels))


class GEOSChemGrid(CIBaseModel):
    """The 72 levels of GEOS-Chem and MERRA2's terrain-following hybrid
    sigma-pressure grid, at one standard surface pressure.

    The pressure at the edges of level L is Ap(L) + Bp(L) * PS, with the
    model's published Ap and Bp, so a level is at a different pressure in every
    column. A trajectory carries no local surface pressure, so the levels are
    taken at `surface_pressure` everywhere: well above the boundary layer that
    is the model's own levels, and near the ground over high terrain it is off by
    a level or more.

    Like the ISA pressure grid, flights are binned by ISA pressure, and the
    bins are in ascending pressure order.
    """

    mode: Literal['geoschem_72']

    surface_pressure: float = 1013.25
    """Surface pressure [hPa] the levels are taken at."""

    @property
    def bins(self) -> int:
        return N_LEVELS

    @property
    def bottom(self) -> float:
        return self.surface_pressure

    @property
    def top(self) -> float:
        return float(self.edges[0])

    @property
    def levels(self) -> np.ndarray:
        """Layer mid pressures [hPa], the surface layer first."""
        return mid_pressures_hpa(np.float64(self.surface_pressure))

    @property
    def edges(self) -> np.ndarray:
        """N+1 layer edge pressures [hPa] in ascending order, the model top
        first and the surface last, as the kernel bins them."""
        return np.sort(edge_pressures_hpa(np.float64(self.surface_pressure)))


AltitudeGrid = Annotated[
    HeightGrid | ISAPressureGrid | GEOSChemGrid, Field(discriminator='mode')
]

PRESSURE_GRIDS = (ISAPressureGrid, GEOSChemGrid)
"""Vertical grids that bin by pressure, in ascending order, not by height."""


class TrajectoryLike(Protocol):
    latitude: np.ndarray
    longitude: np.ndarray
    altitude: np.ndarray
    trajectory_emissions: SpeciesValues[np.ndarray]


@dataclass(slots=True)
class GridCell:
    lon: int
    lat: int
    alt: int


class Grid(CIBaseModel):
    latitude: LatitudeGrid
    longitude: LongitudeGrid
    altitude: AltitudeGrid

    @property
    def shape(self) -> tuple[int, int, int]:
        return (self.latitude.bins, self.longitude.bins, self.altitude.bins)

    @classmethod
    def load(cls, file_path: Path | str) -> Grid:
        with open(file_path, 'rb') as fp:
            d = tomllib.load(fp)
            return cls.model_validate(d)
