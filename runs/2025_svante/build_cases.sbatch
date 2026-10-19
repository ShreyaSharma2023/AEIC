#!/bin/bash
# Array 1-12: build the four case files of one month.  OUTDIR required.
#SBATCH --job-name=aeiccases
#SBATCH --cpus-per-task=4
#SBATCH --mem=48G
#SBATCH --time=01:00:00
set -euo pipefail
: "${OUTDIR:?}"; mkdir -p "$OUTDIR"
/net/fs01/home/shreya22/AEIC/.venv/bin/python -I /net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12/scripts/build_cases.py "${SLURM_ARRAY_TASK_ID}" "$OUTDIR" ${CASELIST:-}
