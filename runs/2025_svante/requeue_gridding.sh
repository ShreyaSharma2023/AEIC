#!/bin/bash
# Queue gridding (8 map processes per job), the case files and the final checks again, after the merge, store check and
# totals have completed. Used after the first gridding stage (16 map processes per job) ran out of memory.
#   requeue_gridding.sh        (RUN_DIR required; DRY=1 prints the commands and submits nothing)
: "${RUN_DIR:?set RUN_DIR to the run directory, e.g. /net/fs12/d1/shreya22/Parser/Output/aeic_runs/<tag>}"
set -euo pipefail
N=$RUN_DIR; S=$N/scripts; L=$N/logs; R=(-p hdr200 --reservation=shreya)
[ -e $N/merge_tree/merged/full_2025.aeic-store ] || { echo "no merged store" >&2; exit 1; }
chk=$(ls $L/chk_store_*.out | tail -1); comp=$(ls $L/components_*.out | tail -1)
grep -q "^files 5872 trajectories 34021689 rows with NaN 0 NaN fuel 0 negative values 0" $chk || { echo "store check not clean: $chk" >&2; exit 1; }
grep -q "^total fuel burn" $comp || { echo "components log incomplete: $comp" >&2; exit 1; }
for c in wide small narrow; do for f in $N/grids_class/$c/$c-2025-*.nc; do [ ! -e "$f" ] || { echo "grid output already exists: $f (move it aside first)" >&2; exit 1; }; done; done
sb() { sbatch --parsable "$@"; }
if [ -n "${DRY:-}" ]; then sb() { echo "sbatch $*" >&2; printf %s "$*" | cksum | awk '{print substr($1, 1, 5)}'; }; fi
export GRID_PARTITION=hdr200 GRID_RESERVATION=shreya GRID_CPUS=8
if [ -n "${DRY:-}" ]; then bash $S/grid_launch.sh --dry-run >&2; grid=2001:2002:2003
else grid=$(bash $S/grid_launch.sh | awk '/^Submitted batch job/ {print $NF}' | paste -sd:); fi
[ "$(echo "$grid" | tr ':' '\n' | wc -l)" = 3 ] || { echo "expected 3 gridding job ids, got '$grid'" >&2; exit 1; }
cases_a=$(OUTDIR=$N/cases_species_mass CASELIST="all no_wide no_narrow no_small" sb "${R[@]}" --array=1-12 --dependency=afterok:$grid -o $L/cases_%A_%a.out $S/build_cases_species_mass.sbatch)
cases_b=$(OUTDIR=$N/class_flux_species_mass CASELIST="only_wide only_narrow only_small" sb "${R[@]}" --array=1-12 --dependency=afterok:$grid -o $L/class_flux_%A_%a.out $S/build_cases_species_mass.sbatch)
final=$(sb "${R[@]}" --dependency=afterok:$cases_a:$cases_b -o $L/final_checks_%j.out $S/finish_checks.sbatch)
{ echo "requeued $(date -Is): gridding $grid (8 map processes per job), cases $cases_a, class flux $cases_b, final checks $final"; } | tee -a $L/queued_next_steps.log
