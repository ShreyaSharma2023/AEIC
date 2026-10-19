#!/bin/bash
: "${RUN_DIR:?set RUN_DIR to the run directory, e.g. /net/fs12/d1/shreya22/Parser/Output/aeic_runs/<tag>}"
# Grid the merged store once per aircraft class (wide, narrow, small), 12 monthly jobs each.   usage: grid_launch.sh [--dry-run]
# Run from inside the AEIC checkout (this script cd's there): the gridder records the git commit of its working directory.
# Wide and small go to the reservation, narrow to the normal queue. 200 GB per job (96 GB ran out of memory in the first run).
set -euo pipefail
O=$RUN_DIR
cd /net/fs01/home/shreya22/AEIC
git describe --tags --exact-match | grep -qx "$(basename "$RUN_DIR")" || { echo "AEIC is not at tag $(basename "$RUN_DIR")" >&2; exit 1; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "AEIC tree is not clean" >&2; exit 1; }
export STORE=$O/merge_tree/merged/full_2025.aeic-store DB=/net/fs12/d1/shreya22/Parser/Output/aeic_input_v2/2025/oag_2025_cirium.sqlite OUT=$O/grids_class
mkdir -p "$OUT"
sb() { if [ "${1:-}" = "--dry-run" ]; then shift; echo "sbatch $*"; else sbatch "$@"; fi; }
DRY=${1:-}
for cls in wide small; do
  TAG=$cls FILTER=$O/class_filters/$cls.toml sb ${DRY:+$DRY} -p hdr200 --reservation=shreya --mem=200G -J aeicgrid_$cls -o $O/logs/grid_${cls}_%A_%a.out $O/scripts/grid_month.sbatch
done
TAG=narrow FILTER=$O/class_filters/narrow.toml sb ${DRY:+$DRY} -p hdr,hdr200 --mem=200G -J aeicgrid_narrow -o $O/logs/grid_narrow_%A_%a.out $O/scripts/grid_month.sbatch
