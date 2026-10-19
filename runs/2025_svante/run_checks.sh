#!/bin/bash
O=/net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12
export PATH="/home/shreya22/.conda/envs/aeic/bin:$PATH"
cd /net/fs01/home/shreya22/AEIC
echo "== payload"; uv run python $O/scripts/check_payload.py
echo "== preflight"; uv run python $O/scripts/preflight.py $O /net/fs12/d1/shreya22/Parser/Output/aeic_input_v2/2025/oag_2025_cirium.sqlite
