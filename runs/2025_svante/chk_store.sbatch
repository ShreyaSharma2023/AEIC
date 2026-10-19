#!/bin/bash
#SBATCH --job-name=aeicchk
#SBATCH -p hdr200
#SBATCH --reservation=shreya
#SBATCH --cpus-per-task=16
#SBATCH --mem=48G
#SBATCH --time=03:00:00
#SBATCH --output=/net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12/logs/chk_store_%j.out
/net/fs01/home/shreya22/AEIC/.venv/bin/python /net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12/scripts/check_store.py /net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12/merge_tree/merged/full_2025.aeic-store /net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12/full/check_store.json
