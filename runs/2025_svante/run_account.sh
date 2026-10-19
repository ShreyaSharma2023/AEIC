#!/bin/bash
: "${RUN_DIR:?set RUN_DIR to the run directory, e.g. /net/fs12/d1/shreya22/Parser/Output/aeic_runs/<tag>}"
export SLICE_LOGS=$RUN_DIR/full/slice_logs ACCOUNT_OUT=$RUN_DIR/full/run_accounting.json
/net/fs01/home/shreya22/AEIC/.venv/bin/python $RUN_DIR/scripts/account_run.py
