"""only_wide + only_narrow + only_small == all, per month and variable, cell by cell (36 class files, 12 case files)."""
import sys, numpy as np, netCDF4 as nc
O = '/net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12'
VARS = ['FUELBURN', 'CO', 'HC', 'NO', 'NO2', 'HONO', 'BC', 'CO2', 'H2O', 'SO2', 'SO4', 'NVPM_N']; bad = []; worst = 0.0
for m in range(1, 13):
    MM = f'{m:02d}'; a = nc.Dataset(f'{O}/cases/AEIC_2025_all_{MM}.0.5x0.625.72L.nc')
    cl = [nc.Dataset(f'{O}/class_flux/AEIC_2025_only_{c}_{MM}.0.5x0.625.72L.nc') for c in ('wide', 'narrow', 'small')]
    for v in VARS:
        t = np.asarray(a[v][0], dtype=np.float64); s = sum(np.asarray(d[v][0], dtype=np.float64) for d in cl)
        err = float(np.abs(s - t).max()) / max(float(t.max()), 1e-300); worst = max(worst, err)
        if err > 1e-5: bad.append(f'{MM} {v}: max rel difference {err:.1e}')
        if min(float(np.asarray(d[v][0]).min()) for d in cl) < 0: bad.append(f'{MM} {v}: negative')
print(f'worst relative difference {worst:.2e}'); print('CLASS FLUX CHECK', 'FAILED' if bad else 'PASSED')
for b in bad[:30]: print('  FAIL', b)
sys.exit(1 if bad else 0)
