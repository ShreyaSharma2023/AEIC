import os
import glob, sqlite3, tomllib, numpy as np, netCDF4 as nc
O = os.environ['RUN_DIR']; DAY = '2025-01-15'
db = sqlite3.connect('file:/net/fs12/d1/shreya22/Parser/Output/aeic_input_v2/2025/oag_2025_cirium.sqlite?mode=ro', uri=True)
rows = []
for f in sorted(glob.glob(f'{O}/test_day/trajectories/{DAY}/{DAY}-[0-9][0-9][0-9].nc')):
    d = nc.Dataset(f); ow = np.asarray(d['payload/overweight_mass'][:], dtype=float); rs = np.asarray(d['payload/reserve_shortfall'][:], dtype=float)
    ids = np.asarray(d['base/flight_id'][:]); st = np.asarray(d['base/starting_mass'][:], dtype=float); pay = np.asarray(d['payload/payload_mass'][:], dtype=float)
    end = np.array([a[-1] for a in d['base/aircraft_mass'][:]], dtype=float); km = np.array([a[-1] for a in d['base/ground_distance'][:]], dtype=float) / 1000
    fb = np.asarray(d['emissions/total_fuel_burn'][:], dtype=float)
    for i in np.where(ow > 0)[0]:
        key, typ, o, de = db.execute('select f.performance_model_key, f.aircraft_type, f.origin, f.destination from schedules s join flights f on f.id=s.flight_id where s.id=?', (int(ids[i]),)).fetchone()
        m = tomllib.load(open(f'{O}/models/{key}.toml', 'rb'))
        rows.append((typ, key[:26], round(km[i]), st[i] / 1e3, m['operating_empty_mass_kg'] / 1e3, pay[i] / 1e3, end[i] / 1e3, ow[i] / 1e3, rs[i] / 1e3, fb[i] / 1e3))
    d.close()
print('type key km start_t OEW_t payload_t landing_t overweight_t reserve_short_t fuelburn_t (flights with overweight_mass > 0)')
for r in sorted(rows, key=lambda r: -r[2]): print('%s %-26s %5d %7.1f %6.1f %6.1f %7.1f %6.1f %6.1f %7.1f' % r)
