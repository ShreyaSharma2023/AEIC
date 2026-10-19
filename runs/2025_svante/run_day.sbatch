#!/bin/bash
# One departure day of the 2025 schedule for version v2026.10.12: convert that day's weather, fly the
# day's flights in parallel slices, delete the weather. Array index 0 is 2025-01-01.
#
# Every setting comes from run_config.toml (RUN_CONFIG, required). Submit with
#   scripts/launch.sh   (it adds --reservation, the array range and the log paths)
# Environment overrides, as in the first run's script:
#   DAY_OVERRIDE         a day outside 2025 (schedule rows whose UTC date is 2024-12-31 or 2026-01-01)
#   SOURCE_YEAR_FORCE    use that year's winds for the day (2025-10-31 and 2026-01-01)
#   RUN_ROOT             output root instead of [paths] output_root (the test day uses test_day/)
#   FILTER_FILE          limit the run to the flights a filter TOML selects
#   SAMPLE, SEED         fly a random sample of the day's flights
# The job refuses to start unless the AEIC checkout is the tagged commit with a clean tree.
#SBATCH --job-name=aeicday
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=03:00:00

set -euo pipefail
ldd --version | head -1 || true   # hdr/hdr200 nodes have glibc 2.28; edr/fdr cannot load the wheels

: "${RUN_CONFIG:?RUN_CONFIG (path of run_config.toml) is required}"
test -f "$RUN_CONFIG" || { echo "no such run config: $RUN_CONFIG" >&2; exit 1; }
PY=/net/fs01/home/shreya22/AEIC/.venv/bin/python

# cfg SECTION.KEY -> value (string, number); cfg_args -> the aeic_run args, NUL separated.
cfg() { "$PY" -c 'import sys, tomllib
c = tomllib.load(open(sys.argv[1], "rb"))
v = c
for k in sys.argv[2].split("."): v = v[k]
print(v)' "$RUN_CONFIG" "$1"; }

TAG=$(cfg version.tag)
AEIC_DIR=$(cfg repos.aeic.path); WANT=$(cfg repos.aeic.commit)
HAVE=$(git -C "$AEIC_DIR" rev-parse HEAD)
[ "$HAVE" = "$WANT" ] || { echo "AEIC is at $HAVE, the run config wants $WANT ($TAG)" >&2; exit 1; }
[ -z "$(git -C "$AEIC_DIR" status --porcelain --untracked-files=no)" ] || { echo "AEIC working tree is not clean" >&2; exit 1; }
[ "$(git -C "$AEIC_DIR" describe --tags --exact-match 2>/dev/null)" = "$TAG" ] || { echo "AEIC HEAD is not tagged $TAG" >&2; exit 1; }

MODELS_DIR=$(cfg paths.models_dir); MISSION_DB=$(cfg paths.mission_db)
test -f "$MODELS_DIR/config.toml" || { echo "no config.toml in $MODELS_DIR" >&2; exit 1; }
test -f "$MISSION_DB" || { echo "no mission database $MISSION_DB" >&2; exit 1; }
MODEL_CACHE=$(ls "$MODELS_DIR"/*.toml | grep -vc "/config.toml$")
EDB=$(cfg paths.edb); LOAD_FACTORS=$(cfg paths.load_factors); MERRA2_SRC=$(cfg paths.merra2_source)
WEATHER_ROOT=$(cfg paths.weather_days); RUN_ROOT=${RUN_ROOT:-$(cfg paths.output_root)}
FILE_RES=$(cfg weather.file_resolution); DATA_RES=$(cfg weather.data_resolution)
SRC_YEAR=$(cfg weather.source_year); SRC_FROM_MONTH=$(cfg weather.source_year_for_months_from)
mapfile -d '' -t RUN_ARGS < <("$PY" -c 'import sys, tomllib
sys.stdout.write("\0".join(tomllib.load(open(sys.argv[1], "rb"))["aeic_run"]["args"]) + "\0")' "$RUN_CONFIG")

SLICES=$SLURM_CPUS_PER_TASK          # one process per core
DAY=${DAY_OVERRIDE:-$(date -u -d "2025-01-01 + ${SLURM_ARRAY_TASK_ID} days" +%Y-%m-%d)}
MONTH=$((10#${DAY:5:2}))

SOURCE_YEAR_ARGS=()
if [ "$MONTH" -ge "$SRC_FROM_MONTH" ] || [ -n "${SOURCE_YEAR_FORCE:-}" ]; then
  SOURCE_YEAR_ARGS=(--source-year "${SOURCE_YEAR_FORCE:-$SRC_YEAR}")
fi

WEATHER=$WEATHER_ROOT/$TAG/$DAY
STORE=$RUN_ROOT/trajectories/$DAY
LOGS=$RUN_ROOT/slice_logs/$DAY
mkdir -p "$WEATHER" "$STORE" "$LOGS"
trap 'rm -rf "$WEATHER"' EXIT        # the day's winds are regenerable; keep disk small

echo "version $TAG, AEIC $HAVE, day $DAY, $SLICES slices, source-year args: ${SOURCE_YEAR_ARGS[*]:-none}"
echo "aeic run args: ${RUN_ARGS[*]}"

"$AEIC_DIR/.venv/bin/aeic" make-weather-data merra2 \
  --source-dir "$MERRA2_SRC" \
  --output-dir "$WEATHER" --start "$DAY" --end "$DAY" "${SOURCE_YEAR_ARGS[@]}" --verify

cat > "$WEATHER/config.toml" <<TOML
engine_file = "$EDB"

[weather]
use_weather = true
weather_data_dir = "$WEATHER"
file_resolution = "$FILE_RES"
data_resolution = "$DATA_RES"
TOML

pids=()
for ((i = 0; i < SLICES; i++)); do
  "$AEIC_DIR/.venv/bin/aeic" run \
    --config-file "$WEATHER/config.toml" \
    --performance-selector-dir "$MODELS_DIR" \
    --model-cache-size "$MODEL_CACHE" \
    --mission-db-file "$MISSION_DB" \
    --output-store "$STORE/$DAY" \
    "${RUN_ARGS[@]}" \
    --load-factor-file "$LOAD_FACTORS" \
    ${FILTER_FILE:+--filter-file "$FILTER_FILE"} \
    ${SAMPLE:+--sample "$SAMPLE" --seed "${SEED:?SEED is required with SAMPLE}"} \
    --departure-date "$DAY" --slice-count "$SLICES" --slice-index "$i" \
    > "$LOGS/slice_$i.log" 2>&1 &
  pids+=($!)
done

status=0
for pid in "${pids[@]}"; do wait "$pid" || status=1; done
grep -h "complete:" "$LOGS"/slice_*.log | sed 's/.*Slice/Slice/' | sort -V
exit $status
