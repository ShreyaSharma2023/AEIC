"""Hard check that wide + narrow + small partition the merged store. Exits non-zero on any failure.
1. every performance model key (models/*.toml) is in exactly one class filter;
2. the 12 monthly files of each class exist, carry the same periods, and have no NaN or negative cells;
3. wide + narrow + small annual CO2 equals the store's trajectory and LTO CO2 (from components_626168.out), relative 1e-6.
usage: partition_check.py"""
import glob, re, sys, tomllib
import numpy as np, netCDF4 as nc
O = '/net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12'
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
txt = open(f'{O}/logs/components_626168.out').read()
ref = {k: float(re.search(rf'^{k}\s+([\d.]+) Tg CO2', txt, re.M).group(1)) * 1e12 for k in ('traj', 'lto')}
for n, k in (('co2', 'traj'), ('co2_lto', 'lto')):
    s = sum(tot.get((c, n), 0.0) for c in CLS)
    rel = s / ref[k] - 1
    print(f'{n}: classes {s / 1e12:.4f} Tg, store {ref[k] / 1e12:.3f} Tg (rounded to 1e-3), relative {rel:+.2e}')
    if abs(rel) > 2e-6 * 1 and abs(s - ref[k]) > 0.0006e12: bad.append(f'{n}: classes differ from the store by {rel:+.2e}')
print('PARTITION CHECK', 'FAILED' if bad else 'PASSED')
for b in bad: print('  FAIL', b)
sys.exit(1 if bad else 0)
