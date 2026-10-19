"""Check the 48 + 36 species-mass files (cases_species_mass, class_flux_species_mass) against the class grids. Exit non-zero on failure.
5. NOx closure: NO*M_NO2/M_NO + NO2 + HONO*M_NO2/M_HONO equals the class grids' NOx (trajectory + LTO), relative 1e-5.
6. the single-class files add up to all, cell by cell.
1. 48 files, all variables finite, none negative.
2. Per file and species: area * seconds * flux (converted back to grams) equals the sum of the included class grids (trajectory + LTO), relative 1e-5.
3. no_wide + no_narrow + no_small == 2 * all, per variable and month (cell by cell, relative 1e-5 of the variable's max).
4. Level 1 holds at least the LTO of the included classes; levels 2-72 equal the trajectory part only."""
import sys, numpy as np, netCDF4 as nc
O = '/net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12'
M_NO, M_NO2, M_HONO = 30.0061, 46.0055, 47.0134
CASES = {'only_wide': ('wide',), 'only_narrow': ('narrow',), 'only_small': ('small',), 'all': ('wide', 'narrow', 'small'), 'no_wide': ('narrow', 'small'), 'no_narrow': ('wide', 'small'), 'no_small': ('wide', 'narrow')}
SRC = {'FUELBURN': ('co2', 1 / 3155.6), 'CO': ('co', 1e-3), 'HC': ('hc', 1e-3), 'NO': ('no', 1e-3 * M_NO / M_NO2), 'NO2': ('no2', 1e-3), 'HONO': ('hono', 1e-3 * M_HONO / M_NO2), 'BC': ('nvpm', 1e-3),
       'CO2': ('co2', 1e-3), 'H2O': ('h2o', 1e-3), 'SO2': ('so2', 1e-3), 'SO4': ('so4', 1e-3), 'NVPM_N': ('nvpm_n', 1.0)}
bad = []; worst = 0.0
for m in range(1, 13):
    MM = f'{m:02d}'; cls = {c: nc.Dataset(f'{O}/grids_class/{c}/{c}-2025-{MM}.nc') for c in ('wide', 'narrow', 'small')}
    out = {}
    for case, inc in CASES.items():
        d = nc.Dataset(f'{O}/{"class_flux_species_mass" if case.startswith("only_") else "cases_species_mass"}/AEIC_2025_{case}_{MM}.0.5x0.625.72L.nc'); area = np.asarray(d['AREA'][:]); sec = d.period_seconds
        out[case] = {}
        for v, (g, scale) in SRC.items():
            a = np.asarray(d[v][0], dtype=np.float64); out[case][v] = a
            if not np.isfinite(a).all(): bad.append(f'{case} {MM} {v}: non-finite')
            if (a < 0).any(): bad.append(f'{case} {MM} {v}: negative')
            tot_file = (a * area[None]).sum() * sec / scale
            traj = sum(float(np.asarray(cls[c][g][0], dtype=np.float64).sum()) for c in inc)
            lto = sum(float(np.asarray(cls[c][g + '_lto'][0], dtype=np.float64).sum()) for c in inc)
            rel = tot_file / (traj + lto) - 1; worst = max(worst, abs(rel))
            if abs(rel) > 1e-5: bad.append(f'{case} {MM} {v}: file {tot_file:.6e} vs classes {traj + lto:.6e} ({rel:+.1e})')
            if v == 'CO2' and case == 'all':
                lev1 = (a[0] * area).sum() * sec / scale; upper = (a[1:] * area[None]).sum() * sec / scale
                if abs(upper / traj - 1) > 1e-5 or lev1 < lto * (1 - 1e-5): bad.append(f'{MM}: level split wrong (upper {upper:.6e} vs traj {traj:.6e})')
        nox = sum(((out[case][v] * area[None]).sum() * sec * 1e3) * f for v, f in (('NO', M_NO2 / M_NO), ('NO2', 1.0), ('HONO', M_NO2 / M_HONO)))
        ref = sum(float(np.asarray(cls[c]['nox'][0], dtype=np.float64).sum()) + float(np.asarray(cls[c]['nox_lto'][0], dtype=np.float64).sum()) for c in inc)
        if abs(nox / ref - 1) > 1e-5: bad.append(f'{case} {MM}: NOx closure {nox:.6e} vs {ref:.6e}')
        d.close()
    for v in SRC:
        s = out['no_wide'][v] + out['no_narrow'][v] + out['no_small'][v]; t = 2 * out['all'][v]
        err = float(np.abs(s - t).max()) / max(float(t.max()), 1e-300)
        if err > 1e-5: bad.append(f'{MM} {v}: no_wide+no_narrow+no_small != 2*all (max rel {err:.1e})')
    for v in SRC:
        s3 = out['only_wide'][v] + out['only_narrow'][v] + out['only_small'][v]
        err = float(np.abs(s3 - out['all'][v]).max()) / max(float(out['all'][v].max()), 1e-300)
        if err > 1e-5: bad.append(f'{MM} {v}: classes do not add up to all ({err:.1e})')
    print(f'month {MM} checked', flush=True)
print(f'worst file-vs-class relative difference {worst:.2e}')
print('CASE CHECK', 'FAILED' if bad else 'PASSED')
for b in bad[:40]: print('  FAIL', b)
sys.exit(1 if bad else 0)
