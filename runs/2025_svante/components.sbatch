#!/bin/bash
#SBATCH --job-name=aeiccomp
#SBATCH -p hdr200
#SBATCH --reservation=shreya
#SBATCH --cpus-per-task=16
#SBATCH --mem=48G
#SBATCH --time=03:00:00
: "${RUN_DIR:?set RUN_DIR to the run directory, e.g. /net/fs12/d1/shreya22/Parser/Output/aeic_runs/<tag>}"
export NPROC=16
/net/fs01/home/shreya22/AEIC/.venv/bin/python $RUN_DIR/scripts/components.py $RUN_DIR/merge_tree/merged/full_2025.aeic-store
