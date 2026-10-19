import os
import glob, sqlite3, numpy as np, netCDF4 as nc
O = os.environ['RUN_DIR']
db = sqlite3.connect('file:/net/fs12/d1/shreya22/Parser/Output/aeic_input_v2/2025/oag_2025_cirium.sqlite?mode=ro', uri=True)
print([r[1] for r in db.execute('pragma table_info(airports)')])
f = sorted(glob.glob(f'{O}/test_day/trajectories/2025-01-15/2025-01-15-0*.nc'))[3]; d = nc.Dataset(f)
ids = np.asarray(d['base/flight_id'][:])[:3000]; alt = d['base/altitude'][:][:3000]
a0 = np.array([a[0] for a in alt]); a1 = np.array([a[-1] for a in alt])
el0, el1 = [], []
for i in ids:
    o, de = db.execute('select a.elevation, b.elevation from schedules s join flights f on f.id=s.flight_id join airports a on a.id=f.origin join airports b on b.id=f.destination where s.id=?', (int(i),)).fetchone()
    el0.append(o or 0.0); el1.append(de or 0.0)
el0, el1 = np.array(el0), np.array(el1)
for nm, a, e in (('start', a0, el0), ('end', a1, el1)):
    agl = a - e
    print(f'{nm}: altitude above sea level median {np.median(a):.0f} m; above the airport (altitude - elevation): min {agl.min():.0f}, p5 {np.percentile(agl, 5):.0f}, median {np.median(agl):.0f}, p95 {np.percentile(agl, 95):.0f}, max {agl.max():.0f} m   (3,000 ft = 914 m; 1,500 ft = 457 m)')
print('first points of 3 flights (m):', [np.round(a[:3]).tolist() for a in alt[:3]])
