#!/bin/bash
# Array 1-12: build the four case files of one month.  OUTDIR required.
#SBATCH --job-name=aeiccases
#SBATCH --cpus-per-task=4
#SBATCH --mem=48G
#SBATCH --time=01:00:00
: "${RUN_DIR:?set RUN_DIR to the run directory, e.g. /net/fs12/d1/shreya22/Parser/Output/aeic_runs/<tag>}"
set -euo pipefail
: "${OUTDIR:?}"; mkdir -p "$OUTDIR"
/net/fs01/home/shreya22/AEIC/.venv/bin/python -I $RUN_DIR/scripts/build_cases_species_mass.py "${SLURM_ARRAY_TASK_ID}" "$OUTDIR" ${CASELIST:-}
