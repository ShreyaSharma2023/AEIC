"""Review of a test day of v2026.10.12 against the first run (v1) on the same departure day. Read-only.

usage: review_day.py DAY          (default 2025-01-15)
New:  v2026.10.12/test_day/trajectories/DAY/*.nc      v1: 2025_full_v1/full/trajectories/DAY/*.nc
Reports: flights scheduled / flown / failed (and why), NaN and negative values, CO2 per kg fuel, payload decided by the builder
(payload_mass, reserve_shortfall, overweight_mass), flights landing lighter than empty weight + payload carried, fuel and emission
change against v1 flight by flight (by distance band), and the cruise level against the database level."""
import os
import glob, re, sqlite3, sys, tomllib, collections
from pathlib import Path
import numpy as np
import netCDF4 as nc

DAY = sys.argv[1] if len(sys.argv) > 1 else '2025-01-15'
R1 = Path('/net/fs12/d1/shreya22/Parser/Output/aeic_runs/2025_full_v1')
R2 = Path(os.environ['RUN_DIR'])
DB = '/net/fs12/d1/shreya22/Parser/Output/aeic_input_v2/2025/oag_2025_cirium.sqlite'
SRC = {'v1': (R1 / 'full/trajectories' / DAY, R1 / 'full/slice_logs' / DAY, R1 / 'models'),
       'new': (R2 / 'test_day/trajectories' / DAY, R2 / 'test_day/slice_logs' / DAY, R2 / 'models')}
SP = ['CO2', 'H2O', 'HC', 'CO', 'NOx', 'nvPM']

db = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
day0 = int(np.datetime64(DAY + 'T00:00:00', 's').astype(np.int64))
sched = {r[0]: (r[1], r[2]) for r in db.execute(
    'select s.id, f.performance_model_key, f.aircraft_type from schedules s join flights f on f.id = s.flight_id '
    'where s.departure_timestamp >= ? and s.departure_timestamp < ?', (day0, day0 + 86400))}
print(f'== {DAY}: {len(sched)} scheduled flights')

def last(arr): return np.array([a[-1] if len(a) else np.nan for a in arr], dtype=float)

def load(tag):
    files = sorted(glob.glob(str(SRC[tag][0] / f'{DAY}-[0-9][0-9][0-9].nc')))
    assert files, f'{tag}: no files in {SRC[tag][0]}'
    cols = collections.defaultdict(list); nan = neg = 0
    for f in files:
        d = nc.Dataset(f)
        sp = list(d['species'][:]); ix = [sp.index(s) for s in SP]
        te = np.asarray(d['emissions/total_emissions'][:], dtype=float)[:, ix]
        nan += int(np.isnan(te).sum()); neg += int((te < 0).sum())
        cols['id'].append(np.asarray(d['base/flight_id'][:])); cols['fuel'].append(np.asarray(d['emissions/total_fuel_burn'][:], dtype=float))
        cols['te'].append(te); cols['start'].append(np.asarray(d['base/starting_mass'][:], dtype=float))
        cols['end'].append(last(d['base/aircraft_mass'][:])); cols['km'].append(last(d['base/ground_distance'][:]) / 1000)
        cols['alt'].append(np.array([np.max(a) for a in d['base/altitude'][:]], dtype=float))
        cols['life'].append(np.asarray(d['emissions/lifecycle_co2'][:], dtype=float))
        for k in ('payload_mass', 'reserve_shortfall', 'overweight_mass'):
            cols[k].append(np.asarray(d[f'payload/{k}'][:], dtype=float) if 'payload' in d.groups else np.full(len(cols['id'][-1]), np.nan))
        d.close()
    out = {k: np.concatenate(v) for k, v in cols.items()}
    out['nan'], out['neg'], out['files'] = nan, neg, len(files)
    return out

def failures(tag):
    c = collections.Counter(); fl = fail = 0
    for f in sorted(glob.glob(str(SRC[tag][1] / 'slice_*.log'))):
        block = None
        for line in open(f, errors='replace'):
            if re.match(r'^\d{4}-\d\d-\d\d \d\d:', line):
                if block:
                    c[block] += 1; block = None
                if 'Error simulating mission' in line: block = 'NO-EXCEPTION-LINE'
                elif 'Flights to process in slice' in line: fl += int(line.rsplit(':', 1)[1])
                elif 'complete:' in line: fail += int(re.search(r'complete: (\d+) failed', line).group(1))
            elif block == 'NO-EXCEPTION-LINE':
                m = re.match(r'^(\w+(?:Error|Exception)): (.*)', line)
                if m:
                    msg = m.group(1) + ': ' + m.group(2); msg = re.sub(r'[-\d.e+]+', '#', msg)[:90]; block = msg
        if block: c[block] += 1
    return fl, fail, c

T = {}
for tag in ('v1', 'new'):
    T[tag] = load(tag)
    fl, fail, causes = failures(tag); t = T[tag]
    flown = set(t['id'].tolist())
    print(f'\n-- {tag}: {len(t["id"])} flown in {t["files"]} files; slice logs: attempted {fl}, reported failed {fail}; scheduled ids with no trajectory {len(set(sched) - flown)}; flown ids not scheduled that day {len(flown - set(sched))}')
    for k, v in causes.most_common(6): print(f'   {v:5d}  {k}')
    print(f'   NaN in total emissions {t["nan"]}, negative values {t["neg"]}')
    fuel = t['fuel'].sum(); co2 = t['te'][:, 0].sum() - t['life'].sum()
    print(f'   fuel {fuel / 1e9:.4f} Tg; operational CO2 {co2 / 1e12:.4f} Tg; CO2 per kg fuel {co2 / fuel:.1f} g/kg; NOx {t["te"][:, 4].sum() / 1e12:.5f} Tg; HC {t["te"][:, 2].sum() / 1e12:.6f} Tg; CO {t["te"][:, 3].sum() / 1e12:.5f} Tg; nvPM {t["te"][:, 5].sum() / 1e9:.3f} kt')

# models: empty mass and maximum payload per key
def model_mass(tag):
    cache = {}
    def f(key):
        if key not in cache:
            d = tomllib.load(open(SRC[tag][2] / f'{key}.toml', 'rb')); cache[key] = (d['operating_empty_mass_kg'], d['maximum_payload_kg'])
        return cache[key]
    return f

n = T['new']; mm = model_mass('new')
oew = np.array([mm(sched[i][0])[0] for i in n['id']]); pmax = np.array([mm(sched[i][0])[1] for i in n['id']])
pay = n['payload_mass']
print('\n== new: payload decided by the builder')
print(f'   payload fields present: {not np.isnan(pay).all()}')
if not np.isnan(pay).all():
    cut = pay < 0.999 * np.minimum(pmax, np.where(np.isnan(pay), 0, pmax))
    print(f'   payload_mass / maximum payload: median {np.nanmedian(pay / pmax):.3f}, min {np.nanmin(pay / pmax):.3f}, max {np.nanmax(pay / pmax):.3f}')
    print(f'   reserve_shortfall > 0: {int((n["reserve_shortfall"] > 0).sum())} flights; overweight_mass > 0: {int((n["overweight_mass"] > 0).sum())} flights (max {np.nanmax(n["overweight_mass"]):.0f} kg)')
    below = n['end'] < oew + pay - 1.0
    print(f'   landing mass < empty weight + payload carried: {int(below.sum())} flights ({100 * below.mean():.2f}%) (v1, whole year: 2.06%)')
    print(f'   starting mass < empty weight + payload: {int((n["start"] < oew + pay - 1.0).sum())}; starting mass < empty weight: {int((n["start"] < oew).sum())}')
    for lo, hi in ((0, 2000), (2000, 5000), (5000, 8000), (8000, 12000), (12000, 99999)):
        s = (n['km'] >= lo) & (n['km'] < hi)
        if s.any(): print(f'   {lo:5d}-{hi:5d} km: {int(s.sum()):6d} flights, payload_mass/max median {np.nanmedian(pay[s] / pmax[s]):.2f}, landing below empty+payload {int(below[s].sum())}')

v = T['v1']; mv = model_mass('v1')
common, iv, inn = np.intersect1d(v['id'], n['id'], return_indices=True)
print(f'\n== new vs v1, flight by flight ({len(common)} common flights; flown only in v1: {len(set(v["id"]) - set(n["id"]))}; only in new: {len(set(n["id"]) - set(v["id"]))})')
dist = n['km'][inn]; r = n['fuel'][inn] / v['fuel'][iv] - 1
print(f'   total fuel on common flights: v1 {v["fuel"][iv].sum() / 1e9:.4f} Tg, new {n["fuel"][inn].sum() / 1e9:.4f} Tg ({100 * (n["fuel"][inn].sum() / v["fuel"][iv].sum() - 1):+.2f}%)')
for sp, j in (('CO2', 0), ('NOx', 4), ('HC', 2), ('CO', 3), ('nvPM', 5)):
    a, b = n['te'][inn, j], v['te'][iv, j]
    if sp == 'CO2': a, b = a - n['life'][inn], b - v['life'][iv]
    print(f'   {sp:5s} total change {100 * (a.sum() / b.sum() - 1):+.2f}%')
print('   fuel change by distance band: n, median, p5, p95, share of v1 fuel, band total change')
for lo, hi in ((0, 500), (500, 2000), (2000, 5000), (5000, 8000), (8000, 12000), (12000, 99999)):
    s = (dist >= lo) & (dist < hi)
    if s.any(): print(f'   {lo:5d}-{hi:5d} km: {int(s.sum()):6d} {100 * np.median(r[s]):+6.2f}% {100 * np.percentile(r[s], 5):+7.2f}% {100 * np.percentile(r[s], 95):+7.2f}%  {100 * v["fuel"][iv][s].sum() / v["fuel"][iv].sum():5.1f}%  {100 * (n["fuel"][inn][s].sum() / v["fuel"][iv][s].sum() - 1):+6.2f}%')
big = np.argsort(-np.abs(r))[:5]
print('   largest fuel changes:', [(int(common[i]), sched[int(common[i])][1][:28], round(float(dist[i])), f'{100 * r[i]:+.1f}%') for i in big])
print('   failed in v1, flown now:', len((set(sched) - set(v['id'])) & set(n['id'])), '| flown in v1, failed now:', len(set(v['id']) - set(n['id'])))
