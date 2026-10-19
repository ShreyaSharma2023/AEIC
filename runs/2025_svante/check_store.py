"""Parallel check of the merged store: trajectories, rows with any NaN in total_emissions, CO2 vs fuel*3.16, commits."""
import sys, glob, json, collections, numpy as np, netCDF4 as nc
from multiprocessing import Pool
def one(f):
    try:
        return one_(f)
    except Exception as e:
        d = nc.Dataset(f); t = d['emissions/trajectory_emissions']; i = (str(t.dtype), t.shape); d.close()
        return dict(f=f, error=repr(e)[:200], info=i)
def one_(f):
    d = nc.Dataset(f)
    sp = list(d['species'][:]); co2 = sp.index('CO2')
    te = np.asarray(d['emissions/total_emissions'][:]); tr = np.array([np.nansum(np.asarray(e, dtype=float)) for e in d['emissions/trajectory_emissions'][:][:, co2]])
    fuel = np.asarray(d['base/total_fuel_mass'][:])
    r = dict(f=f, n=te.shape[0], nan_rows=int(np.isnan(te).any(axis=1).sum()), nan_fuel=int(np.isnan(fuel).sum()),
             neg=int((te < 0).sum()), fuel=float(np.nansum(fuel)), co2_traj=float(np.nansum(tr)), species=sp,
             commit=str(d['_reproducibility/git_commit'][()]), dirty=int(d['_reproducibility/git_dirty'][()]),
             co2_total=float(np.nansum(te[:, co2])))
    d.close(); return r
if __name__ == '__main__':
    files = sorted(glob.glob(sys.argv[1] + '/*-[0-9][0-9][0-9].nc'))
    if len(sys.argv) > 3: files = files[:int(sys.argv[3])]
    with Pool(16) as p: res = p.map(one, files, chunksize=4)
    bad = [r for r in res if 'error' in r]
    print('files with errors', len(bad)); [print(' ', r['f'].rpartition('/')[2], r['info'], r['error']) for r in bad[:10]]
    res = [r for r in res if 'error' not in r]
    n = sum(r['n'] for r in res)
    print('files', len(files), 'trajectories', n, 'rows with NaN', sum(r['nan_rows'] for r in res), 'NaN fuel', sum(r['nan_fuel'] for r in res), 'negative values', sum(r['neg'] for r in res))
    F = sum(r['fuel'] for r in res); C = sum(r['co2_traj'] for r in res); T = sum(r['co2_total'] for r in res)
    print(f'fuel {F/1e9:.4f} Tg  trajectory CO2 {C/1e12:.4f} Tg  CO2 g per kg fuel {C/F:.1f}  total CO2 (incl LTO, APU, GSE) {T/1e12:.4f} Tg')
    print('commits', dict(collections.Counter((r['commit'][:7], r['dirty']) for r in res)))
    print('species orders', len({tuple(r['species']) for r in res}))
    json.dump(res, open(sys.argv[2], 'w'))
