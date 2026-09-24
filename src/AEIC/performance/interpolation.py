"""Grid interpolation of tabulated aircraft performance data."""

# TODO: Remove this when we migrate to Python 3.14+.
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.interpolate import interpn

from AEIC.performance.types import Performance


class Interpolator:
    """Grid-based interpolator for performance model data."""

    def __init__(self, df: pd.DataFrame):
        # Requirements:
        #  - Regular FL, regular mass ⇒ rectlinear grid;
        #  - Dense: unique (FL, mass); #rows = #FL × #mass
        #
        # These conditions should be checked in the PerformanceTable
        # constructor, but we check them here for security and testing
        # purposes.
        if len(list(zip(df.fl.values, df.mass.values))) != len(df):
            raise ValueError('Interpolator requires unique (FL, mass) pairs in data')

        # Coordinate values.
        fls = sorted(float(fl) for fl in df.fl.unique())
        self.min_fl = min(fls)
        self.max_fl = max(fls)
        masses = sorted(float(m) for m in df.mass.unique())
        self.min_mass = min(masses)
        self.max_mass = max(masses)

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
