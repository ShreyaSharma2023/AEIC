import os
import glob, numpy as np, netCDF4 as nc
O = os.environ['RUN_DIR']
f = sorted(glob.glob(f'{O}/test_day/trajectories/2025-01-15/2025-01-15-0*.nc'))[2]; d = nc.Dataset(f)
modes = list(d['thrust_mode'][:]); sp = list(d['species'][:]); print('thrust modes in the file:', modes)
for s in ('CO2', 'NOx', 'HC', 'CO'):
    x = np.asarray(d['emissions/lto_emissions'][:], dtype=float)[:, sp.index(s), :]
    tot = x.sum(axis=0); print(f'{s:4s} LTO by mode (share of LTO total):', {m: f'{100 * t / tot.sum():.1f}%' for m, t in zip(modes, tot)})
print('fuel per flight in LTO, by mode, CO2/3155.6 (kg):', {m: round(float(t / 3155.6 / x.shape[0]), 1) for m, t in zip(modes, np.asarray(d['emissions/lto_emissions'][:], dtype=float)[:, sp.index('CO2'), :].sum(axis=0))})
print('grid variable dims: co2', 'see grid files')
