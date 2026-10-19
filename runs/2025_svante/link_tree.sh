#!/bin/bash
set -e
test ! -e /net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12/merge_tree/trajectories || { echo "merge_tree/trajectories exists, not touching"; exit 1; }
cp -al /net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12/full/trajectories /net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12/merge_tree/trajectories
echo "linked: $(find /net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12/merge_tree/trajectories -name '*.nc' | wc -l) files; originals: $(find /net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12/full/trajectories -name '*.nc' | wc -l)"
