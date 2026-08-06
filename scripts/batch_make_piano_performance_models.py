"""Batch-convert PIANO cruise/climb/descent text exports -> AEIC `piano`-type
performance model TOMLs, via `AEIC.parsers.piano_reader.PianoData` and
`AEIC.commands.make_piano_performance_model.write_piano_performance_toml`
(the same machinery behind the `make-piano-performance-model` CLI command --
this script is the batch driver over piano_step8_instructions_v2.csv that
command doesn't have on its own).

For each row where all three PIANO txt files (cruise, climb, descent) are
present, builds a `piano`-type TOML and writes it to OUT_DIR. Skips rows
whose TOML already exists and is newer than all three input files (safe to
re-run as files come in); pass --force to regenerate everything regardless.

Notes on inputs, from auditing piano_step8_instructions_v2.csv against what
PianoData.load/make-piano-performance-model actually require:

- cas_low_kts: none of these PIANO climb file headers separate low/high CAS
  in a way the CSV captures (op_climb_cas_kts is the single high-speed
  value), and only ~10/107 climb files have a parseable header at all. Fixed
  at 250 kt for every aircraft here (CAS_LOW_KTS below) -- the same value
  the old, less strict converter silently defaulted to.
- aircraft_class: AEIC's AircraftClass enum is {wide, narrow, small,
  freight} -- no "regional" -- so the CSV's existing "small" values are used
  as-is. 4 rows have a blank aircraft_class; MANUAL_AIRCRAFT_CLASS below
  fills those in from the aircraft type (ARJ-21-700, Fokker F70, and the
  Dornier 328JET are regional/small aircraft; the Airbus A310-200 is a
  widebody twin-aisle, not small).
- engine UID: prefers nvpm_uid (has both gaseous + nvPM data) over edb_uid
  (often gaseous-only, causing nvPM lookups to fail at runtime), same as the
  previous (non-AEIC) converter. Rows falling back to edb_uid (no nvpm_uid
  available) are converted with EDBEntry.get_engine(strict=False), so a
  missing nvPM sheet entry there zero-fills nvPM fields instead of raising
  -- nvpm_uid rows stay strict, since a mismatch there is a real error.

Usage
-----
    uv run python scripts/batch_make_piano_performance_models.py [--force]

Adjust the path constants below if inputs live elsewhere.
"""

from __future__ import annotations

import argparse
import traceback
from pathlib import Path

import pandas as pd

from AEIC.commands.make_performance_model import lto_from_edb
from AEIC.commands.make_piano_performance_model import write_piano_performance_toml
from AEIC.parsers.piano_reader import PianoData
from AEIC.performance.models.base import LTOPerformanceInput

# ---------------------------------------------------------------------------
# Paths -- adjust if inputs live elsewhere.
# ---------------------------------------------------------------------------
INSTRUCTIONS = Path('/net/d16/data/shreya22/files/piano_step8_instructions_v2.csv')
PIANO_DIR = Path('/net/d16/data/shreya22/All_Planes/data/performance')
OUT_DIR = Path('/home/shreya22/Paper_1_data/performance_models')
EDB_FILE = Path('/home/shreya22/edb-emissions-databank_v32__web_.xlsx')

THRUST_FRACTIONS = (0.07, 0.30, 0.85, 1.0)  # idle, approach, climb, takeoff

# Every PIANO climb file's header CAS/Mach schedule caps low-speed CAS the
# same way across this fleet -- see module docstring.
CAS_LOW_KTS = 250.0

# aircraft_class for the 4 rows with a blank value in the instructions CSV.
MANUAL_AIRCRAFT_CLASS = {
    'ARJ-21-700_v08': 'small',
    'Fokker_F70_basic': 'small',
    'Dornier_328JET': 'small',
    'Airbus_A310-200': 'wide',
}


def _resolve(stem: str, suffix: str) -> Path:
    """Locate a PIANO txt export, tolerating a missing .txt extension."""
    d = PIANO_DIR / stem
    p = d / f'{stem}_{suffix}'
    if p.exists():
        return p
    p_txt = d / f'{stem}_{suffix}.txt'
    if p_txt.exists():
        return p_txt
    return p  # return the expected path so a missing-file message is clear


def _float_or_none(val) -> float | None:
    v = str(val).strip()
    if not v or v.lower() in ('nan', 'none'):
        return None
    return float(v)


def _parse_climb_masses(raw: str) -> list[float] | None:
    """ "132,000 / 146,000 / 157,000 / 170,000" -> [132000.0, 146000.0, ...]"""
    raw = str(raw).strip()
    if not raw or raw.lower() == 'nan':
        return None
    return [float(m.strip().replace(',', '')) for m in raw.split('/')]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        '--force',
        action='store_true',
        help='Regenerate every TOML regardless of existing output/mtimes.',
    )
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    instructions = pd.read_csv(INSTRUCTIONS, dtype=str)

    n_ok = n_skipped = n_missing = n_failed = 0

    for _, row in instructions.iterrows():
        stem = str(row['save_as']).strip()
        out_toml = OUT_DIR / f'{stem}.toml'

        cruise_file = _resolve(stem, 'cruise')
        climb_file = _resolve(stem, 'climb')
        descent_file = _resolve(stem, 'descent')

        missing_files = [
            f
            for f in (cruise_file, climb_file, descent_file)
            if not f.exists() or f.stat().st_size == 0
        ]
        if missing_files:
            print(f'[MISSING] {stem}')
            for f in missing_files:
                print(f'            x {f.name}')
            n_missing += 1
            continue

        if not args.force and out_toml.exists():
            toml_mtime = out_toml.stat().st_mtime
            piano_mtime = max(
                f.stat().st_mtime for f in (cruise_file, climb_file, descent_file)
            )
            if toml_mtime >= piano_mtime:
                print(f'[SKIP]    {stem}.toml is up-to-date')
                n_skipped += 1
                continue

        engine_uid = str(row.get('nvpm_uid', '')).strip()
        has_nvpm_uid = bool(engine_uid) and engine_uid.lower() != 'nan'
        if not has_nvpm_uid:
            engine_uid = str(row.get('edb_uid', '')).strip()
        if not engine_uid or engine_uid.lower() == 'nan':
            print(f'[NO UID]  {stem} -- edb_uid/nvpm_uid missing, skipping')
            n_missing += 1
            continue

        aircraft_class = str(row.get('aircraft_class', '')).strip()
        if not aircraft_class or aircraft_class.lower() == 'nan':
            aircraft_class = MANUAL_AIRCRAFT_CLASS.get(stem, '')
        if not aircraft_class:
            print(f'[NO CLASS] {stem} -- aircraft_class missing, skipping')
            n_missing += 1
            continue

        print(f'[CONVERT] {stem}')
        try:
            # A UID sourced from nvpm_uid is guaranteed to have nvPM sheet
            # coverage (that's why it's in that column), so any mismatch
            # there is worth failing loudly on. Falling back to edb_uid has
            # no such guarantee -- edb_uid is often gaseous-only -- so those
            # rows get zero-filled nvPM data instead of a hard failure.
            lto = lto_from_edb(
                str(EDB_FILE), engine_uid, THRUST_FRACTIONS, strict=has_nvpm_uid
            )
            lto_dump = LTOPerformanceInput.from_internal(lto).model_dump()

            piano_data = PianoData.load(
                str(cruise_file),
                str(climb_file),
                str(descent_file),
                climb_masses_lb=_parse_climb_masses(
                    row.get('climb_start_masses_lb', '')
                ),
                design_mach=_float_or_none(row.get('op_cruise_mach', '')),
                cas_high_kts=_float_or_none(row.get('op_climb_cas_kts', '')),
                cas_low_kts=CAS_LOW_KTS,
                climb_mach=_float_or_none(row.get('op_climb_mach', '')),
                descent_mach=_float_or_none(row.get('op_descent_mach', '')),
                descent_cas_kts=_float_or_none(row.get('op_descent_cas_kts', '')),
            )

            cols = ['fl', 'mass', 'tas', 'rocd', 'fuel_flow']
            write_piano_performance_toml(
                str(out_toml),
                aircraft_name=str(row['piano_plane_file']),
                aircraft_class=aircraft_class,
                isa_offset=0,
                maximum_altitude_ft=piano_data.maximum_altitude_ft,
                maximum_payload_kg=int(float(row['max_payload_kg'])),
                maximum_payload_source='piano_step8_instructions_v2.csv max_payload_kg',
                number_of_engines=int(row['n_engines']),
                apu_name=None,
                lto_dump=lto_dump,
                speeds_dump=piano_data.speeds.model_dump(),
                climb_flight_performance=dict(
                    cols=cols, data=piano_data.flight_performance.climb
                ),
                cruise_flight_performance=dict(
                    cols=cols, data=piano_data.flight_performance.cruise
                ),
                descent_flight_performance=dict(
                    cols=cols, data=piano_data.flight_performance.descent
                ),
            )
            print(f'          done {out_toml.name}')
            n_ok += 1
        except Exception as exc:
            print(f'          FAILED: {exc}')
            traceback.print_exc()
            n_failed += 1

    print()
    print('=' * 60)
    print(f'  Converted : {n_ok}')
    print(f'  Skipped   : {n_skipped}  (TOML already up-to-date)')
    print(f'  Missing   : {n_missing}  (input files/uid/class not available)')
    print(f'  Failed    : {n_failed}')
    print(f'  TOMLs in  : {OUT_DIR}')


if __name__ == '__main__':
    main()
