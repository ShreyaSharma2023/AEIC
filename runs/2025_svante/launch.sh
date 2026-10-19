#!/bin/bash
# Launch the v2026.10.12 run.   usage: launch.sh test | full [--dry-run]
#   test: 2025-01-15 only, output in test_day/ (review before the full year)
#   full: 365 days + 2025-10-31 (2024 winds) + the two days outside 2025, output in full/,
#         split between the reservation and the normal queue (see [slurm] in run_config.toml)
# All settings are in run_config.toml; --dry-run prints the sbatch commands instead of running them.
set -euo pipefail
O=/net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12
PY=/net/fs01/home/shreya22/AEIC/.venv/bin/python
cfg() { "$PY" -c 'import sys, tomllib
v = tomllib.load(open(sys.argv[1], "rb"))
for k in sys.argv[2].split("."): v = v[k]
print(v)' "$O/run_config.toml" "$1"; }
export RUN_CONFIG=$O/run_config.toml
DRY=${2:-}
sb() { if [ "$DRY" = "--dry-run" ]; then echo "sbatch $*"; else sbatch "$@"; fi; }
CPUS=$(cfg slurm.cpus_per_task); MEM=$(cfg slurm.mem); TIME=$(cfg slurm.time)
PART=$(cfg slurm.partition); RES=$(cfg slurm.reservation)
COMMON=(-p "$PART" --reservation="$RES" --cpus-per-task="$CPUS" --mem="$MEM" --time="$TIME")
S=$O/scripts/run_day.sbatch; L=$O/logs
case "${1:?usage: launch.sh test|full}" in
  test)
    RUN_ROOT=$O/test_day sb "${COMMON[@]}" -J aeictest --array=14 -o $L/test_%A_%a.out -e $L/test_%A_%a.err $S ;;
  full)
    export RUN_ROOT=$(cfg paths.output_root)
    NQ=$(cfg slurm.normal_queue.partition); NPAR=$(cfg slurm.normal_queue.parallel_days_per_array)
    NCOMMON=(-p "$NQ" --cpus-per-task="$CPUS" --mem="$MEM" --time="$TIME")
    # reservation pool: every third day starting at 2025-01-01, except index 303 (2025-10-31)
    sb "${COMMON[@]}" -J aeicres --array=0-300:3 -o $L/res_%A_%a.out -e $L/res_%A_%a.err $S
    sb "${COMMON[@]}" -J aeicres --array=306-364:3 -o $L/res_%A_%a.out -e $L/res_%A_%a.err $S
    # normal-queue pool: the other two thirds, two arrays
    sb "${NCOMMON[@]}" -J aeicnq --array=1-364:3%$NPAR -o $L/nq_%A_%a.out -e $L/nq_%A_%a.err $S
    sb "${NCOMMON[@]}" -J aeicnq --array=2-364:3%$NPAR -o $L/nq_%A_%a.out -e $L/nq_%A_%a.err $S
    # special days, normal queue
    SOURCE_YEAR_FORCE=2024 sb "${NCOMMON[@]}" -J aeicnq --array=303 -o $L/nq_%A_%a.out -e $L/nq_%A_%a.err $S
    DAY_OVERRIDE=2024-12-31 sb "${NCOMMON[@]}" -J aeicedge --array=0 -o $L/edge_%A.out -e $L/edge_%A.err $S
    DAY_OVERRIDE=2026-01-01 SOURCE_YEAR_FORCE=2024 sb "${NCOMMON[@]}" -J aeicedge --array=0 -o $L/edge_%A.out -e $L/edge_%A.err $S ;;
  special)
    # the three special days, in the reservation (used after the reservation grew to 8 nodes)
    export RUN_ROOT=$(cfg paths.output_root)
    SOURCE_YEAR_FORCE=2024 sb "${COMMON[@]}" -J aeicres --array=303 -o $L/res_%A_%a.out -e $L/res_%A_%a.err $S
    DAY_OVERRIDE=2024-12-31 sb "${COMMON[@]}" -J aeicedge --array=0 -o $L/edge_%A.out -e $L/edge_%A.err $S
    DAY_OVERRIDE=2026-01-01 SOURCE_YEAR_FORCE=2024 sb "${COMMON[@]}" -J aeicedge --array=0 -o $L/edge_%A.out -e $L/edge_%A.err $S ;;
  days)
    # an explicit set of days (array indices in DAYS, e.g. DAYS=284-362:3) in the reservation; DAYS is required
    export RUN_ROOT=$(cfg paths.output_root)
    sb "${COMMON[@]}" -J aeicres --array="${DAYS:?DAYS is required}" -o $L/res_%A_%a.out -e $L/res_%A_%a.err $S ;;
  *) echo "usage: launch.sh test|full|special|days [--dry-run]" >&2; exit 1 ;;
esac
