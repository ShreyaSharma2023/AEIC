"""Size of the possible gap: fuel and emissions of the ICAO climb (2.2 min) and approach (4 min) modes, which AEIC zeroes when
climb_descent_mode = trajectory. Estimated from the models and the schedule of one day (flights all flown in the test day):
sum over flights of time-in-mode x fuel flow (x EI). Read-only."""
import sqlite3, tomllib, collections, sys
O = '/net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12'
DAY = sys.argv[1] if len(sys.argv) > 1 else '2025-01-15'
import numpy as np
t0 = int(np.datetime64(DAY + 'T00:00:00', 's').astype(np.int64))
db = sqlite3.connect('file:/net/fs12/d1/shreya22/Parser/Output/aeic_input_v2/2025/oag_2025_cirium.sqlite?mode=ro', uri=True)
n = collections.Counter(dict(db.execute('select f.performance_model_key, count(*) from schedules s join flights f on f.id=s.flight_id where s.departure_timestamp >= ? and s.departure_timestamp < ? group by 1', (t0, t0 + 86400))))
T = {'approach': 4.0 * 60, 'climb': 2.2 * 60}
tot = collections.Counter(); per_mode = collections.defaultdict(collections.Counter)
for key, c in n.items():
    d = tomllib.load(open(f'{O}/models/{key}.toml', 'rb'))['LTO_performance']['mode_data']
    for mode, t in T.items():
        m = d[mode]; fuel = m['fuel_kgs'] * t * c
        per_mode[mode]['fuel_kg'] += fuel
        for sp, ei in (('NOx', 'EI_NOx'), ('HC', 'EI_HC'), ('CO', 'EI_CO')): per_mode[mode][sp + '_g'] += fuel * m[ei]
for mode in T:
    for k, v in per_mode[mode].items(): tot[k] += v
flights = sum(n.values())
print(f'{DAY}: {flights} flights')
for mode in T: print(f'  {mode:9s} fuel {per_mode[mode]["fuel_kg"] / 1e3:8.1f} t  ({per_mode[mode]["fuel_kg"] / flights:6.1f} kg/flight)')
print(f'  both      fuel {tot["fuel_kg"] / 1e3:8.1f} t; CO2 {3155.6 * tot["fuel_kg"] / 1e6:8.1f} t; NOx {tot["NOx_g"] / 1e6:7.2f} t; HC {tot["HC_g"] / 1e6:7.3f} t; CO {tot["CO_g"] / 1e6:7.2f} t')
# the test day's totals (review_2025-01-15.txt): fuel 0.6158 Tg, CO2 operational 1.9433 Tg, NOx 0.01044 Tg, HC 0.000119 Tg, CO 0.00141 Tg
ref = {'fuel': 0.6158e12, 'CO2': 1.9433e12, 'NOx': 0.01044e12, 'HC': 0.000119e12, 'CO': 0.00141e12}
print('  as a share of the test day totals: fuel %.2f%%, CO2 %.2f%%, NOx %.2f%%, HC %.1f%%, CO %.1f%%' % (
    100 * tot['fuel_kg'] * 1e3 / ref['fuel'], 100 * 3155.6 * tot['fuel_kg'] / ref['CO2'], 100 * tot['NOx_g'] / ref['NOx'], 100 * tot['HC_g'] / ref['HC'], 100 * tot['CO_g'] / ref['CO']))
