#!/bin/bash
: "${RUN_DIR:?set RUN_DIR to the run directory, e.g. /net/fs12/d1/shreya22/Parser/Output/aeic_runs/<tag>}"
/net/fs01/home/shreya22/AEIC/.venv/bin/python $RUN_DIR/scripts/lto_gap.py ${1:-2025-01-15}
