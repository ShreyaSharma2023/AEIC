#!/bin/bash
: "${RUN_DIR:?set RUN_DIR to the run directory, e.g. /net/fs12/d1/shreya22/Parser/Output/aeic_runs/<tag>}"
export MANIFEST=manifest_launch.json
/net/fs01/home/shreya22/AEIC/.venv/bin/python $RUN_DIR/scripts/make_manifest.py "launch manifest: run_config.toml with the two-pool split; supersedes manifest.json (made before the split was added)" "test day 2025-01-15 reviewed: test_day/review_2025-01-15.txt"
