# 2025 Svante production run: recipe

Scripts that drove the AEIC 2025 global inventory on Svante (OAG 2025 schedule, PIANO
performance models, one SLURM array task per departure day). They were first imported
verbatim from `aeic_runs/v2026.10.12/scripts/` (commit "Import v2026.10.12 run recipe
verbatim"); later commits change them and appear as ordinary diffs.

## Conventions

- **`RUN_DIR` is required** and names the run directory, e.g.
  `/net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12`. Scripts fail immediately if it
  is unset; nothing falls back to a default.
- **The run directory is named after the AEIC git tag** it was produced with. `grid_launch.sh`
  refuses to run unless the AEIC checkout is exactly at the tag `basename $RUN_DIR`, with a
  clean tree.
- A run directory holds its own snapshot: `scripts/` (a copy of this directory),
  `run_config.toml` and `PLAN.md`. Jobs read `$RUN_DIR/scripts/*` and
  `$RUN_DIR/run_config.toml`, so a finished run keeps the exact recipe that produced it.
  `run_config.toml` and `PLAN.md` in this directory are the **v2026.10.12 record**: copy them
  into a new run directory and edit paths, tag and commits there.
- `#SBATCH --output` cannot expand variables, so `chk_store.sbatch` and `components.sbatch`
  have no `--output` line. Submit them with
  `sbatch --output=$RUN_DIR/logs/<name>_%j.out ...`.
- `merge_store.sbatch` and `grid_month.sbatch` still log to the shared
  `aeic_run_2025/day_logs/` directory.

## Order of a full run

1. `build_models.sh`: build the PIANO performance models (checks the AEIC commit against
   `run_config.toml`).
2. `launch.sh test`: one test day (2025-01-15); review with `run_review.sh`, `run_lto_gap.sh`.
3. `launch.sh full`: 365 days plus the special and edge days.
4. `link_tree.sh`, `merge_store.sbatch`, `chk_store.sbatch`, `components.sbatch`: merge and check the store.
5. `run_class_filters.sh`, `grid_launch.sh`: grid once per aircraft class.
6. `build_cases_species_mass.sbatch` (cases `all no_wide no_narrow no_small` into `cases_species_mass/`,
   `only_*` into `class_flux_species_mass/`), then `verify_cases_species_mass.py` and `partition_check.py`:
   the GCHP-ready case files. `nox_totals_check.py` prints the NOx and fuel totals as GCHP reads them.
   - NO and HONO from AEIC are NO2-equivalent masses; the case files hold true NO and HONO masses
     (converted by M(NO)/M(NO2), M(HONO)/M(NO2)). Check 5 of the verifier (NOx closure against AEIC's own
     NOx total) fails if that conversion is missing or applied twice.
   - The flux is over the calendar month (check 7); the UTC edge days 2024-12-31 and 2026-01-01 are folded
     into January and December (attribute `folded_edge_days`).
   - `build_cases.py`, `verify_cases.py` and `verify_class_flux.py` are superseded and refuse to run.
7. `make_manifest.py` (via `run_manifest*.sh`): write `manifest.json`.
