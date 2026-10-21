#!/bin/bash
# The last step: every check on the gridded inventory and the GCHP case files. Each runs even if an earlier one fails, so
# one pass shows everything that is wrong; the job fails if any did.
#SBATCH --job-name=aeicfinal
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=04:00:00
: "${RUN_DIR:?set RUN_DIR to the run directory, e.g. /net/fs12/d1/shreya22/Parser/Output/aeic_runs/<tag>}"
PY=/net/fs01/home/shreya22/AEIC/.venv/bin/python
status=0
echo "=== store check log"; chk=$(ls $RUN_DIR/logs/chk_store_*.out | tail -1); grep -E "^files " $chk | tee /dev/stderr | grep -q "trajectories 34021689 rows with NaN 0 NaN fuel 0 negative values 0" \
  && echo "STORE COUNT OK: 34,021,689 trajectories, no NaN, no negative values" || { echo "STORE COUNT FAILED"; status=1; }
for step in partition_check verify_cases_species_mass nox_totals_check; do
  echo; echo "=== $step"; $PY $RUN_DIR/scripts/$step.py || { echo "$step FAILED"; status=1; }
done
echo; [ $status = 0 ] && echo "ALL CHECKS PASSED" || echo "SOME CHECKS FAILED"
exit $status
