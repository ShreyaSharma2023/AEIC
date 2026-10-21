#!/bin/bash
: "${RUN_DIR:?set RUN_DIR to the run directory, e.g. /net/fs12/d1/shreya22/Parser/Output/aeic_runs/<tag>}"
# Grid the merged store once per aircraft class (wide, narrow, small), 12 monthly jobs each.   usage: grid_launch.sh [--dry-run]
# Run from inside the AEIC checkout (this script cd's there): the gridder records the git commit of its working directory.
# Resources (from the v2026.10.12 gridding, sacct): each job runs 16 map processes and peaks at ~240-260 GB (MaxRSS), almost a whole
# 257 GB hdr/hdr200 node, and reads ~0.85-1.1 TB from the file server. With a 200 GB cap 18 of 36 jobs were killed or timed out, on hdr
# and hdr200 nodes alike; with 250 GB all finished, but slowed ~5x (17 min -> 1.5 h per job) while many ran at once. So: 250 GB and
# --exclusive (one job per node, nothing else competing for its memory), at most GRID_PARALLEL (default 2) months of a class at a time
# (6 jobs, 6 nodes, ~6 TB being read at once instead of 36 jobs). By default all classes go to hdr,hdr200 (the whole pool);
# v2026.10.19 ran them on the reservation (GRID_PARTITION=hdr200 GRID_RESERVATION=shreya, 6 of its 8 nodes).
set -euo pipefail
O=$RUN_DIR
cd /net/fs01/home/shreya22/AEIC
git describe --tags --exact-match | grep -qx "$(basename "$RUN_DIR")" || { echo "AEIC is not at tag $(basename "$RUN_DIR")" >&2; exit 1; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "AEIC tree is not clean" >&2; exit 1; }
export STORE=$O/merge_tree/merged/full_2025.aeic-store DB=/net/fs12/d1/shreya22/Parser/Output/aeic_input_v2/2025/oag_2025_cirium.sqlite OUT=$O/grids_class
mkdir -p "$OUT"
sb() { if [ "${1:-}" = "--dry-run" ]; then shift; echo "sbatch $*"; else sbatch "$@"; fi; }
DRY=${1:-}
PAR=${GRID_PARALLEL:-2}
# Map processes per job = GRID_CPUS (default 8; grid_month.sbatch runs one map process per CPU). With 16, memory grew to
# ~260 GB within the first hour (about 16 GB per process: sstat showed 130 GB after 3 min) and 5 of the first 6 jobs of
# v2026.10.19 were killed at the 250 GB cap. With 8 the peak is about half; the map step itself takes only 6-11 min.
# Where and after what: GRID_PARTITION (default hdr,hdr200), GRID_RESERVATION (default none), GRID_DEPENDENCY
# (an sbatch --dependency value, e.g. afterok:123:124; default none).
WHERE=(-p "${GRID_PARTITION:-hdr,hdr200}")
[ -z "${GRID_RESERVATION:-}" ] || WHERE+=(--reservation="$GRID_RESERVATION")
[ -z "${GRID_DEPENDENCY:-}" ] || WHERE+=(--dependency="$GRID_DEPENDENCY")
for cls in wide small narrow; do
  TAG=$cls FILTER=$O/class_filters/$cls.toml sb ${DRY:+$DRY} "${WHERE[@]}" --exclusive --mem=250G --cpus-per-task=${GRID_CPUS:-8} --array=1-12%$PAR -J aeicgrid_$cls -o $O/logs/grid_${cls}_%A_%a.out $O/scripts/grid_month.sbatch
done
