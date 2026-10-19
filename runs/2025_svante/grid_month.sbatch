#!/bin/bash
# Grid one month of one store: 16 parallel map slices, then reduce. Array index = month 1-12.
# Required (exported): STORE (.aeic-store), DB (mission DB the store's flight ids refer to),
#   OUT (output root), TAG (name for this store's files).  Optional: FILTER (filter TOML, e.g. the
#   exclude filter for the first run), GRID (default: the 72-level GEOS-Chem grid).
# Flights are placed in a month by their UTC departure day. The two UTC days outside 2025
# (2024-12-31 and 2026-01-01) are schedule rows of the 2025 schedule: they go into January and
# December, so the January file's period starts 2024-12-31 and December's ends 2026-01-01.
#SBATCH --job-name=aeicgrid
#SBATCH -p hdr,hdr200
#SBATCH --array=1-12
#SBATCH --cpus-per-task=16
#SBATCH --mem=96G
#SBATCH --time=12:00:00
#SBATCH --output=/net/fs12/d1/shreya22/Parser/Output/aeic_run_2025/day_logs/grid_%x_%A_%a.out
set -euo pipefail
ldd --version | head -1 || true
: "${STORE:?}"; : "${DB:?}"; : "${OUT:?}"; : "${TAG:?}"
AEIC=/net/fs01/home/shreya22/AEIC
GRID=${GRID:-$AEIC/src/AEIC/data/grids/geoschem-0.5x0.625.toml}
M=$SLURM_ARRAY_TASK_ID; MM=$(printf %02d "$M")
START=2025-$MM-01
END=$(date -u -d "$START + 1 month - 1 day" +%F)
if [ "$M" = 1 ]; then START=2024-12-31; fi
if [ "$M" = 12 ]; then END=2026-01-01; fi
SLICES=$SLURM_CPUS_PER_TASK
MAPS=$OUT/maps/$TAG/$MM; mkdir -p "$MAPS" "$OUT/$TAG"
rm -rf "$MAPS"/map-*.zarr
echo "$TAG month $MM: $START .. $END, $SLICES map slices"
pids=()
for ((i = 0; i < SLICES; i++)); do
  "$AEIC/.venv/bin/aeic" trajectories-to-grid --input-store "$STORE" --mission-db-file "$DB" \
    ${FILTER:+--filter-file "$FILTER"} --grid-file "$GRID" --mode map --map-prefix "$MAPS/map" \
    --start-date "$START" --end-date "$END" --slice-count "$SLICES" --slice-index "$i" \
    > "$MAPS/map_$i.log" 2>&1 &
  pids+=($!)
done
status=0
for pid in "${pids[@]}"; do wait "$pid" || status=1; done
[ "$status" = 0 ] || { echo "a map slice failed; see $MAPS/map_*.log" >&2; exit 1; }
"$AEIC/.venv/bin/aeic" trajectories-to-grid --input-store "$STORE" --mission-db-file "$DB" \
  --grid-file "$GRID" --mode reduce --map-prefix "$MAPS/map" --output-file "$OUT/$TAG/$TAG-2025-$MM.nc"
rm -rf "$MAPS"/map-*.zarr     # the intermediate slices are regenerable
ls -la "$OUT/$TAG/$TAG-2025-$MM.nc"
