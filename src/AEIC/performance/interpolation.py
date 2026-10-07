"""Grid interpolation of tabulated aircraft performance data."""

# TODO: Remove this when we migrate to Python 3.14+.
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy.interpolate import interpn

from AEIC.performance.types import Performance

logger = logging.getLogger(__name__)

GRIDDED_COLUMNS = ['fl', 'mass', 'tas', 'rocd', 'fuel_flow']


def _is_dense(df: pd.DataFrame) -> bool:
    return len(df.fl.unique()) * len(df.mass.unique()) == len(df)


def _interpolate_blocks(df: pd.DataFrame, levels: np.ndarray) -> pd.DataFrame:
    """Each mass's trajectory, interpolated linearly in flight level onto
    `levels`. Above (below) a trajectory's own last (first) sample its end
    values are held, so every mass has a row at every level."""
    frames = []
    for mass, block in df.groupby('mass', sort=True):
        block = block.sort_values('fl')
        frame = pd.DataFrame({'fl': levels, 'mass': mass})
        for column in ('tas', 'rocd', 'fuel_flow'):
            frame[column] = np.interp(levels, block.fl.values, block[column].values)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)[GRIDDED_COLUMNS]


def grid_climb_trajectories(df: pd.DataFrame) -> pd.DataFrame:
    """Turn PIANO's per-mass climb trajectories into a (FL, mass) grid.

    Each mass is one trajectory, sampled at its own flight levels and ending at
    its own ceiling, so together they are not a grid. The grid is the flight
    levels of the trajectory that reaches highest (usually the lightest),
    without FL0, the start of the climb. Every trajectory is interpolated onto
    it, holding its last values above its own ceiling so the grid stays
    rectangular without cutting off the lighter masses' higher levels. Levels
    where any mass cannot climb, its held or interpolated rate of climb not
    being positive, are dropped.

    A table that is already a grid is returned as it is.
    """
    if _is_dense(df):
        return df
    reference = df.loc[df.groupby('mass').fl.transform('max') == df.fl.max()]
    levels = np.unique(reference.fl.values[reference.fl.values > 0])
    gridded = _interpolate_blocks(df, levels)
    climbs = gridded.groupby('fl').rocd.min() > 0
    return gridded[gridded.fl.isin(climbs[climbs].index)].reset_index(drop=True)


def grid_cruise_levels(df: pd.DataFrame) -> pd.DataFrame:
    """Keep the cruise flight levels that every mass has rows at.

    A heavy aircraft cannot cruise at the highest flight levels, so PIANO's
    cruise export has no rows there for it. Keeping those levels for the lighter
    masses would leave holes in the (FL, mass) grid, so they are dropped for
    every mass. A table with every level for every mass is returned as it is.
    """
    masses_at_level = df.groupby('fl').mass.nunique()
    complete = masses_at_level[masses_at_level == df.mass.nunique()].index
    if len(complete) == len(masses_at_level):
        return df
    return df[df.fl.isin(complete)].reset_index(drop=True)


def grid_descent_trajectories(df: pd.DataFrame) -> pd.DataFrame:
    """Turn PIANO's per-mass descent trajectories into a (FL, mass) grid, on the
    flight levels of the first (lightest) mass's trajectory, in the same way
    as `grid_climb_trajectories`. A table that is already a grid is returned as
    it is."""
    if _is_dense(df):
        return df
    reference = df.loc[df.mass == df.mass.min()]
    return _interpolate_blocks(df, np.unique(reference.fl.values))


class Interpolator:
    """Grid-based interpolator for performance model data."""

    def __init__(self, df: pd.DataFrame):
        # The table must have exactly one row for every (FL, mass) pair. A
        # missing cell would otherwise be left as zero in the grid below and
        # returned as if it were real performance data. A duplicated pair
        # needs its own check: it can cancel out a missing cell in the row
        # count.
        if df.duplicated(subset=['fl', 'mass']).any():
            raise ValueError('Interpolator requires unique (FL, mass) pairs in data')

        # Coordinate values.
        fls = sorted(float(fl) for fl in df.fl.unique())
        self.min_fl = min(fls)
        self.max_fl = max(fls)
        masses = sorted(float(m) for m in df.mass.unique())
        self.min_mass = min(masses)
        self.max_mass = max(masses)

        if len(fls) * len(masses) != len(df):
            raise ValueError(
                'Interpolator requires a row for every (FL, mass) pair: got '
                f'{len(df)} rows for {len(fls)} flight levels x {len(masses)} masses'
            )

        # If there is only one mass value, we need to do linear interpolation
        # in flight level. Otherwise we will be doing bilinear interpolation in
        # flight level and mass.
        self.n_masses = len(masses)
        if self.n_masses > 1:
            self.xs = (np.array(fls), np.array(masses))

            # Output values.
            shape = (len(fls), len(masses))
            self.tas = np.zeros(shape)
            self.rocd = np.zeros(shape)
            self.fuel_flow = np.zeros(shape)

            # Construct output values.
            for row in df.itertuples():
                i = fls.index(row.fl)  # type: ignore
                j = masses.index(row.mass)  # type: ignore
                self.tas[i, j] = row.tas  # type: ignore
                self.rocd[i, j] = row.rocd  # type: ignore
                self.fuel_flow[i, j] = row.fuel_flow  # type: ignore
        else:
            self.xs = (np.array(fls),)

            # Output values.
            self.tas = df.tas.values
            self.rocd = df.rocd.values
            self.fuel_flow = df.fuel_flow.values

    def __call__(self, fl: float, mass: float) -> Performance:
        """Perform bilinear interpolation to get performance values at given
        flight level and aircraft mass."""

        if self.n_masses > 1:
            x = (
                np.clip(fl, self.min_fl, self.max_fl),
                np.clip(mass, self.min_mass, self.max_mass),
            )
        else:
            x = np.array([np.clip(fl, self.min_fl, self.max_fl)])

        return Performance(
            true_airspeed=float(interpn(self.xs, self.tas, x, method='linear')[0]),
            rate_of_climb=float(interpn(self.xs, self.rocd, x, method='linear')[0]),
            fuel_flow=float(interpn(self.xs, self.fuel_flow, x, method='linear')[0]),
        )


class MachSweepInterpolator:
    """Interpolator for cruise tables tabulated on an (FL, mass) grid with a
    sweep over Mach number in every cell.

    Flight level and mass are interpolated bilinearly, and clipped at the
    table edge like `Interpolator`. Within each of the (up to four) cells a
    query touches, values are interpolated linearly in Mach between the
    neighbouring tabulated Mach numbers. The Mach axis may differ from cell to
    cell, since an aircraft cannot sustain every speed at every flight level
    and mass and those rows are simply absent.

    A Mach a little outside the range tabulated for a cell the query needs
    takes the nearest tabulated Mach, and is logged once per cell. A heavy
    aircraft at a low flight level cannot sustain speeds below the lowest
    tabulated, and a flight's speed schedule can ask for a hair less. A Mach
    further out than `max_mach_gap` is an error, not clipped: the nearest
    tabulated Mach is then a different speed, so using it would return a
    plausible but wrong value.
    """

    MACH_TOL = 1e-9
    """Tolerance on the edge of a cell's tabulated Mach range."""

    def __init__(self, df: pd.DataFrame, max_mach_gap: float = 0.02):
        self.max_mach_gap = max_mach_gap
        """Largest distance [Mach] outside a cell's tabulated range, itself
        included, that the nearest tabulated Mach is still used for. 0.02 is
        about 6 m/s at cruise altitude."""
        self._warned: set[tuple[float, float]] = set()
        if df.duplicated(subset=['fl', 'mass', 'mach']).any():
            raise ValueError(
                'Cruise table repeats Mach numbers within an FL, mass cell'
            )

        fls = sorted(float(fl) for fl in df.fl.unique())
        masses = sorted(float(m) for m in df.mass.unique())
        n_cells = df.groupby(['fl', 'mass']).ngroups
        if n_cells != len(fls) * len(masses):
            raise ValueError(
                'Interpolator requires a Mach sweep for every (FL, mass) pair: got '
                f'{n_cells} cells for {len(fls)} flight levels x {len(masses)} masses'
            )

        self._fls = np.array(fls)
        self._masses = np.array(masses)
        self.min_mass = masses[0]
        self.max_mass = masses[-1]

        # Per cell: the tabulated Mach numbers, ascending, and the value
        # columns (airspeed, rate of climb, fuel flow) at each of them.
        self._cells: dict[tuple[float, float], tuple[np.ndarray, np.ndarray]] = {}
        for (fl, mass), cell in df.groupby(['fl', 'mass']):
            cell = cell.sort_values('mach')
            self._cells[(float(fl), float(mass))] = (
                cell.mach.to_numpy(dtype=float),
                cell[['tas', 'rocd', 'fuel_flow']].to_numpy(dtype=float),
            )

    @staticmethod
    def _bracket(axis: np.ndarray, x: float) -> tuple[int, int, float]:
        """Indices of the two grid points around `x` (clipped to the axis) and
        the weight of the upper one."""
        x = min(max(x, axis[0]), axis[-1])
        if len(axis) == 1 or x >= axis[-1]:
            last = len(axis) - 1
            return last, last, 0.0
        lower = int(np.searchsorted(axis, x, side='right')) - 1
        weight = (x - axis[lower]) / (axis[lower + 1] - axis[lower])
        return lower, lower + 1, float(weight)

    def _at_mach(self, i: int, j: int, mach: float) -> np.ndarray:
        fl, mass = float(self._fls[i]), float(self._masses[j])
        machs, values = self._cells[(fl, mass)]
        if mach < machs[0] - self.MACH_TOL or mach > machs[-1] + self.MACH_TOL:
            nearest = float(machs[0] if mach < machs[0] else machs[-1])
            if abs(mach - nearest) > self.max_mach_gap + self.MACH_TOL:
                raise ValueError(
                    f'no cruise data at Mach {mach:.3f} for FL {fl:g} and mass '
                    f'{mass:g} kg: tabulated range is {machs[0]:.3f}-{machs[-1]:.3f}'
                )
            if (fl, mass) not in self._warned:
                self._warned.add((fl, mass))
                logger.warning(
                    f'no cruise data at Mach {mach:.3f} for FL {fl:g} and mass '
                    f'{mass:g} kg: using the nearest tabulated Mach {nearest:.3f} '
                    f'(tabulated range {machs[0]:.3f}-{machs[-1]:.3f})'
                )
        # np.interp holds the end values beyond the tabulated range, which is
        # the nearest tabulated Mach.
        return np.array([np.interp(mach, machs, values[:, k]) for k in range(3)])

    def __call__(self, fl: float, mass: float, mach: float) -> Performance:
        """Performance at the given flight level, mass and Mach number."""
        i0, i1, w_fl = self._bracket(self._fls, fl)
        j0, j1, w_mass = self._bracket(self._masses, mass)

        total = np.zeros(3)
        for i, w_i in ((i0, 1.0 - w_fl), (i1, w_fl)):
            for j, w_j in ((j0, 1.0 - w_mass), (j1, w_mass)):
                # A cell with no weight is not consulted, so it cannot make a
                # query fail for want of a Mach it does not contribute to.
                if w_i * w_j > 0.0:
                    total += w_i * w_j * self._at_mach(i, j, mach)

        return Performance(
            true_airspeed=float(total[0]),
            rate_of_climb=float(total[1]),
            fuel_flow=float(total[2]),
        )
