"""Sum CO2 by source over the merged store: trajectory, LTO, APU, GSE, lifecycle adjustment, total. Grams."""
import sys, glob, numpy as np, netCDF4 as nc
from multiprocessing import Pool
def one(f):
    d = nc.Dataset(f); co2 = list(d['species'][:]).index('CO2')
    a = lambda n: np.asarray(d[n][:], dtype=float)
    tr = sum(float(np.nansum(np.asarray(e, dtype=float))) for e in d['emissions/trajectory_emissions'][:][:, co2])
    r = dict(traj=tr, lto=float(np.nansum(a('emissions/lto_emissions')[:, co2, :])), apu=float(np.nansum(a('emissions/apu_emissions')[:, co2])),
             gse=float(np.nansum(a('emissions/gse_emissions')[:, co2])), life=float(np.nansum(a('emissions/lifecycle_co2'))),
             total=float(np.nansum(a('emissions/total_emissions')[:, co2])), burn=float(np.nansum(a('emissions/total_fuel_burn'))), n=int(a('emissions/total_fuel_burn').shape[0]))
    d.close(); return r
if __name__ == '__main__':
    files = sorted(glob.glob(sys.argv[1] + '/*-[0-9][0-9][0-9].nc'))
    if len(sys.argv) > 2: files = files[:int(sys.argv[2])]
    with Pool(int(__import__('os').environ.get('NPROC', '8'))) as p: res = p.map(one, files, chunksize=4)
    S = {k: sum(r[k] for r in res) for k in res[0]}
    print('files', len(files), 'flights', S['n'])
    for k in ('traj', 'lto', 'apu', 'gse', 'life', 'total'): print('%-6s %10.3f Tg CO2' % (k, S[k] / 1e12))
    print('traj+lto+apu+gse = %.3f Tg ; + life = %.3f Tg ; stored total = %.3f Tg' % ((S['traj'] + S['lto'] + S['apu'] + S['gse']) / 1e12, (S['traj'] + S['lto'] + S['apu'] + S['gse'] + S['life']) / 1e12, S['total'] / 1e12))
    print('total fuel burn %.3f Tg (kg/1e9)' % (S['burn'] / 1e9))
