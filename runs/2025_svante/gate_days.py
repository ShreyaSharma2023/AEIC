"""Hard check that the full year is complete before anything is merged. Exits non-zero on any failure; read-only.

For every UTC departure day from 2024-12-31 to 2026-01-01 (the 365 days of 2025 and the two schedule days outside it):
  - full/trajectories/DAY holds exactly SLICES files DAY-000.nc ... DAY-0NN.nc;
  - full/slice_logs/DAY holds slice_0.log ... and each has exactly one 'Slice N complete: 0 failed simulations.' line;
and no other day directories exist. Nothing is run twice: a day directory outside this range, or a trajectory file
with an unexpected name, is a failure.
usage: RUN_DIR=... gate_days.py"""
import datetime as dt
import glob
import os
import re
import sys
import tomllib

O = os.environ['RUN_DIR']
cfg = tomllib.load(open(f'{O}/run_config.toml', 'rb'))
SLICES = int(cfg['slurm']['cpus_per_task'])      # one aeic process per core: run_day.sbatch uses SLURM_CPUS_PER_TASK
FIRST, LAST = dt.date(2024, 12, 31), dt.date(2026, 1, 1)
days = [FIRST + dt.timedelta(days=i) for i in range((LAST - FIRST).days + 1)]
expected = {d.isoformat() for d in days}
bad = []

present = {os.path.basename(p) for p in glob.glob(f'{O}/full/trajectories/*') if os.path.isdir(p)}
if present - expected: bad.append(f'unexpected day directories: {sorted(present - expected)[:5]}')
if expected - present: bad.append(f'{len(expected - present)} days have no directory, e.g. {sorted(expected - present)[:5]}')

done = re.compile(r'Slice (\d+) complete: (\d+) failed simulations\.')
files_total = 0
for d in sorted(expected & present):
    want = {f'{d}-{i:03d}.nc' for i in range(SLICES)}
    have = {os.path.basename(p) for p in glob.glob(f'{O}/full/trajectories/{d}/*')}
    files_total += len(have & want)
    if have != want:
        bad.append(f'{d}: files differ from the {SLICES} expected (missing {sorted(want - have)[:3]}, extra {sorted(have - want)[:3]})')
    for i in range(SLICES):
        log = f'{O}/full/slice_logs/{d}/slice_{i}.log'
        if not os.path.exists(log):
            bad.append(f'{d}: no {log}')
            continue
        marks = done.findall(open(log, errors='replace').read())
        if len(marks) != 1 or int(marks[0][1]) != 0 or int(marks[0][0]) != i:
            bad.append(f'{d} slice {i}: completion lines {marks}')

print(f'{len(present)} day directories, {files_total} trajectory files (expected {len(days)} days x {SLICES})')
failing_days = sorted({m.group(0) for b in bad for m in [re.match(r'\d{4}-\d\d-\d\d', b)] if m})
if failing_days: print(f'{len(failing_days)} days with problems: {failing_days}')
print('DAY GATE', 'FAILED' if bad else 'PASSED')
for b in bad[:40]:
    print('  FAIL', b)
if len(bad) > 40:
    print(f'  ... and {len(bad) - 40} more')
sys.exit(1 if bad else 0)
