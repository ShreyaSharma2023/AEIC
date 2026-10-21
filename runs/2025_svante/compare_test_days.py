"""Compare the test day of a run ($RUN_DIR/test_day) with a reference run's ($REF_DIR/test_day), flight by flight.
Read-only. Written for v2026.10.19 against v2026.10.12, whose only intended differences are the LTO climb-out and
approach modes (zero before, kept now). Exit 1 if anything else differs.

usage: RUN_DIR=... REF_DIR=... compare_test_days.py [DAY]     (default 2025-01-15)
Checks: same flights; trajectory fuel and per-species trajectory emissions identical (relative 1e-9); LTO idle and
take-off identical; LTO climb-out and approach zero in the reference and positive in the new run. Reports the change in
total fuel, CO2 and NOx and the share of NOx in each LTO mode."""
import glob, os, sys
from pathlib import Path
import numpy as np
import netCDF4 as nc

DAY = sys.argv[1] if len(sys.argv) > 1 else '2025-01-15'
RUNS = {'ref': Path(os.environ['REF_DIR']), 'new': Path(os.environ['RUN_DIR'])}
SP = ['CO2', 'H2O', 'HC', 'CO', 'NOx', 'nvPM']
CO2_EI = 3155.6                                  # g CO2 per kg fuel (fuel per LTO mode = CO2 / CO2_EI)


def load(run):
    files = sorted(glob.glob(str(RUNS[run] / 'test_day/trajectories' / DAY / f'{DAY}-[0-9][0-9][0-9].nc')))
    assert files, f'{run}: no trajectory files for {DAY} under {RUNS[run]}/test_day'
    ids, traj_fuel, traj_em, lto, tot_fuel, tot_em = [], [], [], [], [], []
    for f in files:
        d = nc.Dataset(f); e = d['emissions']
        sp = list(d['species'][:]); ix = [sp.index(s) for s in SP]; modes = list(d['thrust_mode'][:])
        ids.append(np.asarray(d['base/flight_id'][:]))
        traj_fuel.append(np.array([float(np.sum(x)) for x in e['fuel_burn_per_segment'][:]]))
        te = e['trajectory_emissions'][:]
        traj_em.append(np.array([[float(np.sum(te[i, j])) for j in ix] for i in range(te.shape[0])]))
        lto.append(np.asarray(e['lto_emissions'][:], dtype=float)[:, ix, :])
        tot_fuel.append(np.asarray(e['total_fuel_burn'][:], dtype=float))
        tot_em.append(np.asarray(e['total_emissions'][:], dtype=float)[:, ix])
        d.close()
    ids = np.concatenate(ids); order = np.argsort(ids)
    assert len(np.unique(ids)) == len(ids), f'{run}: duplicate flight ids'
    cat = lambda a: np.concatenate(a)[order]
    return dict(ids=ids[order], traj_fuel=cat(traj_fuel), traj_em=cat(traj_em), lto=cat(lto),
                tot_fuel=cat(tot_fuel), tot_em=cat(tot_em), modes=modes)


r, n = load('ref'), load('new')
bad = []
print(f'== {DAY}: {len(r["ids"])} flights in ref ({RUNS["ref"].name}), {len(n["ids"])} in new ({RUNS["new"].name})')
if not np.array_equal(r['ids'], n['ids']):
    bad.append(f'flight sets differ: {len(np.setdiff1d(r["ids"], n["ids"]))} only in ref, {len(np.setdiff1d(n["ids"], r["ids"]))} only in new')
    common = np.intersect1d(r['ids'], n['ids'])
    for x in (r, n):
        k = np.isin(x['ids'], common)
        for key in ('ids', 'traj_fuel', 'traj_em', 'lto', 'tot_fuel', 'tot_em'): x[key] = x[key][k]
assert r['modes'] == n['modes'], (r['modes'], n['modes'])
M = {m: i for i, m in enumerate(r['modes'])}


def rel(a, b): return float(np.max(np.abs(a - b) / np.maximum(np.abs(b), 1e-300)))


d = rel(n['traj_fuel'], r['traj_fuel']); print(f'trajectory fuel: max relative difference per flight {d:.1e}')
if d > 1e-9: bad.append(f'trajectory fuel differs ({d:.1e})')
for j, s in enumerate(SP):
    d = rel(n['traj_em'][:, j], r['traj_em'][:, j])
    if d > 1e-9: bad.append(f'trajectory {s} differs ({d:.1e})')
print('trajectory emissions (' + ', '.join(SP) + '): ' + ('identical' if not any('trajectory ' in b for b in bad) else 'DIFFER'))
for m in ('IDLE', 'TAKEOFF'):
    d = rel(n['lto'][:, :, M[m]], r['lto'][:, :, M[m]]); print(f'LTO {m:8s}: max relative difference {d:.1e}')
    if d > 1e-9: bad.append(f'LTO {m} differs ({d:.1e})')
for m in ('CLIMB', 'APPROACH'):
    ref_max = float(np.abs(r['lto'][:, :, M[m]]).max()); new_pos = float((n['lto'][:, SP.index('CO2'), M[m]] > 0).mean())
    print(f'LTO {m:8s}: ref max {ref_max:.3g} g (expect 0); new: {new_pos * 100:.2f}% of flights > 0')
    if ref_max != 0: bad.append(f'ref LTO {m} not zero')
    if new_pos < 1: bad.append(f'new LTO {m} zero for {(1 - new_pos) * 100:.2f}% of flights')
fr, fn = r['tot_fuel'].sum(), n['tot_fuel'].sum()
print(f'total fuel: {fr / 1e6:.3f} -> {fn / 1e6:.3f} kt  ({(fn / fr - 1) * 100:+.2f}%)')
for s in ('CO2', 'NOx', 'nvPM', 'CO', 'HC'):
    j = SP.index(s); a, b = r['tot_em'][:, j].sum(), n['tot_em'][:, j].sum()
    print(f'total {s:4s}: {(b / a - 1) * 100:+.2f}%')
j = SP.index('NOx'); lt = n['lto'][:, j, :].sum(axis=0); tot = n['tot_em'][:, j].sum()
print('new NOx share by LTO mode: ' + ', '.join(f'{m} {lt[M[m]] / tot * 100:.2f}%' for m in r['modes'])
      + f'; climb-out + approach fuel {(n["lto"][:, SP.index("CO2"), M["CLIMB"]].sum() + n["lto"][:, SP.index("CO2"), M["APPROACH"]].sum()) / CO2_EI / fn * 100:.2f}% of total')
print('TEST DAY COMPARISON', 'FAILED' if bad else 'PASSED')
for b in bad: print('  FAIL', b)
sys.exit(1 if bad else 0)
