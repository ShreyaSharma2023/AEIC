#!/bin/bash
# Queue every step after the 367 day tasks on the reservation, each behind the one before it, and record the job ids.
#   queue_next_steps.sh DAYJOBS     DAYJOBS = colon-separated ids of the day-task jobs still running (e.g. 633376:635039)
# class filters (independent) -> gate (afterany the day jobs; it does the hard check itself) -> merge -> store check and
# components -> gridding of the 3 classes -> the 7 GCHP case sets -> final checks. A failing step leaves the ones behind it
# pending with 'DependencyNeverSatisfied': cancel them, fix, and resubmit from that step; nothing runs on a bad input.
: "${RUN_DIR:?set RUN_DIR to the run directory, e.g. /net/fs12/d1/shreya22/Parser/Output/aeic_runs/<tag>}"
set -euo pipefail
DAYJOBS=${1:?usage: queue_next_steps.sh DAYJOBS (colon-separated job ids)}
N=$RUN_DIR; S=$N/scripts; L=$N/logs
R=(-p hdr200 --reservation=shreya)
sb() { sbatch --parsable "$@"; }
# DRY=1: print the commands and submit nothing; the made-up job id of a command is a checksum of it, so who depends on whom shows
if [ -n "${DRY:-}" ]; then sb() { echo "sbatch $*" >&2; printf %s "$*" | cksum | awk '{print substr($1, 1, 5)}'; }; fi

filt=$(sb "${R[@]}" -c 2 --mem=16G -t 00:30:00 -J aeicfilt -o $L/class_filters_%j.out --wrap "bash $S/run_class_filters.sh")
gate=$(sb "${R[@]}" --dependency=afterany:$DAYJOBS -o $L/gate_%j.out $S/after_days.sbatch)
merge=$(sb "${R[@]}" --dependency=afterok:$gate --export=ALL,SRC=$N/merge_tree,NAME=full_2025 -o $L/merge_%j.out $S/merge_store.sbatch)
chk=$(sb "${R[@]}" --dependency=afterok:$merge -o $L/chk_store_%j.out $S/chk_store.sbatch)
comp=$(sb "${R[@]}" --dependency=afterok:$merge -o $L/components_%j.out $S/components.sbatch)
export GRID_PARTITION=hdr200 GRID_RESERVATION=shreya GRID_DEPENDENCY=afterok:$chk:$comp:$filt
if [ -n "${DRY:-}" ]; then bash $S/grid_launch.sh --dry-run >&2; grid=2001:2002:2003
else grid=$(bash $S/grid_launch.sh | awk '/^Submitted batch job/ {print $NF}' | paste -sd:); fi
[ "$(echo "$grid" | tr ':' '\n' | wc -l)" = 3 ] || { echo "expected 3 gridding job ids, got '$grid'" >&2; exit 1; }
cases_a=$(OUTDIR=$N/cases_species_mass CASELIST="all no_wide no_narrow no_small" sb "${R[@]}" --array=1-12 --dependency=afterok:$grid -o $L/cases_%A_%a.out $S/build_cases_species_mass.sbatch)
cases_b=$(OUTDIR=$N/class_flux_species_mass CASELIST="only_wide only_narrow only_small" sb "${R[@]}" --array=1-12 --dependency=afterok:$grid -o $L/class_flux_%A_%a.out $S/build_cases_species_mass.sbatch)
final=$(sb "${R[@]}" --dependency=afterok:$cases_a:$cases_b -o $L/final_checks_%j.out $S/finish_checks.sbatch)

{ echo "queued $(date -Is): filters $filt, gate $gate (after any of $DAYJOBS), merge $merge, store check $chk, components $comp,"
  echo "  gridding $grid, cases $cases_a, class flux $cases_b, final checks $final"; } | tee -a $L/queued_next_steps.log
