#!/bin/bash
# The step after the 367 day tasks: hard-check the whole year, account for every flight, hard-link the trajectories into
# merge_tree. Fails (and so stops everything queued behind it) if any day is missing, short, or reports failed flights.
#SBATCH --job-name=aeicgate
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=02:00:00
: "${RUN_DIR:?set RUN_DIR to the run directory, e.g. /net/fs12/d1/shreya22/Parser/Output/aeic_runs/<tag>}"
set -euo pipefail
PY=/net/fs01/home/shreya22/AEIC/.venv/bin/python
$PY $RUN_DIR/scripts/gate_days.py
bash $RUN_DIR/scripts/run_account.sh
mkdir -p $RUN_DIR/merge_tree                # link_tree.sh copies into merge_tree/trajectories and needs the directory
bash $RUN_DIR/scripts/link_tree.sh
