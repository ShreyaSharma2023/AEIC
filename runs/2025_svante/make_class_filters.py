"""Write one filter TOML per aircraft class (wide, narrow, small) listing the model keys of that class, read from the models'
own aircraft_class field. Hard failures: a class other than those three, a key in no class or in two, an existing output file.
Also prints keys and flights per class (from the mission database)."""
import glob, sqlite3, tomllib, collections, sys
from pathlib import Path
O = Path('/net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12'); out = O / 'class_filters'; out.mkdir(exist_ok=True)
CLASSES = ('wide', 'narrow', 'small')
by = collections.defaultdict(list)
for p in sorted(glob.glob(str(O / 'models/*.toml'))):
    if p.endswith('/config.toml'): continue
    c = tomllib.load(open(p, 'rb'))['aircraft_class']
    if c not in CLASSES: sys.exit(f'{p}: aircraft_class {c!r} is not one of {CLASSES}')
    by[c].append(Path(p).stem)
allkeys = [k for c in CLASSES for k in by[c]]
assert len(allkeys) == len(set(allkeys)) == 580, (len(allkeys), len(set(allkeys)))
db = sqlite3.connect('file:/net/fs12/d1/shreya22/Parser/Output/aeic_input_v2/2025/oag_2025_cirium.sqlite?mode=ro', uri=True)
fl = dict(db.execute('select f.performance_model_key, count(*) from schedules s join flights f on f.id=s.flight_id group by 1'))
assert set(fl) - {None} <= set(allkeys), 'database keys without a model'
assert None not in fl, 'flights with no model key'
for c in CLASSES:
    f = out / f'{c}.toml'; assert not f.exists(), f'{f} exists, not overwriting'
    f.write_text('performance_model_key = [\n' + ''.join(f'  "{k}",\n' for k in by[c]) + ']\n')
    n = sum(fl.get(k, 0) for k in by[c]); print(f'{c:7s} keys {len(by[c]):4d} (with flights {sum(1 for k in by[c] if k in fl):3d})  flights {n:9d} ({100 * n / sum(fl.values()):.1f}%)')
print('total flights', sum(fl.values()))
