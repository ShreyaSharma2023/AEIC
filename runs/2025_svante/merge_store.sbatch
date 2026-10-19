#!/bin/bash
# Merge every day's slice files under $SRC/trajectories into one multi-file store
# $SRC/merged/$NAME.aeic-store. merge-stores --merge MOVES the slice files into the store.
#SBATCH --job-name=aeicmerge
#SBATCH -p hdr,hdr200
#SBATCH --cpus-per-task=2
#SBATCH --mem=48G
#SBATCH --time=12:00:00
#SBATCH --output=/net/fs12/d1/shreya22/Parser/Output/aeic_run_2025/day_logs/merge_%x_%j.out
set -euo pipefail
ldd --version | head -1 || true
: "${SRC:?set SRC}"; : "${NAME:?set NAME}"
mkdir -p "$SRC/merged"
shopt -s nullglob
FILES=("$SRC"/trajectories/*/*.nc)
echo "merging ${#FILES[@]} slice files from $SRC"
test "${#FILES[@]}" -gt 0
# merge-stores moves inputs into one directory by file name: refuse duplicates up front.
dups=$(printf "%s\n" "${FILES[@]}" | xargs -n1 basename | sort | uniq -d | head -3)
if [ -n "$dups" ]; then echo "duplicate file names, refusing to merge: $dups" >&2; exit 1; fi
time /net/fs01/home/shreya22/AEIC/.venv/bin/aeic merge-stores --allow-differing-runs --output-store "$SRC/merged/$NAME.aeic-store" --merge "${FILES[@]}"
ls "$SRC/trajectories"/*/slice-*.nc 2>/dev/null | wc -l | sed 's/^/slice files left behind: /'
du -sh "$SRC/merged/$NAME.aeic-store"
