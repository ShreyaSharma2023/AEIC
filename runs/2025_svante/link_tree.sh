#!/bin/bash
: "${RUN_DIR:?set RUN_DIR to the run directory, e.g. /net/fs12/d1/shreya22/Parser/Output/aeic_runs/<tag>}"
set -e
test ! -e $RUN_DIR/merge_tree/trajectories || { echo "merge_tree/trajectories exists, not touching"; exit 1; }
cp -al $RUN_DIR/full/trajectories $RUN_DIR/merge_tree/trajectories
echo "linked: $(find $RUN_DIR/merge_tree/trajectories -name '*.nc' | wc -l) files; originals: $(find $RUN_DIR/full/trajectories -name '*.nc' | wc -l)"
