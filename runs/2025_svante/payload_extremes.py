import csv, tomllib, sqlite3
O = '/net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12'; V = '/net/fs12/d1/shreya22/Parser/Output/aeic_runs/2025_full_v1'
new = {r['performance_model_key']: r for r in csv.DictReader(open(f'{O}/inputs/aircraft_parameters.csv')) if r['performance_model_key']}
fl = dict(sqlite3.connect('file:/net/fs12/d1/shreya22/Parser/Output/aeic_input_v2/2025/oag_2025_cirium.sqlite?mode=ro', uri=True).execute('select f.performance_model_key, count(*) from schedules s join flights f on f.id=s.flight_id group by 1').fetchall())
rows = []
for k, r in new.items():
    a = tomllib.load(open(f'{O}/models/{k}.toml', 'rb')); b = tomllib.load(open(f'{V}/models/{k}.toml', 'rb'))
    rows.append((a['maximum_payload_kg'] / b['maximum_payload_kg'], k, b['maximum_payload_kg'], a['maximum_payload_kg'], float(r['oew_kg']), float(r['mtow_kg']), fl.get(k, 0), r['max_payload_comment'][:46]))
rows.sort()
print('ratio  old   new   OEW  MTOW  flights  comment | key (lowest 8, highest 8, and flights-weighted shares)')
for x in rows[:8] + rows[-8:]: print('%.2f %6d %6d %6d %6d %8d  %s | %s' % (x[0], x[2], x[3], x[4], x[5], x[6], x[7], x[1][:34]))
tot = sum(x[6] for x in rows); print('flight-weighted mean new/old: %.3f' % (sum(x[0] * x[6] for x in rows) / tot))
for lo, hi in ((0, .8), (.8, .95), (.95, 1.05), (1.05, 1.25), (1.25, 9)): print('ratio %.2f-%.2f: %d keys, %.1f%% of flights' % (lo, hi, sum(lo <= x[0] < hi for x in rows), 100 * sum(x[6] for x in rows if lo <= x[0] < hi) / tot))
print('payload + OEW > MTOW (impossible):', [(x[1][:30], x[3] + x[4], x[5]) for x in rows if x[3] + x[4] > x[5]][:6])
