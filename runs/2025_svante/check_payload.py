"""Payload check of the v2026.10.12 models: each model's maximum_payload_kg equals the table's max_payload_kg
(no rounding surprises), and how the payloads differ from the first run's (v1) models and table. Read-only."""
import os
import csv, sys, tomllib, collections
from pathlib import Path
O = Path(os.environ['RUN_DIR']); V = Path('/net/fs12/d1/shreya22/Parser/Output/aeic_runs/2025_full_v1')
new = {r['performance_model_key']: r for r in csv.DictReader(open(O / 'inputs/aircraft_parameters.csv')) if r['performance_model_key']}
old = {r['performance_model_key']: r for r in csv.DictReader(open(V / 'inputs/aircraft_parameters.csv')) if r['performance_model_key']}
assert set(new) == set(old), 'key sets differ'
bad, changed, ratios, cls = [], 0, [], collections.Counter()
for k, r in new.items():
    m = tomllib.load(open(O / 'models' / f'{k}.toml', 'rb'))['maximum_payload_kg']
    if m != int(float(r['max_payload_kg'])): bad.append((k, m, r['max_payload_kg']))
    mo = tomllib.load(open(V / 'models' / f'{k}.toml', 'rb'))['maximum_payload_kg']
    if mo != m: changed += 1; ratios.append(m / mo); cls[r['aircraft_class']] += 1
print('models with payload != table:', len(bad), bad[:5])
print('models whose payload changed from v1:', changed, 'of', len(new), '| by class', dict(cls))
if ratios: print('new/old payload: min %.2f median %.2f max %.2f' % (min(ratios), sorted(ratios)[len(ratios) // 2], max(ratios)))
print('payload comment values:', collections.Counter(r['max_payload_comment'].split(':')[0].split(' ')[0] + ' ' + (r['max_payload_comment'].split(' ')[1] if ' ' in r['max_payload_comment'] else '') for r in new.values()).most_common(6))
sys.exit(1 if bad else 0)
