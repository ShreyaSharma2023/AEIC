"""The regenerated species-mass files against the copies GCHP reads (Paper_1/AviationEmis_gchp_ready_species_mass):
every variable's data must be identical and the coordinate attributes GCHP needs must match. Exit non-zero on any difference."""
import glob, os, sys, numpy as np, netCDF4 as nc
A = '/net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12/cases_species_mass'; B = '/net/fs12/d1/shreya22/Paper_1/AviationEmis_gchp_ready_species_mass'
fa = sorted(os.path.basename(p) for p in glob.glob(A + '/*.nc')); fb = sorted(os.path.basename(p) for p in glob.glob(B + '/*.nc'))
bad = [] if fa == fb else [f'file lists differ: {len(fa)} vs {len(fb)}']
for f in fa:
    a, b = nc.Dataset(f'{A}/{f}'), nc.Dataset(f'{B}/{f}')
    if set(a.variables) != set(b.variables): bad.append(f'{f}: variables differ')
    for n in a.variables:
        if not np.array_equal(np.asarray(a[n][:]), np.asarray(b[n][:]), equal_nan=True): bad.append(f'{f} {n}: data differ')
    for n in ('lev', 'lat', 'lon', 'time'):
        for k in ('units', 'standard_name', 'long_name', 'axis', 'calendar'):
            if k in b[n].ncattrs() and (k not in a[n].ncattrs() or a[n].getncattr(k) != b[n].getncattr(k)):
                if not (n in ('lev', 'time') and k == 'long_name'): bad.append(f'{f} {n}.{k}: {a[n].getncattr(k) if k in a[n].ncattrs() else None!r} vs {b[n].getncattr(k)!r}')
    a.close(); b.close()
print(f'{len(fa)} files compared'); print('COMPARE', 'FAILED' if bad else 'PASSED: data identical, GCHP attributes present')
for x in bad[:30]: print('  ', x)
sys.exit(1 if bad else 0)
