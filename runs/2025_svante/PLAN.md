# AEIC 2025 rerun, version v2026.10.12

(This replaces the old setup plan for the first run, which was written on 2026-10-06 and is now out
of date. It is based on the state of the repos, the run directory and the other chats' notes as
read on 2026-10-12.)

## Context

**v1 exists and is done.** `aeic_runs/2025_full_v1` (AEIC `9209977`, 580 models, mission DB
`aeic_input_v2`) flew 34,021,689 flights: 34,020,625 flown, 1,064 failed. It is merged, gridded
(lat-lon and C90) and analysed (`analysis_full/RESULTS.md`, `diagnose/FINDINGS.md`,
`followup/FOLLOWUP.md`).

**What the analysis of v1 found** (`diagnose/FINDINGS.md`):
- 1,045 mass-iteration failures (777-200, 747): the residual needs 6 iterations, the limit was 5.
- 346 flights with a starting mass below the empty mass, and 702,304 flights (2.06%, 9.7% of fuel)
  landing lighter than empty weight + payload: the starting mass is capped at the heaviest PIANO
  table mass, and the correction assumes it is not. About 0.3% of annual fuel is missing.
- 10 "route too short" and 9 ground-distance failures: the apex is sized without wind, and
  `lower_apex` over-corrects or reports a move it did not make.

**The fixes are already written**, as 9 unpushed commits on AEIC `svante-test` (`80de76c`..`3646691`,
from the other chat): wind in the distance functions, opt-in wind-aware apex, the `lower_apex` fix,
opt-in consistent starting mass with a payload-limited cap, `payload_mass` / `overweight_mass`
fields, and the options `--wind-aware-apex --consistent-starting-mass --max-mass-iterations
--when-overweight`. Validation replays are in `diagnose/val/` (latest 21:48 today).

**This rerun** uses those four options, the newest AEIC, PIANO-database and parser, and models
rebuilt with `--force` because the payloads changed to BADA values. The version is tagged
`v2026.10.12` in all three repos and pushed; the run goes in a new directory of the same name; one
run config file there records the versions and every setting.

Standing rules: nothing deleted, moved or overwritten without asking; no Python on the login node;
scripts on shared `/net` storage; hard failures; `aeic merge-stores --merge` only on a hard-link tree.

## State of the three repos (read-only check)

| Repo, branch | Local | Remote | Needed |
|---|---|---|---|
| AEIC `svante-test` | `3646691`, clean, 9 ahead of the fork (`6887d2a`) | fork `6887d2a` | test, push |
| PIANO-database `generated-aircraft-parameters` | `6fbc988` "Payload changed to match BADA values" | same | nothing |
| Parser `generate-aircraft-parameters` | `b0d7b3a` (+ untracked `config.local.yml`, `config.v2.yml`) | `bfcfe3d`, newer | fetch, fast-forward |

- New key table vs v1's: **same 580 keys**; `max_payload_basis` renamed `max_payload_comment`;
  payload values changed. AEIC reads only `max_payload_kg` (`piano_batch.py:395`), so the v2 mission
  database still matches the keys.
- What parser `bfcfe3d` changes is unknown until it is fetched.
- `aeic make-piano-models` has `--force` (`commands/make_piano_models.py:69`).

## Your question on smoke numbers ("27 of the 324 records")

That was the state on 2026-10-06, before v1. It was closed before v1 ran:
- The parser's `engine_sn_overrides.csv` (39 rows) covers every record in use, and the override is
  applied when the models are built. An engine with no nvPM data and a blank smoke number is a
  **build failure**, not a silent gap.
- v1 result: 580 of 580 models built; **0 smoke-number failures** in the 1,064 failures;
  **0 rows with NaN** in 34,020,625 trajectories.
- For this rerun the same guard applies. Step 4 checks it again, because the parser commit is new:
  every override row used, 580 built, 0 failed.

## Steps

### 1. Bring the repos to the newest version
1. Parser: `git fetch`; show the log and files of `b0d7b3a..bfcfe3d`; fast-forward.
   **Gate:** if it touches anything that feeds the mission database (lookup, key assignment, flight
   levels), stop and report: the database would need regenerating (into `aeic_input_v3`) first.
   If it is payload/table code only, keep the v2 database.
2. `parser/mappings/aircraft_parameters.csv` must equal `PIANO-database/data/aircraft-parameters.csv`
   byte for byte, and `engine_sn_overrides.csv` is compared with v1's copy (differences listed).
3. AEIC: read the validation summary in `diagnose/val/`; full `pytest` and `ruff` on `3646691` on a
   compute node; then `git push origin svante-test`.

### 2. Tag `v2026.10.12` (annotated) in all three and push
- AEIC fork, parser and PIANO-database (the last two are LAE repos on github.mit.edu), each on its
  current commit, each tree clean. The tag message names the other two SHAs.
- Refuse if the tag exists anywhere.

### 3. New run directory `/net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12/`
```
run_config.toml   tag, three SHAs, mission DB, models dir, every aeic run option, reservation
inputs/           aircraft_parameters.csv (new), engine_sn_overrides.csv; read-only copies
models/           580 TOMLs + selector config.toml
scripts/          run_day.sbatch, build_models.sh, launch scripts, checks
manifest.json     sha256 of every input and of run_config.toml, repo commits, clean state
test_day/  full/  logs/
```
- `run_config.toml` is the one place the settings live. A new copy of `run_day.sbatch` reads the
  options from it and fails without it (the original in `aeic_run_2025/` is not edited):
  `--builder adjustable --iterate-mass --iterate-ground-distance --descent-distance-from-model
  --wind-aware-apex --consistent-starting-mass --max-mass-iterations 10 --when-overweight cap`,
  load factors, and per-day MERRA2 weather as in v1.
- The job refuses to start unless AEIC's HEAD is the tagged commit and the tree is clean.

### 4. Build the models with `--force`
- `build_models.sh` from v1's, plus `--force`, into `models/`. Expect 580 built, 0 failed.
- Checks: each TOML's `maximum_payload` = the table's `max_payload_kg`; how many payloads changed
  from v1; UID, APU and smoke-number coverage as in the v1 preflight.

### 5. Preflight
- v1's `preflight.py` against the new models and the mission DB. Zero failed checks, report saved.

### 6. Test day 2025-01-15 (reservation `shreya`), then stop
- Same slicing as the real run, output in `test_day/`.
- Reported against v1's same day: failures by cause (v1: mass iteration, route too short, ground
  distance); flights payload-limited, trimmed or capped (`payload_mass`, `overweight_mass`); flights
  landing below empty weight + payload (should be 0); NaN scan; CO2 per kg fuel; fuel and emissions
  change; time and memory per task (wind-aware apex cost about +19% per flight in the prototype).
- **The full year starts only on your go-ahead.**

### 7. Full year in the reservation
- `sbatch -p hdr200 --reservation=shreya` (4 nodes c108-110, c117, 192 cores, until 2026-11-11):
  12 day-tasks of 16 cores at once; 365 days plus 2024-12-31, 2026-01-01, and 2025-10-31 on 2024 winds.
- Then as in v1: accounting, parallel store check, hard-link merge.
- The analysis scripts in `analysis_full/` read the store layout unchanged; `payload_mass` is new.

### 8. Gridding and the four GCHP emission cases

**Order (recommended): grid by class on lat-lon, add up the cases, convert to flux, regrid last.**

1. **Trajectories to lat-lon, once per aircraft class** (wide, narrow, small; the table has no
   freight class). Three runs of `grid_month.sbatch` (`--mem=200G`), each with a filter TOML listing
   that class's model keys (`Filter.performance_model_key`, built from `aircraft_class` in the
   table with a real CSV parser). Each flight is gridded exactly once.
2. **Check the partition:** wide + narrow + small must equal the store's trajectory and LTO totals
   per species and month (1e-6); every key is in exactly one class.
3. **Build the four cases by addition on lat-lon** (never subtraction, so no negative cells):
   all = W + N + S; no wide-bodies = N + S; no narrow-bodies = W + S; no small = W + N.
4. **Convert each case to the AEIC2019 layout GCHP's HEMCO already reads** (your choice): 0.5 x 0.625,
   model levels, `kg/m2/s` (mass / cell area / seconds in the month), one file per month and case.
   Reference file: `/net/geoschem/data/gcgrid/data/ExtData/HEMCO/AEIC2019/v2022-03/2019_monmean/`
   (variables `FUELBURN`, `NO2`, `CO`, `HC`, `BC`, ...; 361 latitudes with half-size polar cells,
   36 levels). Species names, the 360 -> 361 latitude layout, the level count and where LTO goes
   vertically are matched to that file and shown to you before the 48 files are written.
5. **C90 regrid only if you still want it, and last.** Why last: the gridder writes lat-lon only;
   regridding smooths and cannot be undone, so it should start from the finest grid; and it is
   linear, so regridding the three class files gives every case. Tool: v1's
   `scripts/regrid_to_c90.py` with the `gchp_regrid` environment and its saved weights (mass
   conserved to 1e-15 in v1).

*Note:* I found no GCHP emission files from another chat on disk or in the other chats' transcripts;
the only GCHP regrid I can find is v1's `grids_c90/` (made in this chat). If the other chat's output
is elsewhere, point me to it and I will build on that instead.

**What the MATLAB AEIC (github.mit.edu/LAE/AEIC) puts on the grid** (read from the copy at commit
`3115781`, `Model_Files/allocateResults.m`, `Sequence_World_Func.m`):
- Gridded fields: fuel burn, NOx, NO, NO2, HONO, HC, CO, nvPM mass (and number). Flight emissions
  above 3,000 ft plus **engine LTO**, placed in the airport's cell and spread over 0-3,000 ft
  (ground modes in the lowest level; initial climb to 1,400 ft; climb-out 1,400-3,000 ft; approach
  0-3,000 ft).
- **APU and GSE are computed and reported per airport but are not gridded** and not passed to the
  GEOS-Chem post-processor.
- So the matching choice for the cases is: flight + engine LTO, with the class removed from both;
  APU and GSE left out of the files (they stay in the store totals).

**A possible gap to check before any of this (not yet verified):** with `climb_descent_mode =
trajectory`, AEIC zeroes the LTO climb-out and approach modes (`emissions/lto.py:52`, "handled in
the trajectory"), but the trajectories start and end about 3,000 ft above the airport. If so, the
fuel and emissions of climb-out and approach below 3,000 ft are in neither part; the MATLAB code
keeps them. Step 6 measures this on the test day (LTO fuel by mode against the trajectory's first
and last altitudes). If confirmed it is your decision how to close it; I will not change it unasked.

## Verification
- Repos: clean, `git describe --tags` = `v2026.10.12`, tags on the remotes.
- Models: 580 built, payloads equal the table, every engine has usable nvPM or smoke numbers.
- Preflight: zero failures.
- Test day: every flight accounted for; no NaN; the three v1 failure classes gone or explained.
- Full run: flights in slices = 34,021,689; trajectories = flights minus counted failures; grids
  equal the store's trajectory CO2; C90 totals equal the lat-lon totals.

## Open points I will raise when reached, not decide myself
- Trent 772/768 equal split with `2RR023`/`2RR022` (unchanged from v1 unless the parser changed it).
- 53 airports with no elevation, flown at 0 m (your decision for v1 was: no change).
- The 346 777-200LR flights and other model-data issues listed in `FOLLOWUP.md`.

## Critical files
- AEIC: `commands/run_simulations.py`, `commands/make_piano_models.py`, `performance/piano_batch.py`
- `PIANO-database/data/aircraft-parameters.csv`; parser `mappings/aircraft_parameters.csv`, `engine_sn_overrides.csv`
- Reused from `aeic_runs/2025_full_v1/scripts/`: `build_models.sh`, `preflight.py`, `make_manifest.py`,
  `check_store.py`, `components.py`, `check_grids.py`, `regrid_to_c90.py`, `grid_month.sbatch`, `merge_store.sbatch`
- `aeic_run_2025/run_day.sbatch` (copied, not edited in place)

## Queued for later (added 2026-10-14)
After the class grids, partition check, example header and the 48 case files: take in PIANO PR #159 (Louis Robion, head 59bcf5b) on a new branch
off svante-test, in a separate worktree. Details in v2026.10.12/logs/RUN_EVENTS.md. The tag v2026.10.12 does not move.

## Status of the plan above (2026-10-14)
Steps 1-8 done. Full year: 0 failures; merged store 34,021,689 trajectories. Class grids: 20 months
re-run at 12 slices / 250 GB after OOM and timeouts. Partition check passed (CO2 trajectory +6e-7,
LTO within the store's rounding). 48 case files in `v2026.10.12/cases/` and 36 single-class files in
`class_flux/`, all checks passed (worst 1.2e-9 and 7e-8). Choices: 72 levels, 360 latitudes, all LTO in
level 1, APU/GSE excluded, OC and DISTANCE omitted, extras CO2/H2O/SO2/SO4/NVPM_N. **C90 not wanted**
(HEMCO regrids). Known limit: the 3,000 ft climb-out/approach gap (~3% fuel). Events in
`v2026.10.12/logs/RUN_EVENTS.md`.

---

# NEW SECTION (2026-10-14): take in PIANO PR #159's new commits on a new branch

## Context

Louis Robion pushed new commits to MIT-LAE/AEIC PR #159 (head `59bcf5b`, 12-13 Oct). `svante-test`
(`3646691`) is tagged `v2026.10.12` and was used for the run, so it must not change. Goal: say whether
the new commits merge and what they change, and carry them on a **new branch** off `svante-test`.

Already done, read-only: fetched the PR as `upstream/pr-159`. Of its 48 commits, 40 are already in
`svante-test` (rebased copies), 8 are not.

## What changed (the 8 commits)

| Commit | What | Take it? |
|---|---|---|
| `6b2e2ae` (26 Aug) | Splits the TOML writer into `_performance_model_toml.py` | **No**: superseded; the PR head itself no longer has that file (re-consolidated later, as `svante-test` already is) |
| `f9c355a` | `crossover_altitude(cas, mach)` in `utils/standard_atmosphere.py`: closed-form ISA crossover + tests | yes, applies cleanly |
| `81985f5` | Test fixture `climb.txt` made consistent (280 kt / M0.75 cross at 30,523 ft) | yes, clean |
| `e7ff9a9` | Climb reader warns when a PIANO header's stated crossover differs > 0.1% from its CAS/Mach | yes, clean |
| `285e355` | **The behaviour change.** Removes `--climb-crossover-altitude-ft` and `PianoOverrides.climb_crossover_altitude_ft`; climb schedule override is all-or-nothing (low CAS, high CAS, Mach); crossover always derived from high CAS + Mach; error if it falls below FL100 | yes, **conflicts** |
| `685d02e` | Docs for the above | yes |
| `1340155` | Test/doc cleanup, comments | yes |
| `59bcf5b` | Cruise reader: the "duplicate cruise row" warning only with `verbose=True` (less log noise) | yes |

**Can they merge?** Not as a plain merge (20 conflicting files, all from the rebased history). As
cherry-picks: the first three apply cleanly; `285e355` conflicts in `parsers/piano_reader/piano_data.py`.

**Why it conflicts, and what it breaks in our code:** `svante-test` has extra fields in
`PianoOverrides` that the PR doesn't (`descent_cas_low_kts`, `descent_cas_high_kts`, `descent_mach`,
`descent_crossover_altitude_ft`, from our descent-schedule work). And our
`performance/piano_batch.py:193-205` (`piano_overrides`, used by `aeic make-piano-models`) passes
`climb_crossover_altitude_ft=crossover_altitude_ft(cas_high, mach)`, the field the PR deletes. After
the cherry-pick, model building would fail with a TypeError until `piano_batch.py` is adapted.

**Does it change the models?** Probably by a hair only. Our `piano_batch.crossover_altitude_ft`
(`piano_batch.py:148`) already derives the climb crossover from the high CAS and Mach by bisection on
`cas_to_tas / speed_of_sound`; the PR does the same physics in closed form via
`altitude_from_pressure_isa_bada4`. Same quantity, different numerics: to be measured, not assumed.
Header warnings (e7ff9a9) may appear for some of the 580 PIANO files: information, not failure.

Nothing in the PR touches the trajectory builders, wind-aware apex, consistent starting mass,
`--when-overweight`, payload fields, emissions, LTO, gridding or the nvPM_N units fix.

## Steps (all in a separate worktree; the AEIC checkout and `svante-test` are never touched)

1. `git worktree add -b svante-test-pr159 /net/fs12/d1/shreya22/worktrees/AEIC-pr159 svante-test`
   (on /net so compute nodes see it; its own `.venv` via `uv sync`, or the main venv with
   `PYTHONPATH` pointing at the worktree's `src`).
2. Cherry-pick `f9c355a 81985f5 e7ff9a9 285e355 685d02e 1340155 59bcf5b` (not `6b2e2ae`) with `-x`
   so each records its PR origin.
3. Resolve `285e355` in `piano_data.py`: take the PR's climb change (drop
   `climb_crossover_altitude_ft`, new docstring), **keep our four `descent_*` fields**. In the same
   commit (so every commit builds), change `piano_batch.py:piano_overrides` to stop passing
   `climb_crossover_altitude_ft`. `crossover_altitude_ft` stays: the descent still uses it.
4. Tests and ruff on a compute node (reservation `shreya`): full `pytest`, `ruff check`, `ruff format --check`.
5. **Model comparison**: rebuild all 580 models with the new branch into a scratch dir (same inputs as
   `v2026.10.12/scripts/build_models.sh`, via `srun` in the reservation) and diff every TOML against
   `v2026.10.12/models/`: number of files that differ, climb crossover change per model (max in ft),
   any other field that changes, number of header-crossover warnings.
6. Report to you: commit list, the conflict resolution diff, test results, model diff.
   **No push, no tag, no change to `svante-test` without your go-ahead.** If you approve: push
   `svante-test-pr159` to the fork; a new tag only if you later run with it.

## Open point I'll raise, not decide
- Descent: the PR derives only the climb crossover; our descent crossover is already derived by
  `piano_batch`, so behaviour is consistent. Whether the reader should derive the descent one the
  same way (and drop `descent_crossover_altitude_ft`) is a question for Louis.

## Verification
- `git rev-parse svante-test v2026.10.12^{commit}` both still `3646691` afterwards; main checkout clean.
- New branch: 7 commits on top of `3646691`, each with `(cherry picked from commit …)`; tests and ruff green.
- 580 models build, 0 failed; TOML diff summary as in step 5.

## Critical files
- `src/AEIC/parsers/piano_reader/piano_data.py` (conflict), `climb_reader.py`, `cruise_reader.py`, `common.py`
- `src/AEIC/utils/standard_atmosphere.py`, `src/AEIC/commands/make_performance_model.py`
- `src/AEIC/performance/piano_batch.py` (`piano_overrides`, `crossover_altitude_ft`)
- `v2026.10.12/scripts/build_models.sh` (inputs for the rebuild), `v2026.10.12/models/` (comparison base)
