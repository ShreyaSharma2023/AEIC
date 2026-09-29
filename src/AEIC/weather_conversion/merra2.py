"""Convert MERRA2 winds to the fixed pressure levels the `Weather` reader uses.

MERRA2's wind files are on terrain-following hybrid levels: a level index is at
a different pressure in every column, and there is no level below the ground.
`Weather` needs a fixed list of pressure levels shared by every column, with a
wind at every one, so the winds are interpolated onto such a list column by
column, and levels that lie inside the ground get zero wind.
"""

# TODO: Remove this when we move to Python 3.14+.
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import xarray as xr

from AEIC.utils.geos_levels import N_LEVELS, mid_pressures_hpa

PRESSURE_LEVELS_HPA = np.array(
    [1050, 1025, 1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600]
    + [550, 500, 450, 400, 350, 300, 250, 225, 200, 175, 150, 125, 100, 70],
    dtype=float,
)
"""Output levels [hPa]. They run from below sea level (1050, 1025: the ISA
surface is 1013.25 hPa, and `Weather` rejects a point outside its levels) to
above any aircraft ceiling, closest together in the lower troposphere."""


def interp_to_pressure_levels(
    values: np.ndarray,
    mid_hpa: np.ndarray,
    surface_hpa: np.ndarray,
    targets_hpa: np.ndarray,
) -> np.ndarray:
    """Interpolate hybrid-level winds onto fixed pressure levels.

    Args:
        values: Winds on the hybrid levels, levels first: (level, ...).
        mid_hpa: Pressure at the middle of each level [hPa], same shape.
        surface_hpa: Surface pressure of each column [hPa], shape (...).
        targets_hpa: Pressure levels to interpolate onto [hPa], in any order.

    Returns:
        Winds at the target levels, (target, ...), in the order given.

    Interpolation is linear in ln(pressure) between the two model levels either
    side of the target. A target above the ground but outside the middles of the
    lowest or highest model level takes that level's wind. A target below the
    ground, at a higher pressure than the column's surface, gets zero wind:
    it is inside a mountain, and NaN is not accepted by the `Weather` reader.
    """
    n_levels = values.shape[0]
    log_mid = np.log(mid_hpa)
    out = np.empty((len(targets_hpa),) + surface_hpa.shape)

    for k, target in enumerate(targets_hpa):
        # mid_hpa falls with the level index, so this counts the levels at a
        # higher pressure than the target: the target is above all of them.
        n_below = (mid_hpa > target).sum(axis=0)
        lower = np.clip(n_below - 1, 0, n_levels - 2)
        upper = lower + 1

        def at(array, index):
            return np.take_along_axis(array, index[np.newaxis], axis=0)[0]

        log_lower, log_upper = at(log_mid, lower), at(log_mid, upper)
        weight = np.clip((log_lower - np.log(target)) / (log_lower - log_upper), 0, 1)
        wind = at(values, lower) + weight * (at(values, upper) - at(values, lower))
        out[k] = np.where(target > surface_hpa, 0.0, wind)

    return out


def linear_in_time(
    values: np.ndarray, times: np.ndarray, targets: np.ndarray
) -> np.ndarray:
    """Interpolate linearly in time along the first axis of `values`.

    Raises:
        ValueError: If a target is before the first or after the last time.
            Extrapolating would hold or invent winds at the edges of a day.
    """
    seconds = times.astype('datetime64[s]').astype(float)
    wanted = targets.astype('datetime64[s]').astype(float)
    if wanted.min() < seconds[0] or wanted.max() > seconds[-1]:
        raise ValueError(
            f'target times {targets.min()}..{targets.max()} are outside the '
            f'data\'s {times[0]}..{times[-1]}'
        )
    lower = np.clip(
        np.searchsorted(seconds, wanted, side='right') - 1, 0, len(seconds) - 2
    )
    weight = (wanted - seconds[lower]) / (seconds[lower + 1] - seconds[lower])
    weight = weight.reshape((-1,) + (1,) * (values.ndim - 1))
    return values[lower] * (1 - weight) + values[lower + 1] * weight


def merra2_path(source_dir: Path, day: date, stream: str) -> Path:
    """Where a day's MERRA2 file for `stream` (``A3dyn`` or ``I3``) lives in the
    GEOS-Chem ``GEOS_0.5x0.625/MERRA2`` tree."""
    return (
        Path(source_dir)
        / f'{day:%Y}'
        / f'{day:%m}'
        / f'MERRA2.{day:%Y%m%d}.{stream}.05x0625.nc4'
    )


def _open(path: Path) -> xr.Dataset:
    if not path.exists():
        raise FileNotFoundError(f'MERRA2 file not found: {path}')
    return xr.open_dataset(path)


def _surface_pressure_timeline(source_dir: Path, source_day: date):
    """Surface pressure [hPa] at every 3-hourly I3 time of the day before, the
    day and the day after, as (times, (time, lat, lon) values)."""
    times, fields = [], []
    for offset in (-1, 0, 1):
        with _open(merra2_path(source_dir, source_day + timedelta(offset), 'I3')) as ds:
            times.append(ds['time'].values)
            fields.append(ds['PS'].values / 100.0)  # Pa -> hPa
    return np.concatenate(times), np.concatenate(fields)


def convert_day(
    day: date,
    source_dir: str | Path,
    *,
    source_year: int | None = None,
    pressure_levels_hpa: np.ndarray = PRESSURE_LEVELS_HPA,
) -> xr.Dataset:
    """Winds for every hour of `day`, on fixed pressure levels, in the layout
    `Weather` reads from a daily file with hourly data.

    MERRA2's A3dyn winds are 3-hour means centred on 01:30, 04:30, ... 22:30, so
    the first and last hours of the day are interpolated between the previous
    day's 22:30 and this day's 01:30, and this day's 22:30 and the next day's
    01:30, which need those neighbouring days' files. Surface pressure, needed
    to place the hybrid levels, comes from the instantaneous I3 files and is
    interpolated to each wind time.

    Args:
        day: The day to convert.
        source_dir: Root of the ``GEOS_0.5x0.625/MERRA2`` tree.
        source_year: Read the files of this year instead of the day's own,
            labelling the result with the day's. Stands in for a year whose
            files are not available (2025 has none after October).
        pressure_levels_hpa: Levels to interpolate onto.
    """
    source_dir = Path(source_dir)
    source_day = day.replace(year=source_year) if source_year else day
    hours = np.datetime64(source_day.isoformat()) + np.arange(24) * np.timedelta64(
        1, 'h'
    )

    ps_times, ps_fields = _surface_pressure_timeline(source_dir, source_day)

    # The wind records that bracket the day: the previous day's last, this
    # day's eight, the next day's first.
    records = [(-1, [7]), (0, list(range(8))), (1, [0])]
    times, converted = [], []
    lat = lon = None
    for offset, indices in records:
        path = merra2_path(source_dir, source_day + timedelta(offset), 'A3dyn')
        with _open(path) as ds:
            lat, lon = ds['lat'].values, ds['lon'].values
            for i in indices:
                u, v = ds['U'].isel(time=i).values, ds['V'].isel(time=i).values
                if np.isnan(u).any() or np.isnan(v).any():
                    raise ValueError(f'{path}: NaN winds in record {i}')
                time = ds['time'].values[i]
                surface = linear_in_time(ps_fields, ps_times, np.array([time]))[0]
                mids = mid_pressures_hpa(surface)
                if u.shape[0] != N_LEVELS:
                    raise ValueError(f'{path}: expected {N_LEVELS} levels')
                converted.append(
                    np.stack(
                        [
                            interp_to_pressure_levels(
                                field, mids, surface, pressure_levels_hpa
                            )
                            for field in (u, v)
                        ]
                    )
                )
                times.append(time)

    hourly = linear_in_time(np.stack(converted), np.array(times), hours)
    # MERRA2 longitudes run -180..179.375; Weather wants 0..360, ascending. A
    # longitude left in -180..180 would silently return the wrong hemisphere's
    # wind for every western-hemisphere point.
    east = lon % 360.0
    order = np.argsort(east)
    shift = np.timedelta64(int((day - source_day).days), 'D')
    dims = ('valid_time', 'pressure_level', 'latitude', 'longitude')
    return xr.Dataset(
        {
            name: (dims, hourly[:, k][..., order].astype(np.float32))
            for k, name in enumerate(('u', 'v'))
        },
        coords={
            'valid_time': hours + shift,
            'pressure_level': ('pressure_level', pressure_levels_hpa, {'units': 'hPa'}),
            'latitude': ('latitude', lat, {'units': 'degrees_north'}),
            'longitude': ('longitude', east[order], {'units': 'degrees_east'}),
        },
        attrs={'source': 'MERRA2 A3dyn and I3, converted by AEIC'},
    )


def write_day(dataset: xr.Dataset, path: Path) -> None:
    """Write a converted day, one hour per chunk so that reading an hour reads
    only that hour, through a temporary file so an interrupted job leaves no
    half-written day behind."""
    n = dataset.sizes
    chunks = (1, n['pressure_level'], n['latitude'], n['longitude'])
    encoding = {
        name: {
            'dtype': 'float32',
            'chunksizes': chunks,
            'zlib': True,
            'complevel': 1,
            'shuffle': True,
        }
        for name in ('u', 'v')
    }
    temporary = path.with_suffix('.nc.part')
    dataset.to_netcdf(temporary, encoding=encoding)
    temporary.replace(path)
