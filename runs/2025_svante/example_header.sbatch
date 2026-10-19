#!/bin/bash
# One example file (January, case all), then print its header.
#SBATCH --job-name=aeicexample
#SBATCH --cpus-per-task=4
#SBATCH --mem=48G
#SBATCH --time=00:30:00
set -euo pipefail
O=/net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12
P=/net/fs01/home/shreya22/AEIC/.venv/bin/python
$P -I $O/scripts/build_cases.py 1 $O/cases_example all
$P -I - $O/cases_example/AEIC_2025_all_01.0.5x0.625.72L.nc <<'PY'
import sys, netCDF4 as nc, numpy as np
d = nc.Dataset(sys.argv[1]); print({k: len(v) for k, v in d.dimensions.items()}); print({a: d.getncattr(a) for a in d.ncattrs()})
for n, v in d.variables.items():
    s = f'{n} {v.dimensions} {v.dtype} {({a: v.getncattr(a) for a in v.ncattrs()})}'
    if v.ndim == 4:
        a = np.asarray(v[0]); s += f'  sum_over_area_kg_per_s={float((a * d["AREA"][:][None]).sum()):.4e} min={a.min():.2e} max={a.max():.2e}'
    print(s)
PY
