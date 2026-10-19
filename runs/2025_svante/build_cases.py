"""Build GCHP/HEMCO-style flux files for the four cases from the three class grids, by addition only.
usage: build_cases.py MONTH(1-12) OUTDIR [case ...]     cases: all no_wide no_narrow no_small (default: all four)
Layout of HEMCO AEIC2019 (time, lev, lat, lon; kg m-2 s-1). LTO (2D *_lto grids) is added to model level 1 only.
Flux = grams / 1000 / cell area / seconds of the file's period (period_end is a day, inclusive)."""
import sys, os, datetime as dt
import numpy as np, netCDF4 as nc
O = '/net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12'
R = 6371007.2; CO2_EI = 3155.6          # earth radius of GEOS-Chem (gcpy); g CO2 per kg fuel
CASES = {'only_wide': ('wide',), 'only_narrow': ('narrow',), 'only_small': ('small',), 'all': ('wide', 'narrow', 'small'), 'no_wide': ('narrow', 'small'), 'no_narrow': ('wide', 'small'), 'no_small': ('wide', 'narrow')}
# output name: (source grid variable, scale to kg of the output species per g of source, units, long name)
VARS = [('FUELBURN', 'co2', 1 / CO2_EI, 'kg m-2 s-1', 'Fuel burn, from operational CO2 / 3155.6 g/kg'),
        ('CO', 'co', 1e-3, 'kg m-2 s-1', 'CO'), ('HC', 'hc', 1e-3, 'kg m-2 s-1', 'Hydrocarbons'),
        ('NO', 'no', 1e-3, 'kg m-2 s-1', 'NO'), ('NO2', 'no2', 1e-3, 'kg m-2 s-1', 'NO2'), ('HONO', 'hono', 1e-3, 'kg m-2 s-1', 'HONO'),
        ('BC', 'nvpm', 1e-3, 'kg m-2 s-1', 'Non-volatile PM mass (black carbon)'),
        ('CO2', 'co2', 1e-3, 'kg m-2 s-1', 'Operational CO2 (no lifecycle adjustment)'), ('H2O', 'h2o', 1e-3, 'kg m-2 s-1', 'H2O'),
        ('SO2', 'so2', 1e-3, 'kg m-2 s-1', 'SO2'), ('SO4', 'so4', 1e-3, 'kg m-2 s-1', 'SO4'),
        ('NVPM_N', 'nvpm_n', 1.0, 'm-2 s-1', 'Non-volatile PM number')]
month = int(sys.argv[1]); outdir = sys.argv[2]; cases = sys.argv[3:] or list(CASES)
os.makedirs(outdir, exist_ok=True)
assert 1 <= month <= 12 and all(c in CASES for c in cases)
MM = f'{month:02d}'
src = {c: nc.Dataset(f'{O}/grids_class/{c}/{c}-2025-{MM}.nc') for c in ('wide', 'narrow', 'small')}
ref = src['wide']
for d in src.values():
    assert (d.period_start_utc, d.period_end_utc) == (ref.period_start_utc, ref.period_end_utc), 'class periods differ'
p0, p1 = dt.date.fromisoformat(ref.period_start_utc), dt.date.fromisoformat(ref.period_end_utc)
seconds = ((p1 - p0).days + 1) * 86400.0
lat = np.asarray(ref['latitude'][:]); lon = np.asarray(ref['longitude'][:])
lb = np.asarray(ref['lat_bnds'][:]); ob = np.asarray(ref['lon_bnds'][:])
area = (R ** 2 * np.deg2rad(ob[:, 1] - ob[:, 0])[None, :] * (np.sin(np.deg2rad(lb[:, 1])) - np.sin(np.deg2rad(lb[:, 0])))[:, None])
assert abs(area.sum() / (4 * np.pi * R ** 2) - 1) < 1e-9, 'cell areas do not add up to the sphere'
for case in cases:
    path = f'{outdir}/AEIC_2025_{case}_{MM}.0.5x0.625.72L.nc'
    o = nc.Dataset(path, 'w', format='NETCDF4')
    o.createDimension('time', 1); o.createDimension('lev', 72); o.createDimension('lat', len(lat)); o.createDimension('lon', len(lon)); o.createDimension('nv', 2)
    for name, arr, dim, att in (('lat', lat, 'lat', {'units': 'degrees_north'}), ('lon', lon, 'lon', {'units': 'degrees_east'})):
        v = o.createVariable(name, 'f8', (dim,)); v[:] = arr; v.setncatts(att)
    v = o.createVariable('lat_bnds', 'f8', ('lat', 'nv')); v[:] = lb
    v = o.createVariable('lon_bnds', 'f8', ('lon', 'nv')); v[:] = ob
    v = o.createVariable('lev', 'i4', ('lev',)); v[:] = np.arange(1, 73); v.setncatts({'long_name': 'GEOS-Chem level (1 = surface)', 'positive': 'up'})
    t = o.createVariable('time', 'f8', ('time',)); t[:] = float(ref['time'][0]); t.setncatts({'units': ref['time'].units, 'long_name': 'Start of inventory period'})
    ar = o.createVariable('AREA', 'f8', ('lat', 'lon')); ar[:] = area; ar.setncatts({'units': 'm2', 'long_name': 'Cell area on a sphere of radius 6371007.2 m'})
    for name, g, scale, units, long_name in VARS:
        field = np.zeros((72, len(lat), len(lon)), dtype=np.float64)
        for c in CASES[case]:
            field += np.asarray(src[c][g][0], dtype=np.float64)
            field[0] += np.asarray(src[c][g + '_lto'][0], dtype=np.float64)       # all LTO in model level 1
        v = o.createVariable(name, 'f4', ('time', 'lev', 'lat', 'lon'), zlib=True, complevel=1, chunksizes=(1, 1, len(lat), len(lon)))
        v[0] = (field * scale / area / seconds).astype(np.float32)
        v.setncatts({'units': units, 'long_name': long_name})
    o.setncatts({'title': f'AEIC v2026.10.12 aviation emissions, case {case}', 'case': case, 'classes_included': ','.join(CASES[case]),
                 'source': 'class grids (wide, narrow, small) of the merged v2026.10.12 store, added; no subtraction',
                 'period_start_utc': ref.period_start_utc, 'period_end_utc': ref.period_end_utc, 'period_seconds': seconds,
                 'lto': 'idle, takeoff (and all LTO emissions of the store) are in model level 1; APU and GSE are not included',
                 'not_included': 'OC (volatile organic PM), DISTANCE: not produced by AEIC', 'created_utc': dt.datetime.now(dt.timezone.utc).isoformat()})
    o.close(); print('wrote', path)
