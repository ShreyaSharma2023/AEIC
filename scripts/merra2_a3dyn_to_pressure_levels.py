"""Convert MERRA2's native model-level A3dyn wind (U, V on hybrid-sigma
levels) into a fixed-pressure-level NetCDF that AEIC.weather.Weather can
consume directly -- the same schema ERA5/MERRA2-Np already use
(pressure_level, latitude, longitude, valid_time).

Why this is needed: A3dyn's `lev` (1-72) is a model/hybrid level index, not
pressure -- the real pressure at a given level varies by column via
P = a_k + b_k * PS (see wind-pressure-vs-model-levels-explain.py for the
conceptual picture). Unlike the pre-converted Np products (where NASA/ECMWF
already did this at the source, masking or extrapolating near terrain), we
have to do it ourselves for A3dyn, using MERRA2's own real per-column,
per-time surface pressure (`PS`, from the I3 stream) -- not a constant
reference pressure. Because we use the REAL local PS, this conversion should
mostly avoid the terrain-masking gaps in the Np product: a column's hybrid
levels are always valid down to its own true PS, so interpolating onto a
target pressure only fails where that pressure is genuinely below the local
surface -- a real physical boundary, not a data-masking artifact.

Level ordering verified empirically against wind speed / RH profiles: lev=1
is near-surface (moist, slow), lev=72 is TOA (dry, fast) -- matches the
hybrid JSON's natural (surface-first) ordering with no reversal needed.
"""

import json

import numpy as np
import xarray as xr
from scipy.ndimage import distance_transform_edt

A3DYN_FILE = (
    '/net/d16/data/shreya22/Paper_1/weather_test_merra2/'
    'MERRA2.20221121.A3dyn.05x0625.nc4'
)
I3_FILE = (
    '/net/d16/data/shreya22/Paper_1/weather_test_merra2/MERRA2.20221121.I3.05x0625.nc4'
)
HYBRID_JSON = (
    '/home/shreya22/committee_meeting_1/aeic3_work/gridding/gchp/geos_hybrid_72l.json'
)
OUT_FILE = (
    '/net/d16/data/shreya22/Paper_1/weather_test_merra2/'
    'MERRA2_A3dyn_converted_pressure_levels_20221121.nc'
)

# Same target pressure levels ERA5's product here uses, for direct
# comparability with the ERA5/MERRA2-Np work already done.
TARGET_LEVELS_HPA = np.array(
    [
        100,
        125,
        150,
        175,
        200,
        225,
        250,
        300,
        350,
        400,
        450,
        500,
        550,
        600,
        650,
        700,
        750,
        800,
        850,
        900,
        950,
        1000,
    ],
    dtype=float,
)


def load_hybrid_coefficients(path):
    with open(path) as f:
        d = json.load(f)
    hyai = np.asarray(d['hyai'], dtype=float)  # hPa, 73 edges, surface-first
    hybi = np.asarray(d['hybi'], dtype=float)
    return hyai, hybi


def interp_column_batch(target_p, p_mid, values):
    """Vectorized per-column linear interpolation.

    target_p : scalar, hPa
    p_mid : (nlev, lat, lon), monotonically decreasing along axis 0
    values : (nlev, lat, lon)

    Returns (lat, lon) array, NaN where target_p is out of the column's
    range (i.e. genuinely above the local surface pressure, or above the
    model top).
    """
    above = p_mid >= target_p  # True near-surface levels (higher pressure)
    count = above.sum(axis=0)  # number of levels at or above target pressure
    nlev = p_mid.shape[0]

    idx_hi = np.clip(
        count - 1, 0, nlev - 1
    )  # level just at/above target (higher pressure)
    idx_lo = np.clip(count, 0, nlev - 1)  # level just below target (lower pressure)

    p_hi = np.take_along_axis(p_mid, idx_hi[None, :, :], axis=0)[0]
    p_lo = np.take_along_axis(p_mid, idx_lo[None, :, :], axis=0)[0]
    v_hi = np.take_along_axis(values, idx_hi[None, :, :], axis=0)[0]
    v_lo = np.take_along_axis(values, idx_lo[None, :, :], axis=0)[0]

    with np.errstate(invalid='ignore', divide='ignore'):
        weight = (p_hi - target_p) / (p_hi - p_lo)
    result = v_hi + weight * (v_lo - v_hi)
    # v_hi == v_lo (idx_hi == idx_lo) happens at the exact boundaries; handle
    # that as an exact match instead of 0/0.
    result = np.where(idx_hi == idx_lo, v_hi, result)

    out_of_range = (count == 0) | (count == nlev)
    result = np.where(out_of_range, np.nan, result)
    return result


def fill_horizontal_nans(field_2d):
    """Nearest-valid-neighbor fill for a single (lat, lon) slice.

    Needed because even after the per-column hybrid->pressure conversion,
    isolated NaN cells remain near terrain edges (a target pressure that's
    valid at the nearest grid point can still be NaN a few cells away, over
    slightly higher terrain). Without this, xarray's multi-linear `.interp()`
    picks up those NaN cells as stencil corners and contaminates otherwise-
    valid nearby query points -- confirmed empirically: a query point with a
    fully valid nearest grid cell still failed because a neighboring corner
    in the interpolation stencil (one pressure level up) was NaN. This fill
    only helps queries near the edge of a masked region; deep within a large
    masked region (e.g. the Tibetan Plateau's interior at low levels) it
    still ultimately returns NaN once nearest-valid is arbitrarily far away
    -- but that's the physically correct outcome there.
    """
    mask = np.isnan(field_2d)
    if not mask.any() or mask.all():
        return field_2d
    idx = distance_transform_edt(mask, return_distances=False, return_indices=True)
    return field_2d[tuple(idx)]


def main():
    hyai, hybi = load_hybrid_coefficients(HYBRID_JSON)

    a3dyn = xr.open_dataset(A3DYN_FILE)
    i3 = xr.open_dataset(I3_FILE)

    lat = a3dyn['lat'].values
    lon = a3dyn['lon'].values
    times = a3dyn['time'].values  # bin-centered, e.g. 01:30, 04:30, ...

    out_u = np.full(
        (len(times), len(TARGET_LEVELS_HPA), len(lat), len(lon)),
        np.nan,
        dtype=np.float32,
    )
    out_v = np.full_like(out_u, np.nan)

    for ti, t in enumerate(times):
        print(f'Processing time {ti + 1}/{len(times)}: {t}')

        # Nearest I3 instantaneous PS to this A3dyn time-averaged bin center.
        ps_pa = i3['PS'].sel(time=t, method='nearest').values  # (lat, lon), Pa
        ps_hpa = ps_pa / 100.0

        u = a3dyn['U'].sel(time=t, method='nearest').values  # (lev, lat, lon)
        v = a3dyn['V'].sel(time=t, method='nearest').values

        # PEDGE[k] = hyai[k] + hybi[k] * PS, k=0..72 (73 edges, surface-first).
        pedge = hyai[:, None, None] + hybi[:, None, None] * ps_hpa[None, :, :]
        p_mid = 0.5 * (
            pedge[:-1] + pedge[1:]
        )  # (72, lat, lon), matches A3dyn's lev=1..72

        for li, target_p in enumerate(TARGET_LEVELS_HPA):
            out_u[ti, li] = interp_column_batch(target_p, p_mid, u)
            out_v[ti, li] = interp_column_batch(target_p, p_mid, v)

    valid_frac = 1 - np.isnan(out_u).mean()
    print(
        f'Fraction of (time, level, lat, lon) points with valid data '
        f'before fill: {valid_frac:.4f}'
    )

    # Horizontal nearest-valid-neighbor fill, per (time, level) slice -- see
    # fill_horizontal_nans docstring for why this is needed even after the
    # per-column hybrid conversion.
    for ti in range(out_u.shape[0]):
        for li in range(out_u.shape[1]):
            out_u[ti, li] = fill_horizontal_nans(out_u[ti, li])
            out_v[ti, li] = fill_horizontal_nans(out_v[ti, li])

    valid_frac_after = 1 - np.isnan(out_u).mean()
    print(
        f'Fraction of (time, level, lat, lon) points with valid data '
        f'after fill: {valid_frac_after:.4f}'
    )

    out = xr.Dataset(
        {
            'u': (('valid_time', 'pressure_level', 'latitude', 'longitude'), out_u),
            'v': (('valid_time', 'pressure_level', 'latitude', 'longitude'), out_v),
        },
        coords={
            'valid_time': times,
            'pressure_level': TARGET_LEVELS_HPA,
            'latitude': lat,
            'longitude': lon,
        },
    )
    out.attrs['title'] = (
        'MERRA2 A3dyn (hybrid-sigma model levels) converted to fixed pressure '
        'levels using real per-column PS from I3, for use with AEIC.weather.Weather. '
        'Horizontal nearest-valid-neighbor fill applied per (time, level) slice to '
        'avoid interpolation-stencil NaN contamination near terrain edges.'
    )
    out.attrs['source_files'] = f'{A3DYN_FILE}, {I3_FILE}'
    out.attrs['hybrid_coefficients'] = HYBRID_JSON

    out.to_netcdf(OUT_FILE)
    print(f'Saved {OUT_FILE}')


if __name__ == '__main__':
    main()
