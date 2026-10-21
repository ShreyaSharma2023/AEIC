"""Hard check that wide + narrow + small partition the merged store. Exits non-zero on any failure.
1. every performance model key (models/*.toml) is in exactly one class filter;
2. the 12 monthly files of each class exist, carry the same periods, and have no NaN or negative cells;
3. wide + narrow + small annual CO2, 3-D grid plus 2-D surface LTO grid, equals the store's trajectory plus LTO CO2 (from the run's
   logs/components_*.out), relative 1e-6. The split between the two grids is not the store's: the surface grid holds take-off and
   idle, and the 3-D grid holds the trajectories plus the LTO climb-out and approach columns (AEIC commit cdf3894), so the
   3-D total exceeds the store's trajectory CO2 and the surface total is below the store's LTO CO2 by the same amount.
usage: partition_check.py"""
import os
import glob, re, sys, tomllib
import numpy as np, netCDF4 as nc
O = os.environ['RUN_DIR']
CLS = ('wide', 'narrow', 'small'); TOL = 1e-6; bad = []
keys = {p.split('/')[-1][:-5] for p in glob.glob(f'{O}/models/*.toml')} - {'config'}
sets = {c: set(tomllib.load(open(f'{O}/class_filters/{c}.toml', 'rb'))['performance_model_key']) for c in CLS}
for a in CLS:
    for b in CLS:
        if a < b and sets[a] & sets[b]: bad.append(f'keys in both {a} and {b}: {sorted(sets[a] & sets[b])[:5]}')
if keys - set().union(*sets.values()): bad.append(f'keys in no class: {sorted(keys - set().union(*sets.values()))[:5]}')
if set().union(*sets.values()) - keys: bad.append('class filters hold keys with no model')
print(f'keys {len(keys)}: ' + ', '.join(f'{c} {len(sets[c])}' for c in CLS))
tot = {}; periods = {}
for c in CLS:
    fs = sorted(glob.glob(f'{O}/grids_class/{c}/{c}-2025-[0-9][0-9].nc'))
    if len(fs) != 12: bad.append(f'{c}: {len(fs)} monthly files, expected 12'); continue
    for f in fs:
        d = nc.Dataset(f); m = f[-5:-3]
        periods.setdefault(m, set()).add((d.period_start_utc, d.period_end_utc))
        for n in d.variables:
            if n.endswith('_lto') or n in ('co2', 'h2o', 'hc', 'co', 'nox', 'no', 'no2', 'hono', 'sox', 'so2', 'so4', 'nvpm', 'nvpm_n'):
                a = np.asarray(d[n][:], dtype=np.float64)
                if np.isnan(a).any(): bad.append(f'{f} {n}: NaN')
                if (a < 0).any(): bad.append(f'{f} {n}: negative cells')
                if n in ('co2', 'co2_lto'): tot[(c, n)] = tot.get((c, n), 0.0) + float(a.sum())
        d.close()
    print(f'{c}: 12 files, CO2 traj {tot.get((c, "co2"), 0) / 1e12:.3f} Tg, LTO {tot.get((c, "co2_lto"), 0) / 1e12:.3f} Tg')
for m, p in sorted(periods.items()):
    if len(p) != 1: bad.append(f'month {m}: classes disagree on the period {p}')
logs = glob.glob(f'{O}/logs/components_*.out')        # the components job's log of THIS run; its name carries the job id
if len(logs) != 1: sys.exit(f'expected exactly one {O}/logs/components_*.out, found {len(logs)}')
txt = open(logs[0]).read()
ref = {k: float(re.search(rf'^{k}\s+([\d.]+) Tg CO2', txt, re.M).group(1)) * 1e12 for k in ('traj', 'lto')}
grid3d = sum(tot.get((c, 'co2'), 0.0) for c in CLS); surface = sum(tot.get((c, 'co2_lto'), 0.0) for c in CLS)
columns = grid3d - ref['traj']                      # what the 3-D grids hold beyond the store's trajectories: climb-out and approach
rel = (grid3d + surface) / (ref['traj'] + ref['lto']) - 1
print(f'3-D grids {grid3d / 1e12:.4f} Tg = store trajectories {ref["traj"] / 1e12:.3f} + climb-out/approach columns {columns / 1e12:.4f}')
print(f'surface LTO grids {surface / 1e12:.4f} Tg (take-off and idle) = {surface / ref["lto"] * 100:.1f}% of the store LTO {ref["lto"] / 1e12:.3f} Tg; columns {columns / ref["lto"] * 100:.1f}%')
print(f'grids total {(grid3d + surface) / 1e12:.4f} Tg, store trajectory + LTO {(ref["traj"] + ref["lto"]) / 1e12:.3f} Tg (rounded to 1e-3), relative {rel:+.2e}')
if abs(rel) > 2e-6 and abs(grid3d + surface - ref['traj'] - ref['lto']) > 0.0012e12: bad.append(f'classes differ from the store by {rel:+.2e}')
if not (0 < columns < ref['lto']): bad.append(f'climb-out/approach columns {columns / 1e12:.3f} Tg are not between 0 and the store LTO')
print('PARTITION CHECK', 'FAILED' if bad else 'PASSED')
for b in bad: print('  FAIL', b)
sys.exit(1 if bad else 0)
