"""Build GCHP/HEMCO-style flux files for the four cases from the three class grids, by addition only,
with NO and HONO as true species mass.

AEIC's NO, NO2 and HONO are NO2-equivalent masses: the NOx emission index is g NO2 per kg fuel (ICAO
EDB convention) and is split by NOy mole fractions (emissions/ei/nox.py), so each part is the NO2 mass of
that share. HEMCO reads each variable as kg of that species, so NO is converted by M(NO)/M(NO2) and HONO
by M(HONO)/M(NO2); NO2 needs nothing. (build_cases.py wrote the earlier files without this conversion.)
Coordinates carry the axis, standard_name and units attributes GCHP needs (lev units "level", time units without
" UTC", calendar "standard"), so the files are read by GCHP as written.
usage: build_cases_species_mass.py MONTH(1-12) OUTDIR [case ...]     cases: all no_wide no_narrow no_small only_wide only_narrow only_small (default: all seven)
Layout of HEMCO AEIC2019 (time, lev, lat, lon; kg m-2 s-1). LTO (2D *_lto grids) is added to model level 1 only.
Flux = grams / 1000 / cell area / seconds of the calendar month. Flights are in a month by UTC departure day;
the UTC days 2024-12-31 and 2026-01-01 are folded into January and December (their flights kept, recorded in
folded_edge_days), but GCHP applies the flux over the calendar month, so that is the divisor. (v2026.10.12 divided
January and December by 32 days: about 3% low.)"""
import calendar, sys, os, datetime as dt
from typing import NamedTuple
import numpy as np, netCDF4 as nc
R = 6371007.2; CO2_EI = 3155.6          # earth radius of GEOS-Chem (gcpy); g CO2 per kg fuel
M_NO, M_NO2, M_HONO = 30.0061, 46.0055, 47.0134   # g/mol (N 14.0067, O 15.9994, H 1.00794)
CASES = {'only_wide': ('wide',), 'only_narrow': ('narrow',), 'only_small': ('small',), 'all': ('wide', 'narrow', 'small'), 'no_wide': ('narrow', 'small'), 'no_narrow': ('wide', 'small'), 'no_small': ('wide', 'narrow')}
# output name: (source grid variable, scale to kg of the output species per g of source, units, long name)
VARS = [('FUELBURN', 'co2', 1 / CO2_EI, 'kg m-2 s-1', 'Fuel burn, from operational CO2 / 3155.6 g/kg'),
        ('CO', 'co', 1e-3, 'kg m-2 s-1', 'CO'), ('HC', 'hc', 1e-3, 'kg m-2 s-1', 'Hydrocarbons'),
        ('NO', 'no', 1e-3 * M_NO / M_NO2, 'kg m-2 s-1', 'NO (true NO mass: AEIC NO2-equivalent NO x 30.0061/46.0055)'), ('NO2', 'no2', 1e-3, 'kg m-2 s-1', 'NO2 (AEIC NO2-equivalent mass is NO2 mass)'), ('HONO', 'hono', 1e-3 * M_HONO / M_NO2, 'kg m-2 s-1', 'HONO (true HONO mass: AEIC NO2-equivalent HONO x 47.0134/46.0055)'),
        ('BC', 'nvpm', 1e-3, 'kg m-2 s-1', 'Non-volatile PM mass (black carbon)'),
        ('CO2', 'co2', 1e-3, 'kg m-2 s-1', 'Operational CO2 (no lifecycle adjustment)'), ('H2O', 'h2o', 1e-3, 'kg m-2 s-1', 'H2O'),
        ('SO2', 'so2', 1e-3, 'kg m-2 s-1', 'SO2'), ('SO4', 'so4', 1e-3, 'kg m-2 s-1', 'SO4'),
        ('NVPM_N', 'nvpm_n', 1.0, 'm-2 s-1', 'Non-volatile PM number')]
YEAR = 2025
EDGE_DAYS = {1: dt.date(2024, 12, 31), 12: dt.date(2026, 1, 1)}   # UTC departure days outside the year, folded in


class FluxPeriod(NamedTuple):
    start: dt.date                      # first day of the calendar month: the file's time stamp
    seconds: float                      # length of the calendar month: the flux divisor
    folded_days: list[dt.date]          # UTC departure days outside the month whose flights the file holds


def flux_period(month: int, grid_start: str, grid_end: str) -> FluxPeriod:
    """The calendar month of a class grid covering `grid_start`..`grid_end` (inclusive days). The grid must be the
    month itself, or for January (December) the month plus the one UTC day before (after) the year; anything else
    is an error rather than a guess."""
    first = dt.date(YEAR, month, 1); last = dt.date(YEAR, month, calendar.monthrange(YEAR, month)[1])
    g0, g1 = dt.date.fromisoformat(grid_start), dt.date.fromisoformat(grid_end)
    folded = []
    if month == 1 and g0 == EDGE_DAYS[1]: g0 = first; folded.append(EDGE_DAYS[1])
    if month == 12 and g1 == EDGE_DAYS[12]: g1 = last; folded.append(EDGE_DAYS[12])
    if (g0, g1) != (first, last):
        raise ValueError(f'month {month}: grid period {grid_start}..{grid_end} is not the calendar month {first}..{last}'
                         + (f' (plus {EDGE_DAYS[month]})' if month in EDGE_DAYS else ''))
    return FluxPeriod(first, ((last - first).days + 1) * 86400.0, folded)


def main():
  O = os.environ['RUN_DIR']
  month = int(sys.argv[1]); outdir = sys.argv[2]; cases = sys.argv[3:] or list(CASES)
  os.makedirs(outdir, exist_ok=True)
  assert 1 <= month <= 12 and all(c in CASES for c in cases)
  MM = f'{month:02d}'
  src = {c: nc.Dataset(f'{O}/grids_class/{c}/{c}-2025-{MM}.nc') for c in ('wide', 'narrow', 'small')}
  ref = src['wide']
  for d in src.values():
      assert (d.period_start_utc, d.period_end_utc) == (ref.period_start_utc, ref.period_end_utc), 'class periods differ'
  period = flux_period(month, ref.period_start_utc, ref.period_end_utc); seconds = period.seconds
  lat = np.asarray(ref['latitude'][:]); lon = np.asarray(ref['longitude'][:])
  lb = np.asarray(ref['lat_bnds'][:]); ob = np.asarray(ref['lon_bnds'][:])
  area = (R ** 2 * np.deg2rad(ob[:, 1] - ob[:, 0])[None, :] * (np.sin(np.deg2rad(lb[:, 1])) - np.sin(np.deg2rad(lb[:, 0])))[:, None])
  assert abs(area.sum() / (4 * np.pi * R ** 2) - 1) < 1e-9, 'cell areas do not add up to the sphere'
  for case in cases:
      path = f'{outdir}/AEIC_2025_{case}_{MM}.0.5x0.625.72L.nc'
      o = nc.Dataset(path, 'w', format='NETCDF4')
      o.createDimension('time', 1); o.createDimension('lev', 72); o.createDimension('lat', len(lat)); o.createDimension('lon', len(lon)); o.createDimension('nv', 2)
      for name, arr, dim, att in (('lat', lat, 'lat', {'units': 'degrees_north', 'standard_name': 'latitude', 'long_name': 'latitude', 'axis': 'Y'}),
                                    ('lon', lon, 'lon', {'units': 'degrees_east', 'standard_name': 'longitude', 'long_name': 'longitude', 'axis': 'X'})):
          v = o.createVariable(name, 'f8', (dim,)); v[:] = arr; v.setncatts(att)
      v = o.createVariable('lat_bnds', 'f8', ('lat', 'nv')); v[:] = lb
      v = o.createVariable('lon_bnds', 'f8', ('lon', 'nv')); v[:] = ob
      v = o.createVariable('lev', 'i4', ('lev',)); v[:] = np.arange(1, 73); v.setncatts({'long_name': 'GEOS-Chem level (1 = surface)', 'positive': 'up', 'units': 'level', 'standard_name': 'level', 'axis': 'Z'})
      t = o.createVariable('time', 'f8', ('time',)); t[:] = (dt.datetime.combine(period.start, dt.time(), dt.timezone.utc) - dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)).total_seconds(); t.setncatts({'units': ref['time'].units.removesuffix(' UTC'), 'calendar': 'standard', 'axis': 'T', 'standard_name': 'time', 'long_name': 'Start of inventory period (UTC)'})
      assert t.units == 'seconds since 1970-01-01 00:00:00', t.units                 # GCHP (MAPL) rejects a trailing " UTC"
      ar = o.createVariable('AREA', 'f8', ('lat', 'lon')); ar[:] = area; ar.setncatts({'units': 'm2', 'long_name': 'Cell area on a sphere of radius 6371007.2 m'})
      for name, g, scale, units, long_name in VARS:
          field = np.zeros((72, len(lat), len(lon)), dtype=np.float64)
          for c in CASES[case]:
              field += np.asarray(src[c][g][0], dtype=np.float64)
              field[0] += np.asarray(src[c][g + '_lto'][0], dtype=np.float64)       # take-off and idle (the 2-D LTO grid) in model level 1
          v = o.createVariable(name, 'f4', ('time', 'lev', 'lat', 'lon'), zlib=True, complevel=1, chunksizes=(1, 1, len(lat), len(lon)))
          v[0] = (field * scale / area / seconds).astype(np.float32)
          v.setncatts({'units': units, 'long_name': long_name})
      o.setncatts({'title': f'AEIC {os.path.basename(O)} aviation emissions, case {case}', 'case': case, 'classes_included': ','.join(CASES[case]),
                   'source': f'class grids (wide, narrow, small) of the merged {os.path.basename(O)} store, added; no subtraction',
                   'period_start_utc': period.start.isoformat(), 'period_end_utc': (period.start + dt.timedelta(seconds=seconds - 86400)).isoformat(), 'period_seconds': seconds,
                   'folded_edge_days': ','.join(d.isoformat() for d in period.folded_days) or 'none', 'grid_period_utc': f'{ref.period_start_utc}..{ref.period_end_utc}',
                   'lto': 'take-off and idle (taxi) are in model level 1; climb-out and approach are in the 3-D fields, spread from the airport to 3,000 ft above it; APU and GSE are not included',
                   'not_included': 'OC (volatile organic PM), DISTANCE: not produced by AEIC',
                   'nox_convention': 'NO, NO2, HONO are true species masses; NOx as NO2 = NO*46.0055/30.0061 + NO2 + HONO*46.0055/47.0134', 'created_utc': dt.datetime.now(dt.timezone.utc).isoformat()})
      o.close(); print('wrote', path)


if __name__ == '__main__':
    main()
