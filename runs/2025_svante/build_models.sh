#!/bin/bash
# Build the 580 PIANO performance models of version v2026.10.12 from the key table, with --force
# because the payloads changed. Run on a compute node:  srun --jobid=<job> -n1 scripts/build_models.sh
# Paths come from run_config.toml; the AEIC commit is checked first.
set -euo pipefail
O=/net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12
PY=/net/fs01/home/shreya22/AEIC/.venv/bin/python
cfg() { "$PY" -c 'import sys, tomllib
v = tomllib.load(open(sys.argv[1], "rb"))
for k in sys.argv[2].split("."): v = v[k]
print(v)' "$O/run_config.toml" "$1"; }
AEIC_DIR=$(cfg repos.aeic.path)
[ "$(git -C "$AEIC_DIR" rev-parse HEAD)" = "$(cfg repos.aeic.commit)" ] || { echo "AEIC is not at the commit of run_config.toml" >&2; exit 1; }
[ -z "$(git -C "$AEIC_DIR" status --porcelain --untracked-files=no)" ] || { echo "AEIC tree is not clean" >&2; exit 1; }
"$AEIC_DIR/.venv/bin/aeic" make-piano-models --keys-from-table \
  --airframe-table "$(cfg paths.aircraft_parameters)" \
  --piano-root "$(cfg paths.piano_root)" \
  --engine-file "$(cfg paths.edb)" \
  --sn-override-file "$(cfg paths.sn_overrides)" \
  --output-dir "$(cfg paths.models_dir)" --force 2>&1 | tee "$O/logs/build_models.log" | grep -E "built|FAILED|failed" || true
cat > "$(cfg paths.models_dir)/config.toml" <<'TOML'
# Selector configuration for the v2026.10.12 PIANO models.
#
# Every flight in the mission database carries a performance_model_key that names one of
# the <key>.toml files here, so no aircraft-type synonyms and no default model are set.
# A flight with no model is skipped with a warning.
TOML
echo "models: $(ls "$(cfg paths.models_dir)"/*.toml | grep -vc '/config.toml$')"
