# AEIC 2025 rerun v2026.10.19

Same schedule, mission database, inputs, PIANO models and `aeic run` options as v2026.10.12
(`../v2026.10.12/PLAN.md`). AEIC is at tag v2026.10.19 (`aa1d6a0`, svante-test). The parser
(`bfcfe3d`) and PIANO-database (`6fbc988`) commits are unchanged and carry the same tag.
The recipe is `scripts/`, a copy of `AEIC/runs/2025_svante` at the tag (see its README).

## Why the rerun

1. **The 0-3,000 ft gap.** In `climb_descent_mode = trajectory`, AEIC zeroed the ICAO
   climb-out and approach modes, assuming the trajectory covers take-off and landing. The
   adjustable builder starts climb and ends descent 914 m above the airports (every flight
   of the v2026.10.12 test day), so the flight below was in neither part: about 2.9% of fuel
   and 3.0% of NOx, all near the surface. v2026.10.19 keeps both modes and grids them as
   vertical columns from the airport to 914 m (levels 1-7). Take-off and idle stay in level 1.
2. **January and December fluxes.** The UTC departure days 2024-12-31 and 2026-01-01 are
   folded into January and December, but v2026.10.12's case files divided those months by
   32 days while GCHP applies them over 31: about 3% low. The flux is now over the calendar
   month (`folded_edge_days` records the folded day).

## What to expect against v2026.10.12

- Trajectory fuel and emissions: identical per flight.
- LTO: climb-out and approach added, about +2.9% of total fuel and +3.0% of NOx.
- Annual case-file totals: up by the LTO addition. January and December are also up by 32/31
  on top of that.

## Use in GCHP (`~/14.7.1/Av_*`)

The Jul-Dec 2024 spin-up is run with v2026.10.12. At 2025-01-01, `AviationEmis` is re-pointed
to `v2026.10.19/cases_species_mass`, so the 2025 analysis year uses this run.

## Deviations of scripts/ from the tag

- `scripts/make_manifest.py`: at the tag, `import os` came after the first use of `os`, so it
  crashed. This copy has the import moved up. It only writes provenance (`manifest.json`), not
  results. The same fix is committed to AEIC after the run (stash "post-run: make_manifest.py ...").
- `scripts/compare_test_days.py`: added after the tag (read-only analysis); see step 2.
- `scripts/grid_launch.sh`: resources changed for the gridding step, after what happened in v2026.10.12
  (sacct, jobs 626492-626494 and 627052-627054): the 36 monthly jobs ran at once, each with 16 map processes
  peaking at ~240-260 GB (MaxRSS) and reading ~0.85-1.1 TB from the file server. With `--mem=200G`, 18 of 36 jobs
  were killed (OOM) or timed out, on hdr (c001, c004, c036) and hdr200 nodes alike, so it was not specific nodes; the 250 GB
  reruns finished but one job took 1.5 h while many ran together and 17 min when few did (I/O contention).
  Now: 250 GB, `--exclusive` (one job per node), arrays throttled to `GRID_PARALLEL` (default 2) months per class,
  and all three classes on `hdr,hdr200` rather than the 8 reserved nodes. Not changed: the job script itself,
  the 16 map processes per job. Check MaxRSS and elapsed time of the new jobs and adjust `GRID_PARALLEL`.
  The same edit is committed to AEIC after the run.
- `scripts/partition_check.py`: it read the store totals from `logs/components_626168.out`, the components job of
  v2026.10.12 (the job id is in the file name), which does not exist in this run. It now reads the one
  `logs/components_*.out` of this run and stops if there is not exactly one. Committed to AEIC after the run.
- `scripts/partition_check.py` (second change): it compared the 3-D grids with the store's trajectory CO2 and the surface LTO grid with
  the store's LTO CO2. Since the climb-out and approach columns went into the 3-D grids, those two comparisons fail by +22.63 and
  -22.63 Tg (job 635431). The check now compares the sum of both grids with the store's trajectory plus LTO and reports the split.
  The split agrees with the test day's per-mode CO2 (`scripts/lto_modes.py`): idle+take-off 52.2%, climb-out+approach 47.8% of LTO;
  gridded surface 52.2%, columns 47.8%. Committed to AEIC after the run.

## Events during the full-year run

- Array tasks 58 (2025-02-28) and 211 (2025-07-31) of job 633375 hit the 3-hour limit on `c034`, with 13 and 14 of
  16 slices finished. The `c030`-`c035` group of `hdr` nodes averaged 102-108 min per day against 60-76 on the
  others (the test day on the reserved hdr200 nodes took 35 min), so these two days were just over the limit. Their
  partial outputs and slice logs were moved, not deleted, to `full/superseded_timeout_633375/`; their leftover wind
  files were removed (regenerable); both days were rerun as job 635039 on the reservation with a 6-hour limit.
  See `logs/redo_timeouts.log`.
- The last five tasks of array 633376 (indices 350, 353, 356, 359, 362 = 2025-12-17, -20, -23, -26, -29) were still
  pending behind the 12-task throttle while the reservation was idle. They were cancelled before they started and
  resubmitted on the reservation as job 635045 (no output existed for those days). Same script, config and
  commit as every other day.

## Queued post-run chain (2026-10-20, `scripts/queue_next_steps.sh`, log `logs/queued_next_steps.log`)

All on the reservation (`hdr200`), each step behind the one before; a failing step leaves the rest pending with
`DependencyNeverSatisfied` instead of running on bad input.

| Job | Step | Waits for |
|---|---|---|
| 635060 | class filters (`make_class_filters.py`) | nothing |
| 635061 | gate: `gate_days.py` (367 days x 16 files, each slice log with 0 failed), accounting, hard-link tree | day jobs 633376, 635039, 635045, 635053 (`afterany`; the gate does the check, since cancelled tasks make `afterok` impossible) |
| 635062 | merge into `merge_tree/merged/full_2025.aeic-store` | gate |
| 635063, 635064 | store check, components (totals) | merge |
| 635065-67 | gridding wide, small, narrow (12 months each, 2 at a time per class, 250 GB, exclusive node) | store check, components, class filters |
| 635068, 635069 | GCHP case files (4 cases), class-flux files (3) | all three gridding arrays |
| 635070 | final checks: store count, partition check, case verification (NOx closure, calendar-month periods), NOx totals | both case arrays |

New or changed scripts in `scripts/`: `gate_days.py`, `after_days.sbatch`, `finish_checks.sbatch`, `queue_next_steps.sh`;
`grid_launch.sh` gained `GRID_PARTITION`, `GRID_RESERVATION`, `GRID_DEPENDENCY`. The AEIC working tree must stay clean
at the tag until the gridding jobs have started (they record its commit).

## Results of the full year and of the first gridding attempt (2026-10-20)

- Gate (job 635061): 367 days, 5,872 files, 34,021,689 flights flown, 0 failed, 0 without a model. Merge 22 min.
- Store check (635063): 34,021,689 trajectories, 0 NaN, 0 negative, 0 files with errors. Trajectory fuel 239.9917 Tg and
  trajectory CO2 756.3845 Tg are identical to v2026.10.12 (the trajectories did not change); total CO2 including LTO, APU and GSE is
  983.753 Tg against 956.184 Tg (+2.88%, as the test day predicted).
- **Git commit in the store:** 5,712 of the 5,872 files record an empty commit, 160 record `aa1d6a0` (the 10 days I submitted by hand
  from the AEIC checkout). The store takes the commit from the working directory of the job, and the day jobs were launched
  from the run directory, which is not a git checkout (v2026.10.12 launched most days from the checkout). The commit is proven
  independently: `run_day.sbatch` refuses to start unless AEIC is at the tag with a clean tree, and all 372 day-job logs
  (`logs/res_*`, `nq_*`, `edge_*`, `redo_*`, `res2_*`, `res3_*`) print `AEIC aa1d6a0f99...` at the start.
- **Gridding, first attempt (16 map processes per job, 250 GB): failed.** 5 of the first 6 jobs were killed at the memory cap
  (MaxRSS ~260 GB). Memory grew to 130 GB within 3 minutes of a job starting (about 16 GB per process) and reached the cap
  near the end; the map step itself takes only 6-11 min per slice, and a job spends most of its hour opening the 5,872-file
  store. v2026.10.12 passed at 250 GB by a margin of about 11 GB. Cancelled (jobs 635065-635070); the one finished file
  (`wide-2025-02.nc`) is in `grids_class/superseded_16_slices/`. Requeued as jobs 635426-635431 with `GRID_CPUS=8` map
  processes per job (`scripts/requeue_gridding.sh`).

## Outcome and hand-over to GCHP (2026-10-20)

- **Gridding, second attempt** (8 map processes per job, jobs 635426-635431): 36 monthly grids, the 4 + 3 GCHP case sets (84 files), and
  the final checks all passed (job 635588): store count and no NaN; partition (grids = store trajectory + LTO CO2, 1.06e-6); case files
  against the class grids 1.4e-9, NOx closure and calendar-month periods. Checksums of the 84 case files: `case_files.sha256`.
- **Inventory** (`nox_totals_check.py`): all aviation 254.7 Tg fuel (255.9 Tg with APU and ground equipment, which are not gridded) and
  4.383 Tg NO2 of NOx (wide-body 2.237, narrow-body 2.025, regional 0.121); against v2026.10.12 NOx +3.5%, fuel +2.9%; against the stock
  AEIC2019 inventory NOx 0.973 and fuel 0.987. NOx: 2.1% in the surface layer, 4.3% below 931 m, 69.9% between 8 and 13.5 km, 91.3% north
  of the equator.
- **GCHP:** the spin-up (Jul-Dec 2024) keeps v2026.10.12; the 2025 analysis year reads these files. Each run's `AviationEmis/` links
  were repointed per calendar month, 01-10 (and 11 for Av_All) on 2026-10-20, the rest automatically when the run's Dec 2024 job
  completes; Nov and Dec 2025 jobs are held until a guard job verifies all 12 links and checksums. Scripts, logs and tests:
  `/net/fs12/d1/shreya22/Paper_1/swap_2026-10-20/`; per-run notice `INVENTORY_CHANGE_2026-10-20.md` in each run directory.

## Steps (see scripts/README.md)

1. `build_models.sh`; the models must hash identically to v2026.10.12's (performance code unchanged).
2. Preflight, manifest, `launch.sh test`. Review 2025-01-15 against v2026.10.12's test day with
   `scripts/compare_test_days.py` (`REF_DIR=../v2026.10.12`). It is read-only analysis, added
   after the tag. It is committed to AEIC only once the run and gridding are done, because the run
   scripts require AEIC HEAD to be exactly at the tag.
3. Full year, merge, store checks, class grids, case files.
4. `verify_cases_species_mass.py` (NOx closure, calendar-month periods) and `nox_totals_check.py`.
